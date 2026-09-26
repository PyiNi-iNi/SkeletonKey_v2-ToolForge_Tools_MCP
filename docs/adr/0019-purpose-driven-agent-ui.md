# ADR 0019: Purpose-driven agent workspace is a projection of the tool contract

Date: 2026-09-25
Status: accepted
Affects: `skeletonkey/agents/`, `skeletonkey/agents/render.py`,
`skeletonkey/viz/server.py`, `skeletonkey/viz/mount.py`, `sk agents`

## Context

The repository has a rich tool contract: manifests describe capabilities,
risk, requirements, tiers, approvals, and reversibility; the registry decides
what a host may advertise; and the subagent boundary enforces a restricted
view of that engine. A flat tool catalog answers *what exists*, but not the
operator's next question:

> Which bounded autonomous agent should own this task, what is that agent for,
and which tools can it use here?

The UI must not become a second registry or a collection of buttons that
bypass the policy and journal contracts. It also needs to remain useful when a
skill, integration, or remote provider changes the live registry.

## Decision

1. **Introduce purpose profiles, not another tool registry.**
   `skeletonkey.agents.catalog` contains a small, reviewable set of
   `AgentProfile` records. A profile declares a mission, purpose, limits,
   risk note, actions, and selectors over manifest metadata (`group`, id
   prefix, or capability). It contains no handlers, schemas, or copied tool
   facts.

2. **Build the agent directory from the visualizer model.**
   `build_agent_model()` first calls `build_viz_model()` and then projects its
   `VizNode` records into agent assignments. The UI/API carries the live tool
   digest and counts every registered tool. An unassigned tool is shown as a
   coverage failure rather than silently assigned to a generic role. Overlap
   is allowed: an inspection tool may be useful to both an analyst and a
   maintainer.

3. **Purpose determines delegation; the engine determines access.**
   `create_agent(engine, agent_id)` turns the selected profile into the
   existing `SubagentSpec` boundary with an allowlist, tier, read-only mode,
   call budget, wall-time limit, output limit, and audit tag. Actual calls
   continue through `call_as_subagent()` and therefore retain host gating,
   approvals, budgets, filesystem roots, argv handling, journaling, and the
   parent ledger. The profile is not an authority escalation.

4. **The workspace is read-only.**
   `sk agents`, `/agents`, and `/api/agents` show purpose, actions, status,
   limits, and tool IDs. They do not accept a task or execute a tool. The
   action cards are delegation recipes, not POST buttons. The UI links to the
   tool catalog and is standalone, dependency-free, and safe to open from
   `file://`. Execution belongs to an application that explicitly constructs
   an agent and calls the existing runtime boundary.

5. **Coverage is explicit.**
   The default profiles cover every current tool family: workspace/registry,
   shell, sandbox, catalog/planner, integrations, live, publishing, skills,
   and policy. Tests fail when a registered tool is not mapped, so a new tool
   family requires an intentional profile decision.

6. **Serve beside the catalog.**
   The standalone visualizer serves `/agents` and `/api/agents`; the mounted
   viewport serves `/tools/agents` and `/tools/api/agents`. These are GET-only
   projections under ADR-0018's mount guard. `sk agents --serve` starts the
   same server and directs the operator to `/agents`.

## Consequences

- (+) Operators choose an agent by purpose before thinking in tool IDs.
- (+) The UI remains live as skills and integrations change; it does not drift
  from `Registry.advertise()`.
- (+) Every profile is testable as a real `SubagentSpec`, and execution keeps
  the existing safety/audit path.
- (+) All browser assets are inline and zero-dependency, consistent with
  ADR-0001 and ADR-0016.
- (−) A tool may appear under more than one role, so the UI reports unique
  coverage separately from assignment count.
- (−) Purpose selectors are intentionally coarse. A new capability may need a
  selector/profile update before it has a specialist owner.
- (−) The workspace cannot execute tasks. This is deliberate: adding an
  execution endpoint would create a new authorization surface and would blur
  ADR-0015's operator boundary.

## Rejected alternatives

- **Copy tool IDs into per-agent manifests.** Rejected: a copied list would
  drift after skill installation and violate the visualizer's projection rule.
- **Let the UI call tools directly.** Rejected: it would bypass the explicit
  `Subagent` boundary and turn a read-only catalog into an execution surface.
- **One unrestricted general agent.** Rejected: purpose, least privilege, and
  reviewable limits are more useful than a role label over the full registry.
- **Gradio as the required UI.** Rejected: the core contract is dependency-free
  and must render in locked-down agent hosts; the optional feature UI remains
  available for model chat, but this operational workspace is stdlib-only.
