"""Shared logic for the ``skills/sandbox`` pack's tools.

A skill-authored tool is a script run through the shell runner, so it cannot import this
module by project path; each ``handler_*.py`` puts this module's directory on ``sys.path``
(the runner sets ``SKELETONKEY_SKILL_DIR``) and calls one ``cmd_*`` function here. Keeping the
real behaviour in one stdlib-only file means the four tools cannot drift from each other, and
lets the test-suite exercise the logic directly as well as through a real subprocess.

Isolation model (be honest with agents): these tools build a **self-contained workspace dir**
(the sandbox), provision an **isolated Python runtime** inside it (a venv, so pip and imports
never touch the system site-packages), and run commands with a **cleaned environment whose
PATH is pointed into that venv**, bounded by a timeout and an output cap. That is *process and
path* isolation of a scratch project - the same model the rest of the toolkit uses. It is not
an OS jail: like ``shell.run``, the subprocess has the operator user's permissions and no
kernel namespace / cgroup / network sandbox is imposed. Teardown is deliberately routed back
through the journaled ``fs.delete`` so a sandbox is still undoable, never ``rm -rf``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "sandbox/v1"
META_DIR = ".sandbox"
MANIFEST = "manifest.json"
LOG_NAME = "sandbox.log"
VENV_NAME = ".venv"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
TEMPLATES = ("generic", "minimal", "python-app", "python-lib", "node-app")
_VENV_SKIP = frozenset({VENV_NAME, META_DIR, ".git", "__pycache__", ".pytest_cache"})

_TS = "%Y-%m-%dT%H:%M:%SZ"


def err(message: str, code: str = "BAD_ARGS", **extra) -> dict:
    out = {"ok": False, "error": {"code": code, "message": message}}
    if extra:
        out["error"].update(extra)
    return out


def ok(payload: dict) -> dict:
    return {"ok": True, **payload}


def _now() -> str:
    return time.strftime(_TS, time.gmtime())


def _log(sb: Path, line: str) -> None:
    """Append a timestamped line to the sandbox's own log (best effort)."""
    try:
        meta = sb / META_DIR
        meta.mkdir(parents=True, exist_ok=True)
        with (meta / LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(f"[{_now()}] {line}\n")
    except OSError:
        pass


def _resolve_cwd() -> Path:
    return Path(os.getcwd()).resolve()


def _root_path(root) -> Path:
    """Absolute directory to place sandboxes in; relative paths resolve against the cwd."""
    if root in (None, ""):
        return _resolve_cwd()
    p = Path(str(root)).expanduser()
    return p if p.is_absolute() else (_resolve_cwd() / p)


def _clean_name(name: str) -> str | None:
    n = (name or "").strip()
    return n if NAME_RE.match(n) else None


def sb_dir(name: str, root: str | None) -> Path | None:
    """Directory for a named sandbox under ``root`` (no manifest required yet)."""
    n = _clean_name(name)
    if n is None:
        return None
    return _root_path(root) / n


def _locate(args: dict) -> tuple[Path | None, str | None]:
    """Resolve an existing sandbox dir from ``path`` OR ``name``+``root``.

    Returns ``(dir, error)``. ``path`` wins; ``name`` is validated and rooted at ``root``
    (default the cwd/workspace). The returned dir must carry a sandbox manifest unless the
    caller only needed a path target (create uses ``sb_dir`` directly).
    """
    p = args.get("path")
    if p:
        raw = Path(str(p)).expanduser()
        d = raw if raw.is_absolute() else (_resolve_cwd() / raw)
        return d.resolve(), None
    d = sb_dir(str(args.get("name") or ""), args.get("root"))
    if d is None:
        return None, "pass a `path` to a directory, or a `name` that is one safe component " \
                    "[a-z0-9_-] plus an optional `root` to place it under"
    return d, None


def read_manifest(sb: Path) -> dict | None:
    try:
        with (sb / META_DIR / MANIFEST).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_manifest(sb: Path, manifest: dict) -> Path:
    meta = sb / META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    (meta / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return meta / MANIFEST


def manifest_for(sb: Path, *, name: str | None = None, template: str = "generic",
                 description: str = "", root: str = "") -> dict:
    return {
        "schema": SCHEMA, "name": name or sb.name, "path": str(sb.resolve()),
        "root": str(_root_path(root)), "template": template,
        "description": description or "",
        "created": _now(), "updated": _now(),
        "runtime": None, "git": False, "runs": 0, "log": str(sb / META_DIR / LOG_NAME),
    }


# --------------------------------------------------------------------------- templates
GITIGNORE = (
    ".venv/\n.sandbox/\n__pycache__/\n*.py[cod]\n*.egg-info/\n.pytest_cache/\n"
    "dist/\nbuild/\nnode_modules/\n"
)


def _template_files(template: str, name: str) -> dict[str, str]:
    """Ordered ``relpath -> content`` seed files. `.sandbox/` is written separately."""
    mod = name.replace("-", "_")
    README = (f"# {name}\n\nIsolated workspace created by the sandbox skill.\n\n"
              f"Template: `{template}`.\n")
    files: dict[str, str] = {"README.md": README, ".gitignore": GITIGNORE}
    if template in ("minimal", "generic"):
        files["HELLO.txt"] = f"created from the {template} template\n"
        return files
    if template == "python-app":
        files["pyproject.toml"] = (
            f"[project]\nname = \"{name}\"\nversion = \"0.1.0\"\n"
            "requires-python = \">=3.9\"\ndescription = \"scratch app\"\n\n"
            "[build-system]\nrequires = [\"setuptools\"]\nbuild-backend = \"setuptools.build_meta\"\n\n"
            "[tool.setuptools.packages.find]\nwhere = [\"src\"]\n")
        files[f"src/{mod}/__init__.py"] = f'"""App package for {name}."""\n\n__version__ = "0.1.0"\n'
        files[f"src/{mod}/app.py"] = (
            "import sys\n\n\ndef main(argv=None) -> int:\n"
            f"    print(\"hello from {name}\")\n"
            "    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main())\n")
        files["tests/test_smoke.py"] = (
            "def test_importable():\n"
            f"    import {mod}  # noqa: F401\n    assert {mod}.__version__ == \"0.1.0\"\n")
        return files
    if template == "python-lib":
        files["pyproject.toml"] = (
            f"[project]\nname = \"{name}\"\nversion = \"0.1.0\"\n"
            "requires-python = \">=3.9\"\n\n"
            "[build-system]\nrequires = [\"setuptools\"]\nbuild-backend = \"setuptools.build_meta\"\n\n"
            "[tool.setuptools.packages.find]\nwhere = [\"src\"]\n")
        files[f"src/{mod}/__init__.py"] = (
            f'"""Public surface of the {name} library."""\n\n__all__ = ["public_fn"]\n\n\n'
            "def public_fn(x=1):\n    return x\n")
        files["tests/test_smoke.py"] = (
            f"from {mod} import public_fn\n\n\ndef test_public_fn():\n    assert public_fn(2) == 2\n")
        return files
    if template == "node-app":
        files["package.json"] = (
            "{\n  \"name\": \"" + name + "\",\n  \"version\": \"0.1.0\",\n"
            "  \"private\": true,\n  \"main\": \"index.js\"\n}\n")
        files["index.js"] = f'console.log("hello from {name}");\n'
        return files
    return files


# --------------------------------------------------------------------- process running
def _build_env(venv_bin: Path | None, extra: dict | None, base: Path | None) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "TMPDIR": os.environ.get("TMPDIR", os.environ.get("TEMP", "/tmp"))}
    if venv_bin:
        env["PATH"] = str(venv_bin) + os.pathsep + env["PATH"]
        env["VIRTUAL_ENV"] = str(venv_bin.parent)
    for k, v in (extra or {}).items():
        env[str(k)] = str(v)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if base:
        env["SHELL"] = "bash"
    return {k: v for k, v in env.items() if v is not None}


def run_proc(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout_s: float,
             max_output: int, limits: dict | None = None) -> dict:
    """Run one command; return a uniform status dict. Timeout kills the process tree."""
    pre = None
    if limits and sys.platform != "win32":
        mem = limits.get("mem_mb")
        cpu = limits.get("cpu_s")

        def _lim() -> None:
            import resource as _r
            if mem:
                _r.setrlimit(_r.RLIMIT_AS, (int(mem) * 1024 * 1024,) * 2)
            if cpu:
                _r.setrlimit(_r.RLIMIT_CPU, (int(cpu),) * 2)

        pre = _lim
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env, capture_output=True, timeout=float(timeout_s),
            preexec_fn=pre, start_new_session=True, check=False)
        out, err = proc.stdout, proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = -1
        out, err = (exc.stdout or b""), (exc.stderr or b"")
    except FileNotFoundError:
        return {"exit_code": 127, "timed_out": False, "completed": True,
                "stdout": "", "stderr": f"executable not found: {cmd[0]}",
                "truncated": False, "duration_ms": int((time.time() - started) * 1000),
                "stdout_bytes": 0, "stderr_bytes": len(str(cmd))}
    dur = int((time.time() - started) * 1000)
    so, se = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    trunc = False
    so_cap, se_cap = so, se
    if len(so.encode("utf-8")) > max_output:
        so_cap, trunc = so[:max_output], True
    return {"exit_code": code, "timed_out": timed_out, "completed": not timed_out,
            "stdout": so_cap, "stderr": se_cap[-4000:] if se_cap else "",
            "truncated": trunc, "duration_ms": dur,
            "stdout_bytes": len(so), "stderr_bytes": len(se)}


def _venv_python(sb: Path) -> Path:
    return sb / VENV_NAME / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _resolve_python(requested: str | None) -> tuple[str, str | None]:
    cur = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if requested:
        req = str(requested).strip()
        if cur == req or cur.startswith(req + "."):
            return sys.executable, None
        for cand in ("python" + req, "python"):
            found = shutil.which(cand)
            if found:
                return found, None
        return sys.executable, (f"python {req!r} was not on PATH; using current {cur}")
    return sys.executable, None


def provision_runtime(sb: Path, *, requested_version: str | None, packages: list[str],
                      force: bool = False) -> dict:
    """Create/refresh ``<sb>/.venv`` and install ``packages``. Returns a runtime status dict."""
    venv_dir = sb / VENV_NAME
    runtime = {"venv": str(venv_dir), "created": False, "installed": [],
               "install_errors": [], "notes": []}
    py, note = _resolve_python(requested_version)
    if note:
        runtime["notes"].append(note)
    venv_exists = (venv_dir / ("pyvenv.cfg")).exists()
    if not venv_exists or force:
        st = run_proc([py, "-m", "venv", str(venv_dir)], cwd=sb, env=_build_env(None, {}, None),
                      timeout_s=180, max_output=20000)
        if st["exit_code"] != 0:
            runtime["errors"] = st["stderr"] or "venv creation failed"
            return runtime
        runtime["created"] = True
        runtime["python"] = py
        _log(sb, f"runtime: created venv with {py}")
    else:
        _log(sb, "runtime: reused existing venv")
    py_exe = _venv_python(sb)
    if not py_exe.exists():
        runtime["errors"] = f"venv python missing at {py_exe}"
        return runtime
    # record the real interpreter version inside the venv
    ver = run_proc([str(py_exe), "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                   cwd=sb, env=_build_env(None, {}, None), timeout_s=30, max_output=1000)
    runtime["python_version"] = ver["stdout"].strip() or "?"
    pkgs = [p for p in (packages or []) if isinstance(p, str) and p.strip()]
    if pkgs:
        pip = [str(py_exe), "-m", "pip", "install", "--disable-pip-version-check"]
        st = run_proc([*pip, "--no-input", *pkgs], cwd=sb,
                      env=_build_env(None, {}, None), timeout_s=600, max_output=20000)
        if st["exit_code"] == 0:
            runtime["installed"] = pkgs
            _log(sb, "runtime: installed " + ", ".join(pkgs))
        else:
            runtime["install_errors"].append(st["stderr"] or f"pip exited {st['exit_code']}")
            runtime["notes"].append("package install failed (offline?) - see install_errors")
            _log(sb, "runtime: package install FAILED: " + (st["stderr"] or "")[:300])
    return runtime


def _git_init(sb: Path) -> dict:
    st = run_proc(["git", "init", "-q"], cwd=sb, env=_build_env(None, {}, None),
                  timeout_s=30, max_output=4000)
    if st["exit_code"] == 0:
        _log(sb, "git: initialised repository")
        return {"initialized": True}
    return {"initialized": False, "reason": st["stderr"] or "git init failed"}


# ----------------------------------------------------------------------------- commands
def cmd_create(args: dict) -> dict:
    """``sandbox.create`` - the workspace creator with the customization surface."""
    name = (args.get("name") or "").strip()
    if _clean_name(name) is None:
        return err("name must be a single safe component (letters, digits, '_', '-')",
                   **{"given": name})
    template = str(args.get("template") or "generic")
    if template not in TEMPLATES:
        return err(f"template must be one of {', '.join(TEMPLATES)}", template=template)
    root = _root_path(args.get("root"))
    if not root.is_dir():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return err(f"cannot create root dir {root}: {exc}", **{"root": str(root)})
    sb = root / name
    if sb.exists() and any(sb.iterdir()) and not bool(args.get("force")):
        return err(f"{sb} already exists and is not empty; pass force=true to seed it in place, "
                   "or pick another name", existing=True, code="CONFLICT")
    description = str(args.get("description") or "")
    make_runtime = bool(args.get("make_runtime")) or bool(args.get("packages"))
    git = bool(args.get("git"))
    dry_run = bool(args.get("dry_run"))
    files = _template_files(template, name)
    extra = args.get("files")
    if isinstance(extra, dict):
        for rel, content in extra.items():
            files[str(rel)] = content

    manifest = manifest_for(sb, name=name, template=template, description=description,
                            root=str(root))
    manifest["requested"] = {"make_runtime": make_runtime, "git": git,
                             "python_version": args.get("python_version"),
                             "packages": list(args.get("packages") or []),
                             "files": bool(extra)}
    if dry_run:
        plan = {"name": name, "path": str(sb), "root": str(root), "template": template,
                "would_mkdir": [str(sb), str(sb / META_DIR)],
                "would_write": sorted(files),
                "would_runtime": make_runtime, "would_git_init": git, "manifest": manifest}
        return ok({"dry_run": True, **plan})

    sb.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, content in sorted(files.items()):
        target = sb / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # never overwrite a non-empty existing file unless force was asked
        if target.exists() and not bool(args.get("force")):
            continue
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    write_manifest(sb, manifest)
    _log(sb, f"created sandbox (template={template}, git={git}, runtime={make_runtime})")

    runtime = None
    if make_runtime:
        runtime = provision_runtime(sb, requested_version=args.get("python_version"),
                                    packages=list(args.get("packages") or []))
        manifest["runtime"] = runtime
        write_manifest(sb, manifest)
    gitinfo = None
    if git:
        gitinfo = _git_init(sb)
        manifest["git"] = bool(gitinfo.get("initialized"))
        write_manifest(sb, manifest)

    manifest["updated"] = _now()
    write_manifest(sb, manifest)
    return ok({
        "created": True, "name": name, "path": str(sb.resolve()), "root": str(root),
        "template": template, "files_written": sorted(written), "runtime": runtime,
        "git": gitinfo, "manifest": manifest,
        "next_steps": [
            {"tool": "sandbox.runtime", "args": {"path": str(sb), "packages": ["<pkg>"]},
             "why": "add a runtime / install packages later"},
            {"tool": "sandbox.run", "args": {"path": str(sb), "argv": ["pwd"]},
             "why": "run a command inside the sandbox now"},
            {"tool": "sandbox.status", "args": {"path": str(sb)},
             "why": "inspect this sandbox"}],
    })


def cmd_runtime(args: dict) -> dict:
    """``sandbox.runtime`` - provision the isolated venv for an existing sandbox."""
    sb, e = _locate(args)
    if e:
        return err(e)
    if not read_manifest(sb):
        return err(f"{sb} is not a sandbox (no {META_DIR}/{MANIFEST})", code="NOT_A_SANDBOX")
    manifest = read_manifest(sb)
    dry_run = bool(args.get("dry_run"))
    if dry_run:
        return ok({"dry_run": True, "path": str(sb),
                   "venv": str(sb / VENV_NAME),
                   "python_version": args.get("python_version"),
                   "packages": list(args.get("packages") or [])})
    runtime = provision_runtime(sb, requested_version=args.get("python_version"),
                                packages=list(args.get("packages") or []),
                                force=bool(args.get("force_recreate")))
    manifest["runtime"] = runtime
    manifest["updated"] = _now()
    write_manifest(sb, manifest)
    payload = {"path": str(sb.resolve()), "runtime": runtime}
    if runtime.get("install_errors"):
        payload["next_steps"] = [
            {"tool": "sandbox.runtime", "args": {"path": str(sb),
                                                 "python_version": args.get("python_version"),
                                                 "packages": list(args.get("packages") or [])},
             "why": "retry install once connectivity exists"}]
    return ok(payload)


def cmd_run(args: dict) -> dict:
    """``sandbox.run`` - run a command inside an existing sandbox (confined to it)."""
    sb, e = _locate(args)
    if e:
        return err(e)
    if not read_manifest(sb):
        return err(f"{sb} is not a sandbox (no {META_DIR}/{MANIFEST})", code="NOT_A_SANDBOX")
    argv = args.get("argv")
    if not (isinstance(argv, list) and argv and all(isinstance(x, str) and x for x in argv)):
        return err("argv must be a non-empty list of command tokens, e.g. [\"python\", \"-c\", \"..\"]")
    manifest = read_manifest(sb)
    workdir = sb
    if args.get("workdir"):
        wd = (sb / str(args["workdir"])).resolve()
        if not (str(wd).startswith(str(sb)) and wd.is_dir()):
            return err(f"workdir {args['workdir']} is not an existing subdir of the sandbox")
        workdir = wd
    use_runtime = args.get("use_runtime", True)
    venv_bin = None
    if use_runtime:
        b = sb / VENV_NAME / ("Scripts" if os.name == "nt" else "bin")
        if b.is_dir():
            venv_bin = b
    timeout_s = max(0.5, min(float(args.get("timeout_s") or 120), 540))  # < the 600s outer cap
    max_out = int(args.get("max_output_bytes") or 200_000)
    env = _build_env(venv_bin, args.get("env"), sb)
    limits = args.get("limits") if isinstance(args.get("limits"), dict) else None
    st = run_proc(argv, cwd=workdir, env=env, timeout_s=timeout_s, max_output=max_out,
                  limits=limits)
    manifest["runs"] = int(manifest.get("runs", 0)) + 1
    manifest["updated"] = _now()
    write_manifest(sb, manifest)
    log_line = f"run #{manifest['runs']}: {' '.join(argv)[:200]} " \
               f"-> exit {st['exit_code']} ({st['duration_ms']}ms)"
    _log(sb, log_line)
    payload = {"sandbox": str(sb.resolve()), "cwd": str(workdir),
               "used_runtime": bool(venv_bin), **st}
    if st["exit_code"] not in (0, None):
        payload["next_steps"] = [
            {"tool": "sandbox.run", "args": {"path": str(sb), "argv": argv,
                                             "timeout_s": min(840, timeout_s * 2)},
             "why": "the command failed; raise the timeout or fix argv"}]
    if st["truncated"]:
        payload["note"] = "output was truncated to the max_output_bytes cap"
    return ok(payload)


def _sandbox_summary(sb: Path, manifest: dict, *, deep: bool) -> dict:
    nfiles = nbytes = 0
    try:
        for p in sb.rglob("*"):
            if any(part in _VENV_SKIP for part in p.parts[1:]):
                continue
            if p.is_file():
                nfiles += 1
                try:
                    nbytes += p.stat().st_size
                except OSError:
                    pass
            if nfiles > 5000:
                break
    except OSError:
        pass
    runtime = manifest.get("runtime") or {}
    venv_ok = bool((sb / VENV_NAME / ("Scripts" if os.name == "nt" else "bin")).exists())
    row = {
        "name": manifest.get("name", sb.name), "path": str(sb.resolve()),
        "template": manifest.get("template"), "created": manifest.get("created"),
        "runs": manifest.get("runs", 0),
        "runtime": {"requested": bool(runtime), "venv_present": venv_ok,
                    "python_version": runtime.get("python_version"),
                    "installed": runtime.get("installed", [])},
        "git": bool(manifest.get("git")), "files": nfiles, "bytes": nbytes,
        "description": manifest.get("description", ""),
    }
    if deep:
        tail = []
        logf = sb / META_DIR / LOG_NAME
        try:
            tail = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        except OSError:
            pass
        row["log_tail"] = tail
    return row


def cmd_status(args: dict) -> dict:
    """``sandbox.status`` - inventory (no locator) or deep inspect one sandbox."""
    if not args.get("path") and not args.get("name"):
        root = _root_path(args.get("root"))
        sandboxes: list[dict] = []
        errors: list[str] = []
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                man = read_manifest(child)
                if man:
                    sandboxes.append(_sandbox_summary(child, man, deep=False))
        for sb_row in sandboxes:
            sb_row["next_steps"] = [
                {"tool": "sandbox.status", "args": {"path": sb_row["path"]},
                 "why": "inspect this sandbox"},
                {"tool": "sandbox.run", "args": {"path": sb_row["path"], "argv": ["pwd"]},
                 "why": "run inside it"}]
        return ok({"sandboxes": sandboxes, "count": len(sandboxes), "root": str(root),
                   "errors": errors,
                   "note": "teardown is journaled: fs.delete {path, recursive:true} removes a "
                           "sandbox and its inventory row together, and fs.undo restores it"})
    sb, e = _locate(args)
    if e:
        return err(e)
    manifest = read_manifest(sb)
    if not manifest:
        return err(f"{sb} is not a sandbox (no {META_DIR}/{MANIFEST})", code="NOT_A_SANDBOX")
    row = _sandbox_summary(sb, manifest, deep=True)
    row["next_steps"] = [
        {"tool": "sandbox.run", "args": {"path": str(sb), "argv": ["pwd"]},
         "why": "run a command inside it"},
        {"tool": "sandbox.runtime", "args": {"path": str(sb), "packages": ["<pkg>"]},
         "why": "install more packages into its isolated venv"},
        {"tool": "fs.delete", "args": {"path": str(sb), "recursive": True},
         "why": "journaled teardown of the whole sandbox (undoable with fs.undo)"}]
    return ok(row)
