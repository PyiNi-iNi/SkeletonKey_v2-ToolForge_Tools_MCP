---
name: sandbox
description: >-
  Create and manage isolated scratch workspaces ("sandboxes"): a named project
  directory seeded from a template, its own Python venv (pinned version and
  packages), and commands run inside it under a cleaned environment with
  time/output limits - all recorded so a task can be resumed or torn down.
when_to_use: >-
  Setting up a clean, throwaway area to try a package, scaffold a python app or
  library, run isolated tests, or hand a self-contained project to another task.
  Use it when you want isolation of a scratch *workspace* (own dir, own venv,
  clean PATH) rather than the host profile's filesystem and interpreter.
version: "1"
tags: [sandbox, workspace, venv, isolation, scratch]
priority: 60
requires: [sandbox.create, sandbox.runtime, sandbox.run, sandbox.status]
allowed-tools: [sandbox.create, sandbox.runtime, sandbox.run, sandbox.status,
                fs.delete, fs.list, fs.write, shell.run]
---

# Isolated workspaces ("sandboxes")

A *sandbox* here is a self-contained scratch project: its own directory, seeded
from a template you choose, optionally with its **own Python venv** (so
`pip install` and imports never touch the host site-packages) and commands run
inside it under a cleaned environment whose PATH points into that venv.

These four tools are the creator's lifecycle:

```jsonc
sandbox.create {name: "play", template: "python-app"}          // scaffold a workspace
sandbox.runtime {path: "ws/play", packages: ["httpx"]}          // give it its own venv (+pkgs)
sandbox.run {path: "ws/play", argv: ["pytest", "-q"]}           // run inside it, isolated
sandbox.status {root: "ws"}                                     // inventory / deep inspect
```

## 1. What "isolated" means here (be honest)

- Its **own directory**: name it, choose where it lives (`root`, default = the
  workspace you are in), and it is recorded in `<dir>/.sandbox/manifest.json`.
- Its **own runtime**: `.venv` created from your current interpreter (or a
  requested `python_version`), with pip packages installed into it.
- Its **own command environment**: `sandbox.run` runs with cwd inside the
  sandbox, the venv `bin`/`Scripts` first on PATH, a clean env, and a hard
  timeout (process tree killed) plus an output cap.

It is **not** an OS jail. Like `shell.run`, the child has the operator user's
permissions and there is no kernel/network/cgroup sandbox. If you truly need to
confine a hostile binary you must not reach for these tools - that is out of
scope for the whole toolkit (see `docs/SECURITY-MODEL.md`).

## 2. Locating a sandbox

- Prefer the `path` returned by `sandbox.create` on later calls.
- Or `name` + `root` (root defaults to the workspace). Names are one safe
  component: `[A-Za-z0-9_-]`.

## 3. Workflow

- **Create**: pick `template` (`generic`, `minimal`, `python-app`, `python-lib`,
  `node-app`), add seed files with `files: {rel: content}`, choose a runtime
  (`make_runtime`, `python_version`, `packages`) and optionally `git: true`.
- **Run**: `sandbox.run {path, argv: ["python", "-c", "..."]}`. To exercise the
  venv, let argv start with `python`/`pytest`/an installed CLI - PATH resolves it
  to the sandbox's venv. For other interpreters name them explicitly,
  e.g. `["bash", "script.sh"]`.
- **Inspect**: `sandbox.status` (deep view of one) or with no locator (inventory
  under `root`). Every run is appended to `.sandbox/sandbox.log`.

## 4. Teardown is journaled, never `rm -rf`

Skill-authored tools may not declare `destructive` (docs/SKILLS-SPEC.md), so a
sandbox is removed through the toolkit's own reversible path:

```jsonc
fs.delete {path: "<sandbox path>", recursive: true}   // removes dir + its manifest row
fs.undo {undo_token: "<the token fs.delete returned>"} // restores it if you change your mind
```

The inventory is disk-derived, so deleting the directory is enough - there is no
separate registry to desync. See `references/lifecycle.md` for the recorded
layout and example transcript.
