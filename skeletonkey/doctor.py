"""P6 `sk doctor` — one stable JSON blob an operator can paste.

Schema (v1) is documented in README.md under "sk doctor". Content rules:
paths, counts, receipts, and *redacted* config facts only — never environment
values, credential material, or file contents. `--fix` (CLI) performs only the
two safe moves PLAN §6 allows (create state/spill dirs, refresh the capability
probe); integrity problems are reported, never "fixed" by hand.

Keys stay stable across minor versions; additive extension is allowed only
under a new `schema` number.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any

SCHEMA_VERSION = 1


def collect(tk: Any) -> dict[str, Any]:
    """The whole diagnostic in one dict. Never raises for a missing subsystem —
    a broken piece is a reported fact, not a crashed doctor."""
    cfg = tk.config
    reg = tk.engine.registry
    profile = tk.profile
    snap = tk.engine.advertise()

    # what the host sees vs what exists, with the gate that explains each gap
    all_ids = sorted(m.id for m in reg.all())
    advertised = set(snap.names)
    gates: dict[str, Any] = {}
    for tid in sorted(set(all_ids) - advertised):
        man = reg.get(tid)
        g = snap.gates.get(tid) or reg.gate(man)
        # `available` is about calling; a manifest-level `advertised = false`
        # keeps that True while still explaining the absence (the real receipt).
        gates[tid] = {"available": bool(g.available),
                      "advertised": bool(man.advertised),
                      "hidden_reason": man.hidden_reason or "",
                      "reasons": list(getattr(g, "reasons", [])),
                      "unmet": list(getattr(g, "unmet", []))}

    ledger: dict[str, Any] = {"enabled": bool(cfg.state.ledger)}
    if tk.ledger is not None:
        ledger["path"] = str(tk.ledger.path)
        if cfg.state.ledger:
            try:
                verified = tk.ledger.verify()
                ledger.update(verified)
                ledger["stats"] = tk.ledger.stats()
            except Exception as exc:
                ledger["verify_error"] = f"{type(exc).__name__}: {exc}"
    else:
        ledger["path"] = None

    journal = {"enabled": bool(cfg.state.journal), "root": str(tk.journal.root),
               "index_exists": os.path.exists(tk.journal.index_path)}

    spill = cfg.budget.spill_dir
    spill_info: dict[str, Any] = {"path": str(spill), "exists": os.path.isdir(spill)}
    if spill_info["exists"]:
        spill_info["writable"] = os.access(spill, os.W_OK)

    skills = tk.skills
    try:
        skill_count = len(skills.discover())
    except Exception as exc:
        skill_count = -1
        skills.errors = [*list(getattr(skills, "errors", [])), f"discover(): {exc}"]  # type: ignore[attr-defined]
    skills_info: dict[str, Any] = {
        "count": skill_count,
        "errors": list(getattr(skills, "errors", []) or []),
        "tool_errors": list(getattr(skills, "tool_errors", []) or []),
    }

    report = tk.build_report or {}
    remote_report = report.get("remote") or {}

    return {
        "schema": SCHEMA_VERSION,
        "version": _pkg_version(),
        "python": {"version": sys.version.split()[0], "platform": platform.platform()},
        "config": {
            "cwd": cfg.cwd,
            "workspace": cfg.workspace,
            "roots": list(cfg.roots),
            "source_files": list(cfg.source_files),
            "overrides_applied": list(cfg.overrides_applied),
            "warnings": list(cfg.warnings),
        },
        "profile": {
            "os": profile.os,
            "arch": profile.arch,
            "python_version": profile.python_version,
            "capabilities": sorted(profile.capabilities),
            "warnings": list(profile.warnings),
            "probed_at": profile.probed_at,
            "fingerprint": profile.fingerprint,
            "probe_receipts": [r.to_dict() for r in getattr(profile, "probe_receipt", [])],
        },
        "advertise": {
            "active_tier": reg.active_tier,
            "tier": snap.tier,
            "registered": len(all_ids),
            "advertised": len(advertised),
            "tokens": snap.tokens,
            "digest": snap.digest,
            "budget_drops": dict(snap.budget_drops),
        },
        "gates": gates,
        "registry": {
            "load_errors": list(reg.load_errors),
            "loaded_dirs": list(reg.loaded_dirs),
        },
        "skills": skills_info,
        "state": {
            "dir": cfg.state.dir,
            "journal": journal,
            "ledger": ledger,
            "spill": spill_info,
        },
        "remote": {
            "servers": remote_report.get("servers") or [],
            "registered": remote_report.get("registered") or [],
            "errors": remote_report.get("errors") or [],
        },
        "build": {
            "registered_after_load": report.get("registered_after_load"),
            "profile_source": report.get("profile_source"),
            "publish_store": report.get("publish_store"),
            "dropin_files": sum(r.get("files", 0) for r in (report.get("dropin") or [])),
            "dropin_errors": [e for r in (report.get("dropin") or []) for e in r.get("errors", [])],
            "entry_points": report.get("entry_points") or [],
            "builtin_registered": (report.get("builtin") or {}).get("registered", 0),
        },
    }


def safe_fixes(tk: Any) -> list[str]:
    """The two moves PLAN §6 allows. Returns what was actually done."""
    applied: list[str] = []
    cfg = tk.config
    for label, path in (("state.dir", cfg.state.dir), ("budget.spill_dir", cfg.budget.spill_dir)):
        if path and not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            applied.append(f"created {label} ({path})")
        elif not path:
            applied.append(f"{label} is empty; nothing to create")
    if os.path.isdir(cfg.budget.spill_dir) and not os.access(cfg.budget.spill_dir, os.W_OK):
        applied.append(f"budget.spill_dir is not writable ({cfg.budget.spill_dir})")
    return applied


def _pkg_version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - import is always there
        return "unknown"
