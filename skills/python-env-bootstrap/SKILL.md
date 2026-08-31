---
name: python-env-bootstrap
description: >-
  Stand up or repair a Python project's interpreter and dependencies: resolve the real
  executable by absolute path, prove which environment you are in with `sys.executable`,
  and treat a lockfile as the source of truth instead of a requirement guess.
when_to_use: >-
  A repo has no environment yet; imports fail although the package is installed; the
  installer and the lockfile disagree; the same steps must hold on both host families and
  on both interpreter families.
version: "1"
tags: [python, venv, dependencies, lockfile, packaging]
priority: 58
requires: [shell, fs]
triggers: [venv, virtualenv, pip, pip install, uv, poetry, requirements.txt, pyproject.toml, ModuleNotFoundError, import error]
allowed-tools: [shell.run, shell.available, profile.probe, fs.read, fs.glob, fs.stat,
                 fs.sniff, fs.write]
---

# Bootstrapping a Python environment without guessing

An environment is "ready" when three facts agree: the interpreter you invoked is the one
inside the project's venv, that venv's package set matches a committed lock, and the process
that imports the library is the same one that installed it. Most "it installed but will not
import" failures are one of those three disagreeing, and most bootstrap loops fail because
they check none of them.

## 1. Ask the host, not the docs

Before writing any recipe, find out what actually exists here:

1. `profile.probe` — capabilities, incl. whether a shell dialect and `python` were found.
2. `shell.available` — which dialects you may use at all (and why a candidate was rejected).
3. one `shell.run` for the interpreter itself, because a *found* `python` is not necessarily a
   *usable* one (see the Store alias below).

```json
{"script": "python -c \"import sys,sysconfig;print(sys.version);print(sys.executable);print(sysconfig.get_paths()['purelib'])\"",
 "dialect": "bash", "expects": "lines", "timeout_s": 20}
```

Three lines: what it is, which binary answered, where that binary installs packages. Write
them down; every later step is checked against them.

## 2. Do not activate — name the executable

`source .venv/bin/activate` (and the PowerShell sibling, which execution policy may refuse)
is a human convenience with three failure modes for an unattended session: it only affects
one process, it silently no-ops in a dialect whose shell state is not shared, and it makes
the next call's outcome depend on the previous call's ordering. Call the binary by path
instead, which is deterministic in a single call:

| | POSIX | Windows |
| --- | --- | --- |
| venv python | `<root>/bin/python` | `<root>\\Scripts\\python.exe` |
| console script | `<root>/bin/pytest` | `<root>\\Scripts\\pytest.exe` |
| site-packages | `<root>/lib/python3.x/site-packages` | `<root>\\Lib\\site-packages` |
| created with | `python3 -m venv .venv` | `py -3 -m venv .venv` |

`shell.run` takes `cwd`, so `cd … && <cmd>` is never needed; and one absolute path beats both
`python` and `python3`, whose precedence is different on the two hosts.

## 3. Prove the identity of the interpreter you are about to install into

```json
{"script": "\"$1\" -c \"import sys; print(sys.executable); print(sys.prefix != sys.base_prefix)\"",
 "argv": ["/abs/repo/.venv/bin/python"], "dialect": "bash", "expects": "lines"}
```

`True` on the second line means "inside a venv". `False` means your install will land in the
system or user site-packages — which on a modern Linux distribution is refused outright
(`externally-managed-environment`), and on CI images is the reason the next stage cannot
find the package. Run this check *after* creation, not only before: a venv created from a
broken base interpreter reports success and is useless.

## 4. The source of truth, in precedence order

Whichever exists first wins; do not mix two of them in one sync:

1. `uv.lock` — sync with `uv sync --frozen`
2. `poetry.lock` — `poetry install --sync --no-root`
3. `requirements.txt` **with** hashes — `pip install --require-hashes -r …`
4. `requirements.txt` without hashes — pinned versions only; say so in your receipt
5. `pyproject.toml` alone — no lock at all: you are resolving fresh, and the result is not
   reproducible; that is a finding for the human, not a thing to hide

Detect them with `fs.glob` rather than a shell `ls`, and read the pinning style with `fs.read`
— a `>=` line and a `==` line mean different things about what you are allowed to conclude.

## 5. Sync, then re-probe

Install, then prove the effect instead of trusting the exit code of the installer:

```json
{"script": "\"$1\" -m pip install -q --require-hashes -r \"$2\" && \"$1\" -c \"import importlib.metadata as m; print(m.version('rich'))\"",
 "argv": ["/abs/repo/.venv/bin/python", "/abs/repo/requirements.txt"],
 "dialect": "bash", "timeout_s": 300}
```

`python -m pip` and not `pip`, always: a bare `pip` on `PATH` may belong to another
interpreter, and that exact mistake is the top cause of "installed but not importable".

## 6. Long installs are a job, not a blocked turn

A cold dependency install is minutes, and the timeout is yours to set, not to inherit. Prefer
`background: true` plus `shell.job_wait {job_id, timeout_s}` for anything that resolves from
the network: the job's output tail comes back the same, and a wait that times out leaves the
job running instead of killing a half-finished install and leaving a corrupted venv.
Re-bootstrapping over a half-written venv is worse than waiting: remove the directory with
`fs.delete {path, recursive: true}` and start again.

## Anti-patterns

| Don't | Do instead |
| --- | --- |
| trusting `which python` as proof of an environment | `sys.executable` from the interpreter itself |
| `pip install --user` to dodge a permission error | that error is usually "you are in the wrong interpreter"; fix the interpreter |
| `--break-system-packages` | a venv; the refusal exists to keep the OS usable |
| `pip install $(cat requirements.txt)` | `-r requirements.txt` — quoting, comments, and `-e` lines are the file's business |
| reusing a venv whose `pyvenv.cfg` points at a moved path | recreate; a venv is not relocatable and `fs.move` of one breaks it silently |
| `python` on a host where the Store alias is installed | check `sys.executable` — the alias prints nothing and opens a window |
| one `python` for tooling and another for the app | same interpreter, or say which is which in the receipt |
