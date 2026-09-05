"""Auto-wire the SkeletonKey MCP server into MCP host applications (P6 onboarding).

The promise this module keeps: after `pip install skeletonkey-toolforge`, one command -
`sk wire` - makes every MCP host on this machine offer the toolkit's tools, with no
hand-editing of JSON and no guessing at paths. "Drop into any project" is a merge into
an existing config file, not a rewrite of it:

  * read the host's config, keep every byte we did not author (other servers, comments
    preserved by refusing to rewrite files we cannot parse strictly);
  * write only the `skeletonkey` entry, atomically, with a one-generation backup;
  * recognize our own previous entries so a second run is "already wired", not a
    duplicate, and so `--remove` takes out exactly what `sk wire` put in.

This is deliberately an **operator command, not an engine tool**: it writes outside the
filesystem sandbox by definition (host configs live in the user's home), so it must never
be callable through the same surface the agent drives. See ADR-0015.

Stdlib only, like the core (ADR-0001): no toolkit build, no mcp import, so `sk wire`
answers instantly even before the `[mcp]` extra is installed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SCHEMA", "HostSpec", "hosts", "status_rows", "wire"]

SCHEMA = "sk.wire/1"
ENTRY_NAME = "skeletonkey"
BACKUP_SUFFIX = ".sk-wire.bak"


# --------------------------------------------------------------------------- host catalogue
@dataclass(frozen=True)
class HostSpec:
    """One kind of MCP host application, as far as wiring is concerned.

    `user_paths` returns every plausible config location for this host on this machine
    (platform differences resolved from `env`); the first that exists is the detected
    install and the first parent that can be created is the write target. `project_paths`
    are workspace-relative files a host reads when opened on this project.
    `http` says whether the host accepts a remote (`url`) server at all - we refuse to
    write a stanza a host cannot load, because a silently-dead entry is worse than a
    refusal.
    """

    id: str
    title: str
    key: str                                    # the dict key holding the server map
    user_paths: Any                             # Callable[[Mapping[str, str]], list[str]]
    project_paths: tuple[str, ...] = ()
    http: bool = False
    jsonc: bool = False                         # file format tolerates comments (VS Code)
    notes: tuple[str, ...] = field(default=())


def _first_env(env: dict[str, str], *names: str, default: str = "") -> str:
    for n in names:
        v = env.get(n)
        if v:
            return v
    return default


def _home(env: dict[str, str]) -> str:
    return env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")


def hosts() -> list[HostSpec]:
    """The catalogue. Paths follow each vendor's documented locations; unknown platforms
    fall back to the XDG-style path so a new OS degrades to "not detected", not a crash."""

    def claude_desktop(env: dict[str, str]) -> list[str]:
        home = _home(env)
        if sys.platform == "darwin":
            return [os.path.join(home, "Library", "Application Support", "Claude",
                                 "claude_desktop_config.json")]
        if os.name == "nt":
            base = _first_env(env, "APPDATA", default=os.path.join(home, "AppData", "Roaming"))
            return [os.path.join(base, "Claude", "claude_desktop_config.json")]
        xdg = _first_env(env, "XDG_CONFIG_HOME", default=os.path.join(home, ".config"))
        return [os.path.join(xdg, "Claude", "claude_desktop_config.json")]

    def cursor(env: dict[str, str]) -> list[str]:
        home = _home(env)
        out = [os.path.join(home, ".cursor", "mcp.json")]      # documented global location
        if sys.platform == "darwin":
            out.append(os.path.join(home, "Library", "Application Support", "Cursor", "mcp.json"))
        elif os.name == "nt":
            base = _first_env(env, "APPDATA", default=os.path.join(home, "AppData", "Roaming"))
            out.append(os.path.join(base, "Cursor", "mcp.json"))
        else:
            xdg = _first_env(env, "XDG_CONFIG_HOME", default=os.path.join(home, ".config"))
            out.append(os.path.join(xdg, "Cursor", "mcp.json"))
        return out

    def vscode(env: dict[str, str]) -> list[str]:
        home = _home(env)
        bases = []
        if sys.platform == "darwin":
            bases += [os.path.join(home, "Library", "Application Support", d) for d in
                      ("Code", "Code - Insiders")]
        elif os.name == "nt":
            base = _first_env(env, "APPDATA", default=os.path.join(home, "AppData", "Roaming"))
            bases += [os.path.join(base, d) for d in ("Code", "Code - Insiders")]
        else:
            xdg = _first_env(env, "XDG_CONFIG_HOME", default=os.path.join(home, ".config"))
            bases += [os.path.join(xdg, d) for d in ("Code", "Code - Insiders", "VSCodium")]
        return [os.path.join(b, "User", "mcp.json") for b in bases]

    def claude_code(env: dict[str, str]) -> list[str]:
        return [os.path.join(_home(env), ".claude.json")]

    def windsurf(env: dict[str, str]) -> list[str]:
        return [os.path.join(_home(env), ".codeium", "windsurf", "mcp_config.json")]

    return [
        HostSpec(id="claude-desktop", title="Claude Desktop", key="mcpServers",
                 user_paths=claude_desktop, notes=("stdio only: this host has no remote-server support",)),
        HostSpec(id="claude-code", title="Claude Code", key="mcpServers",
                 user_paths=claude_code, project_paths=(".mcp.json",), http=True),
        HostSpec(id="cursor", title="Cursor", key="mcpServers",
                 user_paths=cursor, project_paths=(".cursor/mcp.json",), http=True),
        HostSpec(id="vscode", title="VS Code / VS Code Insiders", key="servers",
                 user_paths=vscode, project_paths=(".vscode/mcp.json",), http=True, jsonc=True),
        HostSpec(id="windsurf", title="Windsurf", key="mcpServers",
                 user_paths=windsurf),
    ]


HOSTS_BY_ID = {h.id: h for h in hosts()}


# --------------------------------------------------------------------------- config file IO
def _read_config(path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse a host config. Returns (data, info). Tolerant read: a JSONC file (comments /
    trailing commas) parses with `info["jsonc"] = True` so the caller can decide whether a
    rewrite is acceptable - a rewrite would drop the comments, and dropping user content
    silently is exactly what this module exists to avoid. Unreadable garbage is an error,
    never an empty dict we might overwrite."""
    info: dict[str, Any] = {"jsonc": False}
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        info["error"] = str(exc)
        return {}, info
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            info["error"] = "top level is not a JSON object"
            return {}, info
        return data, info
    except ValueError:
        pass
    stripped = _strip_jsonc(text)
    try:
        data = json.loads(stripped)
    except ValueError as exc:
        info["error"] = f"cannot parse (JSON with comments?): {exc}"
        return {}, info
    if not isinstance(data, dict):
        info["error"] = "top level is not a JSON object"
        return {}, info
    info["jsonc"] = True
    return data, info


def _strip_jsonc(text: str) -> str:
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    stripped = "".join(out)
    # trailing commas: ", }" / ", ]" -> " }" / " ]"
    cleaned: list[str] = []
    for ch in stripped:
        if ch in "}]":
            j = len(cleaned) - 1
            while j >= 0 and cleaned[j] in " \t\r\n":
                j -= 1
            if j >= 0 and cleaned[j] == ",":
                del cleaned[j:]                     # the comma and the whitespace after it
        cleaned.append(ch)
    return "".join(cleaned)


def _write_atomic(path: str, text: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sk-wire-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: str) -> str | None:
    bak = path + BACKUP_SUFFIX
    try:
        with open(path, encoding="utf-8") as fh:
            data = fh.read()
        with open(bak, "w", encoding="utf-8") as fh:
            fh.write(data)
        return bak
    except OSError:
        return None


# --------------------------------------------------------------------------- stanzas
def is_ours(entry: Any) -> bool:
    """Whether an entry in a host's server map is one `sk wire` would have written.

    The name alone is not proof - a user may have their own `skeletonkey` entry with a
    different interpreter - so the match is on content: any serialized mention of
    `skeletonkey` in command, args or url."""
    if not isinstance(entry, dict):
        return False
    blob = json.dumps(entry).lower()
    return "skeletonkey" in blob


def build_stanza(spec: HostSpec, *, transport: str, python: str, roots: list[str],
                 read_only: bool, url: str) -> dict[str, Any]:
    if transport == "http":
        return {"type": "http", "url": url}
    args = ["-m", "skeletonkey.mcp"]
    for r in roots:
        args += ["--root", r]
    if read_only:
        args.append("--read-only")
    return {"command": python, "args": args}


# --------------------------------------------------------------------------- the operation
def wire(*, host_ids: list[str] | None = None, scope: str = "user", cwd: str | None = None,
         transport: str = "stdio", port: int = 8765, bind: str = "127.0.0.1",
         python: str | None = None, roots: list[str] | None = None, read_only: bool = False,
         name: str = ENTRY_NAME, url: str | None = None, remove: bool = False,
         check_only: bool = False, dry_run: bool = False, allow_jsonc: bool = False,
         env: dict[str, str] | None = None) -> dict[str, Any]:
    """Wire, unwire, or report on every requested host. One function serves the CLI and
    the tests; the CLI adds only argparse and printing. `env` defaults to the real
    environment and is how tests aim path resolution at a temp home."""
    env = dict(os.environ) if env is None else dict(env)
    cwd = os.path.abspath(cwd or env.get("PWD") or os.getcwd())
    python = python or sys.executable
    host_ids = list(host_ids) if host_ids else [h.id for h in hosts()]
    if url is None:
        url = f"http://{bind}:{port}/mcp"
    if roots is None and scope == "project":
        roots = [cwd]

    rows: list[dict[str, Any]] = []
    for hid in host_ids:
        spec = HOSTS_BY_ID.get(hid)
        if spec is None:
            rows.append({"host": hid, "status": "error",
                         "error": f"unknown host {hid!r}; known: {', '.join(sorted(HOSTS_BY_ID))}"})
            continue
        rows.append(_wire_one(spec, scope=scope, cwd=cwd, transport=transport,
                              python=python, roots=roots or [], read_only=read_only,
                              name=name, url=url, remove=remove, check_only=check_only,
                              dry_run=dry_run, allow_jsonc=allow_jsonc, env=env))
    ok = all(r.get("status") not in ("error", "needs-manual") for r in rows)
    return {"schema": SCHEMA, "ok": ok, "scope": scope, "transport": transport,
            "entry": name, "hosts": rows}


def _wire_one(spec: HostSpec, *, scope: str, cwd: str, transport: str, python: str,
              roots: list[str], read_only: bool, name: str, url: str, remove: bool,
              check_only: bool, dry_run: bool, allow_jsonc: bool,
              env: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = {"host": spec.id, "title": spec.title, "scope": scope,
                           "key": spec.key}
    if transport == "http" and not spec.http:
        row.update(status="skipped",
                   reason=f"{spec.title} does not support remote (url) servers; "
                          f"its notes: {'; '.join(spec.notes) or 'stdio only'}")
        return row

    if scope == "project":
        if not spec.project_paths:
            # a project-scoped run must never fall back to the user's home config
            row.update(status="skipped", reason=f"{spec.title} has no project-scope config")
            return row
        cands = [os.path.join(cwd, *p.split("/")) for p in spec.project_paths]
    else:
        cands = spec.user_paths(env)
    if not cands:
        row.update(status="skipped", reason="no config location known for this platform")
        return row
    path = next((c for c in cands if os.path.isfile(c)), cands[0])
    row["path"] = path

    exists = os.path.isfile(path)
    if not exists:
        if remove:
            row.update(status="skipped", reason="config file does not exist; nothing to remove")
            return row
        data: dict[str, Any] = {}
        info: dict[str, Any] = {}
    else:
        data, info = _read_config(path)
        if "error" in info:
            row.update(status="error", error=info["error"])
            return row

    servers: dict[str, Any] = data.get(spec.key) if isinstance(data.get(spec.key), dict) else {}
    had_ours = name in servers and is_ours(servers[name])
    row["wired"] = had_ours

    if remove:
        if not had_ours:
            other = name in servers
            row.update(status="skipped",
                       reason=("entry exists but is not ours; refusing to remove a "
                               "hand-written entry" if other else "no skeletonkey entry present"),
                       **({"entry": servers[name]} if other else {}))
            return row
        if check_only or dry_run:
            row.update(status="dry-run" if dry_run else "checked", action="remove")
            return row
        bak = _backup(path)
        del servers[name]
        data[spec.key] = servers
        try:
            _write_atomic(path, json.dumps(data, indent=2) + "\n")
        except OSError as exc:
            row.update(status="error", error=str(exc))
            return row
        row.update(status="removed", backup=bak)
        return row

    stanza = build_stanza(spec, transport=transport, python=python, roots=roots,
                          read_only=read_only, url=url)
    row["stanza"] = {name: stanza}

    needs_write = (not exists) or (not had_ours) or _needs_update(servers[name], stanza)
    if needs_write and exists and info.get("jsonc") and not allow_jsonc:
        # A comments-bearing file can only be rewritten by dropping the comments. Refuse
        # by default and hand back the exact entry to paste; `--allow-jsonc` accepts the
        # comment loss explicitly.
        row.update(status="needs-manual",
                   reason="config is JSONC (comments); a rewrite would drop them",
                   hint="paste the stanza manually, or pass --allow-jsonc")
        return row
    action = "wire" if (not exists or not had_ours) else ("update" if needs_write else "already")
    if action == "already":
        row.update(status="already")
        return row
    if check_only:
        row.update(status="checked", action=action)
        return row
    if dry_run:
        row.update(status="dry-run", action=action)
        return row

    bak = _backup(path) if exists else None
    servers[name] = stanza
    data[spec.key] = servers
    try:
        _write_atomic(path, json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        row.update(status="error", error=str(exc))
        return row
    out = {"status": "wired" if action == "wire" else "updated"}
    if bak:
        out["backup"] = bak
    if info.get("jsonc"):
        out["note"] = "rewrote a JSONC file as plain JSON; comments were dropped (--allow-jsonc)"
    row.update(out)
    return row


def _needs_update(existing: Any, stanza: dict[str, Any]) -> bool:
    return not isinstance(existing, dict) or existing != stanza


def status_rows(*, scope: str = "user", cwd: str | None = None,
                env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Read-only scan: which hosts are installed here, and which of them point at us.
    This is the `wire` check `sk doctor` embeds - detection without writes."""
    env = dict(os.environ) if env is None else dict(env)
    cwd = os.path.abspath(cwd or env.get("PWD") or os.getcwd())
    out = []
    for spec in hosts():
        cands = ([os.path.join(cwd, *p.split("/")) for p in spec.project_paths]
                 if scope == "project" else spec.user_paths(env))
        path = next((c for c in cands if os.path.isfile(c)), None)
        row: dict[str, Any] = {"host": spec.id, "title": spec.title, "path": path,
                               "installed": path is not None}
        if path is None:
            row["wired"] = False
        else:
            data, info = _read_config(path)
            servers = data.get(spec.key) if isinstance(data.get(spec.key), dict) else {}
            row["wired"] = any(is_ours(v) for v in servers.values())
            row["wired_entry"] = next((k for k, v in servers.items() if is_ours(v)), None)
            if "error" in info:
                row["unparsable"] = True
        out.append(row)
    return out
