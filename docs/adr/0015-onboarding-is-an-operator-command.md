# ADR 0015: onboarding and diagnosis are operator commands, not agent tools

Date: 2026-09-05
Status: accepted
Affects: `wire.py` (`sk wire`), `diagnostics.py` (`sk doctor`), `cli.py`,
`docs/CONNECT-A-HOST.md`, `tests/test_wire.py`, `tests/test_doctor.py`,
`tests/test_core_purity.py`

## Context

The P6 distribution goal is "others can install, run, and debug this without reading the
source". The last mile of installation is a JSON stanza inside some host application's
config file - a file that lives in the user's home directory, outside every filesystem
root this toolkit will ever be given. The obvious shortcut was to expose "wire me up"
and "diagnose me" as registry tools, so an autopilot could onboard itself.

## Options considered

1. **Engine tools (`wire.*`, `doctor.*` in the registry).** Rejected. A tool that writes
   outside the sandbox by definition is a standing escape hatch from the sandbox - the
   one guarantee (`fs.*` cannot leave its roots) that makes autonomous operation
   approvable. It also reads host configs, which sit next to other applications' entries
   and their environment blocks, i.e. other programs' configured secrets. The agent's
   view must not widen with installation convenience.
2. **A separate binary (`skeletonkey-wire`).** Rejected: two more console scripts to
   discover, and `sk` already is the operator surface.
3. **Operator subcommands on `sk`, dispatching before any Toolkit build (chosen):**
   `sk wire` and `sk doctor` are stdlib-only, instant, and never registered - the
   registry stays exactly what the host is allowed to see, and the doctor's wiring scan
   is a read the operator makes, not a capability the agent holds.

## Consequences

- The agent cannot wire hosts, read host configs, or run the probe. A human (or the
  operator loop, shelling out) runs `sk wire` / `sk doctor` directly.
- `sk wire`/`sk doctor` must stay dependency-free (ADR-0001 discipline extends to the
  onboarding path); `enforced by tests/test_core_purity.py`, which runs both commands in
  a `-S` interpreter with site-packages hidden.
- The doctor's introspection build uses a scratch state dir, so diagnosing cannot create
  the operator's state as a side effect; `--fix` is the only writing path and reports
  each repair.
- Observable check: `tests/test_doctor.py::test_doctor_is_healthy_end_to_end_on_a_fresh_workspace`
  asserts a doctor run creates no `state` dir in the workspace it examined; `wire`'s
  project mode refuses hosts without a project-scope config rather than falling back to
  the user's home.
