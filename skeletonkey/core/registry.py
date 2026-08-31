"""ToolRegistry - the dynamic part of the dynamic toolset.

Responsibilities:
  1. hold manifests + handlers (builtin, drop-in dirs, entry points, skills, remotes)
  2. gate them against the CapabilityProfile (don't advertise `pwsh` on a box
     without pwsh; don't advertise gnu-patch when only python fallback exists)
  3. collapse *capabilities* to one winning provider, ranked by declared
     priority then by observed success (the adaptive bit)
  4. answer `search()` for the "I need a tool that does X" loop
  5. emit an advertisement plan that respects a token budget

`advertise()` returns an `AdSnapshot` with `added/removed` so callers (MCP) can
raise tools/list_changed only when the set truly changed.
"""

from __future__ import annotations

import importlib.util
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .errors import E, SkeletonKeyError
from .manifest import RISK_ORDER, Requirement, ToolManifest
from .profile import CapabilityProfile
from .util import compact_json, new_run_id, short_hash


@dataclass
class ProviderStats:
    calls: int = 0
    ok: int = 0
    fail: int = 0
    total_ms: int = 0
    last_ms: int = 0
    last_error: str | None = None
    last_seen: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.ok / self.calls) if self.calls else 0.5

    @property
    def mean_ms(self) -> float:
        return (self.total_ms / self.calls) if self.calls else 0.0

    def score(self) -> float:
        """Higher is better. Weighted so reliability dominates latency."""
        import math

        if self.calls < 3:
            return 0.0  # not enough evidence: trust declared priority
        reliability = self.success_rate
        speed = math.exp(-min(self.mean_ms, 30_000) / 12_000)
        evidence = min(1.0, self.calls / 25)
        return 0.75 * reliability + 0.15 * speed + 0.10 * evidence

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "ok": self.ok, "fail": self.fail,
                "success_rate": round(self.success_rate, 3), "mean_ms": round(self.mean_ms, 1),
                **({"last_error": self.last_error} if self.last_error else {})}


@dataclass
class AdGate:
    """Why a tool is/isn't advertised right now."""

    available: bool
    reasons: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"available": self.available}
        if self.unmet:
            d["unmet"] = self.unmet
        if self.reasons:
            d["reasons"] = self.reasons
        return d


@dataclass
class AdSnapshot:
    tools: list[ToolManifest]
    gates: dict[str, AdGate]
    tokens: int
    digest: str
    selected: dict[str, str] = field(default_factory=dict)   # capability -> winning tool id
    at: float = field(default_factory=time.time)

    @property
    def names(self) -> list[str]:
        return [t.id for t in self.tools]

    def diff(self, other: AdSnapshot) -> dict[str, list[str]]:
        a, b = set(self.names), set(other.names)
        return {"added": sorted(b - a), "removed": sorted(a - b)}


@dataclass
class Registry:
    profile: CapabilityProfile | None = None
    _tools: dict[str, ToolManifest] = field(default_factory=dict, repr=False)
    _stats: dict[str, ProviderStats] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _generation: int = field(default=0, repr=False)
    _listeners: list[Callable[[str, ToolManifest | None], None]] = field(default_factory=list, repr=False)
    _sources: dict[str, str] = field(default_factory=dict, repr=False)
    loaded_dirs: list[str] = field(default_factory=list, repr=False)
    load_errors: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.load_errors = []

    # ------------------------------------------------------------- registration
    def register(self, manifest: ToolManifest, handler: Callable[..., Any] | None = None, *,
                 replace: bool = False) -> ToolManifest:
        with self._lock:
            existing = self._tools.get(manifest.id)
            if existing and not replace:
                raise SkeletonKeyError(
                    E.CONFLICT, f"tool {manifest.id} already registered (source={existing.source})",
                    details={"existing_source": existing.source, "new_source": manifest.source,
                             "hint": "pass replace=True or use a different id"},
                )
            if handler is not None:
                manifest.handler = handler
            if manifest.handler is None:
                raise SkeletonKeyError(E.BAD_ARGS, f"tool {manifest.id} has no handler",
                                       details={"id": manifest.id})
            self._tools[manifest.id] = manifest
            self._sources[manifest.id] = manifest.source_path or manifest.source
            self._generation += 1
            self._notify("added", manifest)
            return manifest

    def unregister(self, tool_id: str) -> ToolManifest | None:
        with self._lock:
            man = self._tools.pop(tool_id, None)
            if man:
                self._generation += 1
                self._notify("removed", man)
            return man

    def get(self, tool_id: str) -> ToolManifest:
        try:
            return self._tools[tool_id]
        except KeyError:
            near = self.suggest(tool_id)
            raise SkeletonKeyError(
                E.UNKNOWN_TOOL, f"unknown tool {tool_id!r}",
                details={"requested": tool_id, "suggested": near,
                         "count": len(self._tools)},
                next_actions=[{"tool": "registry.search", "args": {"query": tool_id, "limit": 5}}]
                if not near else [{"tool": near[0]["id"], "note": "close match"}],
            ) from None

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def all(self) -> list[ToolManifest]:
        return [self._tools[k] for k in sorted(self._tools)]

    def by_group(self, group: str) -> list[ToolManifest]:
        return [t for t in self.all() if t.group == group]

    def by_capability(self, capability: str, *, prefix: bool = True) -> list[ToolManifest]:
        out = []
        for t in self.all():
            cands = [t.capability, *t.provides]
            for c in cands:
                if c == capability or (prefix and c.startswith(capability.rstrip(".") + ".")):
                    out.append(t)
                    break
        return out

    def suggest(self, name: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """Typo/did-you-mean helper for agents that invent tool names."""
        scored = []
        target = name.lower().replace("/", ".")
        for tid in self._tools:
            score = _similar(target, tid.lower())
            if score > 0.34:
                scored.append((score, {"id": tid, "why": "name similarity", "score": round(score, 2)}))
        scored.sort(key=lambda x: -x[0])
        return [s[1] for s in scored[:limit]]

    # ----------------------------------------------------------------- listeners
    def on_change(self, cb: Callable[[str, ToolManifest | None], None]) -> None:
        self._listeners.append(cb)

    def _notify(self, event: str, man: ToolManifest | None) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, man)
            except Exception:
                pass

    @property
    def generation(self) -> int:
        return self._generation

    # -------------------------------------------------------------------- stats
    def record(self, tool_id: str, *, ok: bool, duration_ms: int, error_code: str | None = None) -> None:
        with self._lock:
            s = self._stats.setdefault(tool_id, ProviderStats())
            s.calls += 1
            s.total_ms += int(duration_ms)
            s.last_ms = int(duration_ms)
            s.last_seen = time.time()
            if ok:
                s.ok += 1
                s.last_error = None
            else:
                s.fail += 1
                s.last_error = error_code

    def stats(self, tool_id: str | None = None) -> dict[str, Any]:
        if tool_id:
            s = self._stats.get(tool_id)
            # keyed by id either way: a filtered response must not change shape, or the
            # caller loses the tool name it asked about
            return {tool_id: s.to_dict() if s else {"calls": 0}}
        return {k: v.to_dict() for k, v in sorted(self._stats.items())}

    # ------------------------------------------------------------------- gating
    def gate(self, man: ToolManifest, *, read_only: bool = False,
             disabled: Iterable[str] = ()) -> AdGate:
        disabled_set = set(disabled)
        reasons: list[str] = []
        if man.id in disabled_set:
            return AdGate(False, ["disabled by configuration"])
        if not man.advertised:
            reasons.append(man.hidden_reason or "internal tool (not advertised)")
        if read_only and man.is_mutating:
            return AdGate(False, ["policy.read_only is on; mutating tool withheld"], reasons)
        if man.platforms and self.profile and self.profile.os not in man.platforms:
            return AdGate(False, [f"supports {man.platforms}, host is {self.profile.os}"], reasons)
        if self.profile and man.requirements:
            ok, unmet = self.profile.meets(man.requirements)
            if not ok:
                return AdGate(False, reasons, unmet)
        if self.profile and man.require_any:
            ok, notes = self.profile.meets_any(man.require_any)
            if not ok:
                return AdGate(False, reasons, ["none of: " + ", ".join(notes)])
        return AdGate(True, reasons)

    def rank_providers(self, capability: str) -> list[tuple[float, ToolManifest]]:
        cands = self.by_capability(capability)
        usable = [(m, g) for m in cands for g in [self.gate(m)] if g.available]
        scored: list[tuple[float, ToolManifest]] = []
        for man, _g in usable:
            stat = self._stats.get(man.id)
            scored.append((float(RISK_ORDER.get(man.risk, 0)) * -2.0 + man.priority
                           + (stat.score() * 40 if stat else 0.0), man))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return scored

    # -------------------------------------------------------------- advertisement
    def advertise(self, *, profile: CapabilityProfile | None = None, read_only: bool = False,
                  disabled: Iterable[str] = (), dedupe_capability: bool = True,
                  token_budget: int | None = None, include_internal: bool = False) -> AdSnapshot:
        """The tool list a host actually sees right now.

        dedupe_capability keeps one provider per capability (highest rank), which
        is how "adaptive" shows up to the model: it never sees 4 file-search tools.
        """
        saved_profile = self.profile
        if profile is not None:
            self.profile = profile
        try:
            tools = self.all()
            gates: dict[str, AdGate] = {}
            survivors: list[ToolManifest] = []
            for man in tools:
                g = self.gate(man, read_only=read_only, disabled=disabled)
                gates[man.id] = g
                if not g.available:
                    continue
                if not include_internal and not man.advertised:
                    continue
                survivors.append(man)

            selected: dict[str, str] = {}
            chosen: list[ToolManifest] = []
            by_cap: dict[str, list[tuple[float, ToolManifest]]] = {}
            for man in survivors:
                cap = man.capability or man.id
                by_cap.setdefault(cap, []).append((self._score(man), man))
            for cap, lst in sorted(by_cap.items()):
                lst.sort(key=lambda x: (-x[0], x[1].id))
                # A capability with several *providers* (rg/grep/python search backends,
                # pwsh/powershell shells) collapses to the winner. Several tools that
                # merely share a capability namespace (jobs/wait/kill) are not
                # interchangeable, so they all stay advertised.
                providers = {m.provider for _s, m in lst} - {None}
                is_provider_race = len(lst) > 1 and len(providers) > 1
                if dedupe_capability and is_provider_race:
                    win = lst[0][1]
                    selected[cap] = win.id
                    chosen.append(win)
                else:
                    chosen.extend(m for _s, m in lst)
            chosen.sort(key=lambda m: (m.group, m.id))
            tokens = sum(m.tokens_estimate() for m in chosen)
            dropped: list[ToolManifest] = []
            if token_budget:
                kept: list[ToolManifest] = []
                running = 0
                for man in sorted(chosen, key=lambda m: -self._score(m)):
                    cost = man.tokens_estimate()
                    if kept and running + cost > token_budget:
                        dropped.append(man)
                        continue
                    kept.append(man)
                    running += cost
                chosen = sorted(kept, key=lambda m: (m.group, m.id))
                tokens = running

            digest = short_hash(compact_json([
                [m.id, m.version, sorted(m.tags), gates.get(m.id, AdGate(True)).available] for m in chosen
            ] + ([{"dropped": [m.id for m in dropped]}] if dropped else [])), 16)
            return AdSnapshot(tools=chosen, gates=gates, tokens=tokens, digest=digest, selected=selected)
        finally:
            self.profile = saved_profile

    def _score(self, man: ToolManifest) -> float:
        stat = self._stats.get(man.id)
        return float(RISK_ORDER.get(man.risk, 0)) * -2.0 + man.priority + (stat.score() * 40 if stat else 0.0)

    # ------------------------------------------------------------------- search
    def search(self, query: str, *, limit: int = 8, include_gated: bool = False,
               group: str | None = None, max_risk: str | None = None,
               dialect: str | None = None) -> list[dict[str, Any]]:
        """Deterministic lexical ranking. (Phase 5 adds semantic/hybrid recall.)"""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        max_idx = RISK_ORDER.get(max_risk, 99) if max_risk else 99
        out: list[tuple[float, dict[str, Any]]] = []
        for man in self.all():
            g = self.gate(man)
            if not g.available and not include_gated:
                continue
            if group and man.group != group:
                continue
            if RISK_ORDER.get(man.risk, 0) > max_idx:
                continue
            if dialect and dialect not in ([*man.tags, man.provider or "", man.id]):
                continue
            score = _score_match(q_tokens, man)
            if score <= 0:
                continue
            out.append((score, {
                "id": man.id, "title": man.title, "capability": man.capability,
                "risk": man.risk, "group": man.group, "score": round(score, 3),
                "description": man.description.strip().split("\n")[0][:200],
                "available": g.available, **({"gated": g.to_dict()} if not g.available else {}),
            }))
        out.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [d for _s, d in out[:limit]]

    # ---------------------------------------------------------------- drop-ins
    def load_dir(self, path: str, *, source: str = "dropin", replace: bool = False) -> dict[str, Any]:
        """Import `*.py` files from a directory; each may expose TOOL(S)/register().

        Contract (see docs/PLUGIN-CONTRACT.md):
          module.TOOL: ToolManifest            - single tool, handler set
          module.TOOLS: list[ToolManifest]     - several
          module.register(reg)                 - imperative
        """
        report = {"dir": path, "files": 0, "added": [], "skipped": [], "errors": []}
        if not os.path.isdir(path):
            report["skipped"].append("missing-dir")
            return report
        for entry in sorted(os.listdir(path)):
            if not entry.endswith(".py") or entry.startswith(("_", ".")):
                continue
            fpath = os.path.join(path, entry)
            try:
                mod = _import_from_path(fpath)
            except Exception as exc:
                report["errors"].append({"file": entry, "error": f"{type(exc).__name__}: {exc}"})
                self.load_errors.append({"file": fpath, "stage": "import", "error": str(exc)[:400]})
                continue
            report["files"] += 1
            found = 0
            if hasattr(mod, "register") and callable(mod.register):
                try:
                    mod.register(self)
                    found += 1
                except Exception as exc:
                    report["errors"].append({"file": entry, "error": f"register(): {exc}"})
            for man in _manifests_of(mod, source=source, source_path=fpath):
                try:
                    self.register(man, replace=replace)
                    report["added"].append(man.id)
                    found += 1
                except SkeletonKeyError as exc:
                    report["skipped"].append({"id": man.id, "reason": exc.err.code, "message": str(exc)})
            if not found:
                report["skipped"].append({"file": entry, "reason": "no TOOL/TOOLS/register() found"})
        self.loaded_dirs.append(path)
        return report

    def load_entry_points(self, group: str = "skeletonkey.tools") -> list[str]:
        added: list[str] = []
        try:
            from importlib.metadata import entry_points

            # requires-python >= 3.11, so the group= form always exists; a broken
            # distribution's metadata is the only thing that can raise here.
            eps = entry_points(group=group)
        except Exception:
            return added
        for ep in eps or []:
            try:
                obj = ep.load()
            except Exception as exc:
                self.load_errors.append({"entry_point": ep.name, "stage": "load", "error": str(exc)[:400]})
                continue
            for man in _manifests_of_obj(obj, source=f"entrypoint:{ep.name}"):
                try:
                    self.register(man, replace=False)
                    added.append(man.id)
                except SkeletonKeyError as exc:
                    self.load_errors.append({"entry_point": ep.name, "stage": "register", "error": str(exc)[:200]})
        return added

    # ------------------------------------------------------------------- misc
    def overview(self) -> dict[str, Any]:
        groups: dict[str, dict[str, int]] = {}
        for t in self.all():
            g = groups.setdefault(t.group, {"total": 0, "mutating": 0})
            g["total"] += 1
            if t.is_mutating:
                g["mutating"] += 1
        return {
            "tools": len(self._tools), "generation": self._generation,
            "groups": groups, "loaded_dirs": self.loaded_dirs,
            "load_errors": self.load_errors,
            "capabilities": sorted({t.capability for t in self._all_values()}),
        }

    def _all_values(self) -> list[ToolManifest]:
        return list(self._tools.values())

    def snapshot_json(self) -> str:
        return compact_json([t.to_dict(include_schema=False) for t in self.all()])

    def describe(self, tool_id: str) -> dict[str, Any]:
        man = self.get(tool_id)
        gate = self.gate(man)
        d = man.to_dict(include_schema=True, include_handler=True)
        d["availability"] = gate.to_dict()
        d["stats"] = self.stats(man.id)
        d["typical"] = {"latency_ms": man.typical_latency_ms, "output_bytes": man.typical_output_bytes,
                        "timeout_s": man.timeout_s, "reversible": man.reversible}
        if man.examples:
            d["examples"] = man.examples
        if man.anti_patterns:
            d["anti_patterns"] = man.anti_patterns
        return d


# --------------------------------------------------------------------- helpers


def _manifests_of(mod: Any, *, source: str, source_path: str) -> list[ToolManifest]:
    out: list[ToolManifest] = []
    for attr in ("TOOL", "TOOLS", "MANIFESTS", "MANIFEST"):
        obj = getattr(mod, attr, None)
        if obj is None:
            continue
        out.extend(_manifests_of_obj(obj, source=source, source_path=source_path))
    return out


def _manifests_of_obj(obj: Any, *, source: str, source_path: str | None = None) -> list[ToolManifest]:
    if isinstance(obj, ToolManifest):
        return [obj]
    if isinstance(obj, dict):
        return [ToolManifest.from_dict(obj, source=source, source_path=source_path)]
    items: list[ToolManifest] = []
    if isinstance(obj, (list, tuple)):
        for item in obj:
            items.extend(_manifests_of_obj(item, source=source, source_path=source_path))
        return items
    if callable(obj):
        produced = obj()
        if isinstance(produced, (list, tuple)):
            for item in produced:
                items.extend(_manifests_of_obj(item, source=source, source_path=source_path))
        elif isinstance(produced, ToolManifest):
            items.append(produced)
    return items


def _import_from_path(path: str) -> Any:
    name = "sk_dropin_" + re.sub(r"\W+", "_", os.path.splitext(os.path.basename(path))[0]) + \
        "_" + short_hash(path, 6)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "by", "is", "are", "be", "this", "that", "it", "its", "you", "your", "we", "our", "from", "at", "as", "if", "then", "else", "when", "how", "what", "which", "can", "could", "would", "should", "do", "does", "not", "no", "yes", "all", "any"}


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*|\d+", (text or "").lower())
    toks: list[str] = []
    for r in raw:
        for part in re.split(r"[_\-./]+", r):
            if part and part not in _STOP and len(part) > 1:
                toks.append(part)
    return toks


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _hit(tok: str, words: list[str]) -> float:
    """Word-boundary matching only: exact == 1.0, prefix (file->filesystem) == 0.5.

    Substring scoring ("file" inside "pro*file*", "name" inside "*name*space")
    promoted capability-noise tools above the tools that actually do the job, so
    we refuse to match inside a word at all.
    """
    best = 0.0
    for w in words:
        if w == tok:
            return 1.0
        if len(tok) >= 3 and w.startswith(tok):
            best = max(best, 0.5)
    return best


def _score_match(q: list[str], man: ToolManifest) -> float:
    if not q:
        return 0.0
    name_words = re.split(r"[._\-/]", man.id.lower())
    desc_words = _words(man.description) + _words(man.title)
    tag_words = [t.lower() for t in man.tags]
    cap_words = _words(" ".join([man.capability, *man.provides]))
    toks = set(q)
    score = 0.0
    covered = 0
    for tok in toks:
        h_name = _hit(tok, name_words)
        h_cap = _hit(tok, cap_words)
        h_tag = _hit(tok, tag_words)
        h_desc = _hit(tok, desc_words)
        total = 4.0 * h_name + 2.2 * h_cap + 1.6 * h_tag + 1.0 * h_desc
        if total > 0:
            covered += 1
        score += total
    # A tool matching every word of the query should beat one matching one word,
    # even if that one word is a high-weight field.
    score *= 0.55 + 0.45 * (covered / max(1, len(toks)))
    # id-phrase bonus: "fs.search" typed verbatim should beat everything
    joined = " ".join(set(q))
    if joined and joined in man.id.lower().replace(".", " ").replace("_", " "):
        score *= 1.25
    return score / (1.0 + 0.015 * len(q))


def _similar(a: str, b: str) -> float:
    """Cheap normalized similarity for did-you-mean."""
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    # bigram dice coefficient
    def bigrams(s: str) -> set[str]:
        return {s[i:i + 2] for i in range(len(s) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


def parse_requirements(raw: list[dict[str, Any]] | list[str]) -> list[Requirement]:
    out: list[Requirement] = []
    for item in raw:
        if isinstance(item, str):
            kind = "binary"
            name = item
            if ":" in item:
                kind, name = item.split(":", 1)
            out.append(Requirement(kind, name))
        else:
            out.append(Requirement(**item))
    return out


def new_id(prefix: str = "call") -> str:
    return f"{prefix}_{new_run_id()}"
