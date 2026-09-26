# Sandbox layout and lifecycle

Everything a sandbox owns lives under its directory, so a sandbox is *just a
folder plus a manifest* - copy it, rename it, or delete it and nothing else in
the toolkit desyncs.

```
<root>/
  <name>/                    # the sandbox (e.g. ws/play)
    .sandbox/
      manifest.json          # schema "sandbox/v1": name, template, runtime, git, runs, log path
      sandbox.log            # timestamped audit of create / runtime / run events
    .venv/                   # isolated runtime, when provisioned (own interpreter + site-packages)
    README.md  .gitignore ...# the template's seed files (customizable via `files`)
```

## Reading a manifest

`sandbox.status {path}` returns the manifest plus live facts it recomputes each
call: whether `.venv` is actually present, the venv's `python_version`, installed
packages, run count, file count/size, git flag and the last ~30 log lines. It
does not trust the manifest's memory for things that can go stale (a deleted
venv, extra packages) - it re-checks the directory.

## Resume

Because state is on disk, a sandbox is resumable across calls and across engine
restarts: `sandbox.status`/`sandbox.run`/`sandbox.runtime` re-locate it by the
same `path` or `name`+`root` you created it with, and `runs` in the manifest
monotonic increments so an agent can tell how far a scratch area has been driven.

## Teardown

```jsonc
fs.delete {path: "<sandbox path>", recursive: true}   // journaled + undoable
fs.undo {undo_token: "<token fs.delete returned>"}    // bring it back if needed
```

A skill tool cannot declare `destructive` (docs/SKILLS-SPEC.md), and by design a
sandbox creator routes removal through the toolkit's reversible path instead of
`rm -rf`. The inventory is derived by scanning `*/.sandbox/manifest.json`, so
removing the folder removes the inventory row - no orphan registry entry.

## Template reference

| template | seeds | use for |
| --- | --- | --- |
| `generic` | README, .gitignore, HELLO.txt | any scratch area |
| `minimal` | README, .gitignore, HELLO.txt | tiniest footprint |
| `python-app` | pyproject + `src/<pkg>/` + tests | an executable app |
| `python-lib` | pyproject + `src/<pkg>/__init__.py` + tests | a library |
| `node-app` | package.json + index.js + .gitignore | node scratch |

`.gitignore` always excludes `.sandbox/` and `.venv/` so scaffolding never
commits its own bookkeeping.

## Honest limits

- **Resource limits**: `sandbox.run` enforces a hard `timeout_s` (process tree
  killed) and `max_output_bytes`. A POSIX-only `limits: {mem_mb, cpu_s}` map
  applies best-effort `resource` rlimits; Windows ignores it (no stdlib job
  object).
- **No network isolation**: package installs and command runs may reach the
  network. An offline install reports in `install_errors`, not a crash.
- **No kernel/OS sandbox**: see the main `SKILL.md`.
