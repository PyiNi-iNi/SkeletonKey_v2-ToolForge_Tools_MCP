# Lockfiles, and what each one entitles you to conclude

A lockfile is a promise about *this* environment. Reading it tells you how much of that
promise you can rely on, and each manager's file says something different.

## What the file proves

| File | Pins | Verifies | If it is missing or stale |
| --- | --- | --- | --- |
| `uv.lock` | exact version + hash per package, per platform marker | hashes at sync time | `uv sync --frozen` refuses, which is the correct outcome — do not drop `--frozen` to make it pass |
| `poetry.lock` | exact version + hash for the content-hash of `pyproject.toml` | the content-hash, then hashes | a mismatch is *information*: `pyproject.toml` changed and the lock was not regenerated, so someone edited dependencies without resolving |
| `pdm.lock` | exact version + hashes, `__meta__.lock_version` | forward-compat check | a newer `lock_version` means the installed `pdm` is older than the file |
| `requirements.txt` with `--hash` lines | exact version + hash | `pip install --require-hashes` verifies every wheel | pip refuses the mix: with `--require-hashes` every line needs a hash, so one unpinned line is an error, not a half-verified install |
| `requirements.txt` without hashes | version only | nothing | you are trusting the index and the clock |
| `pyproject.toml` only | a range | nothing | every sync may resolve differently; say so in the receipt |
| `Pipfile.lock` | version + hash, split into default/develop | hashes | the manager is in maintenance; the file is still honest, treat it as a lock |

## The one command per manager

Always through the interpreter or the pinned tool, never a bare global command:

```json
{"script": "\"$1\" -m pip install -q --require-hashes -r \"$2\"",
 "argv": ["/abs/repo/.venv/bin/python", "/abs/repo/requirements.txt"], "dialect": "bash", "timeout_s": 600}
```

```json
{"script": "uv sync --frozen --directory $1", "argv": ["/abs/repo"], "dialect": "bash", "timeout_s": 300}
```

`--directory` (uv), `-C`/`--directory` (pip's `--target` is unrelated), or the venv path
itself (poetry, pdm, which resolve their project from the current directory) decide *where*
the environment lands. There is no option that means "use the interpreter calling me", so
pass the venv python and let the manager derive the environment from it: `"$VENV/bin/python"
-m pip`, `uv sync --python "$VENV/bin/python"`, `poetry -C "$root" install --sync`.

## Regenerating a lock safely

The lock is a build artifact with a reviewable diff, so an agent should be able to show why it
changed:

1. resolve into a scratch copy — write the new lock to a different name first
2. diff the two files and count the moved versions (`fs.read` + a line diff; a lock diff of
   400 changed versions is a different event from 3)
3. only then replace it, and commit the lock together with the manifest change that caused it
4. run the test suite *after* the sync, because the point of a lock is that the next sync is
   the one you tested

If step 2 shows a large diff and nobody asked for an upgrade, the manifest has unpinned ranges
and someone's release moved them. Record that as a finding; do not silently commit it.

## Offline and airgapped

`pip download -r requirements.txt -d ./vendor-cache` on a connected host, then
`pip install --no-index --find-links ./vendor-cache -r requirements.txt` on the other one.
`uv` differs: its cache is content-addressed and not a directory of wheels you can copy, so
the portable artifact is still a wheelhouse built by `pip download` (or a mirror). A
"network unavailable" failure during a sync is normally one of these two shapes, and both are
worth distinguishing before reaching for a mirror URL: the index is unreachable, or the
package resolution wants to check a source the lock does not pin.

## When the environment is right and the import still fails

| Symptom | Usual cause | Check |
| --- | --- | --- |
| `ModuleNotFoundError` after a successful install | the process is not the venv interpreter | `sys.executable` *in the failing process*, printed at the top of it |
| imports work in a shell, fail in the service | the service's environment (systemd unit, Windows service, container) has its own `PATH`/interpreter | print `sys.path` from the service, not from your shell |
| works after `pip install -e .`, fails in CI | the editable install wrote a `__editable__` path hook that CI does not have | install the package, or `PYTHONPATH`, explicitly — not both |
| package found, version wrong | two environments: user site-packages shadowing, or a stale wheelhouse | `importlib.metadata.version(name)` and `module.__file__` together |
| C-extension import fails only on one host | the wheel is for another `cp3x`/platform tag | `fs.read` of the wheel name is enough: the tag is in the filename |
| everything fine locally, `pip` refuses in CI | PEP 668 `externally-managed-environment` | a venv is the answer; `--break-system-packages` is the wrong one |
