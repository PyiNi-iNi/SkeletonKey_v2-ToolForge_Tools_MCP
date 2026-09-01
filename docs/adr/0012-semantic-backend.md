# ADR 0012: the semantic routing backend is a zero-dependency deterministic scorer

Date: 2026-09-01
Status: accepted
Affects: `core/semantic.py`, `core/registry.py` (`route`), `core/config.py`
(`tools.semantic`), `pyproject.toml` (entry-point group `skeletonkey.semantic`),
`tests/test_semantic.py`, `tests/test_discovery.py`

## Context

P5a shipped the router as a two-stage contract: exact name, then a deterministic lexical
ranking, then an *optional* `SemanticBackend` (entry-point group `skeletonkey.semantic`)
that the core deliberately does not ship. P5b must choose the backend, and AC2 stops being
a statement about absence the moment `route(semantic=True)` has a real stage to run.

The constraints on the choice:

- **ADR-0001: zero mandatory dependencies.** A torch / onnxruntime dependency is
  permissible only as part of an optional extra.
- **Windows + Linux + macOS first-class.** A model that downloads weights, needs
  `libgomp`/onnxruntime builds per-platform, or behaves differently on NT, costs the
  Windows story that both P2 and P6 depend on.
- **Deterministic and replayable.** `docs/adr/0009` proves turns by replaying them; a
  backend whose ranking depends on a model version `pip` happened to install breaks the
  proof. The router should already record the decision; the backend must be stable for a
  given package build.
- **Offline.** Agent sandboxes routinely have no network; no feature may depend on a
  fetch at first use.

## Options considered

1. **Pure-Python TF-IDF + character n-grams, shipped in core (chosen).** A ~60-line
   deterministic scorer: lowercase tokens with word + char-bigram features, IDF weighting
   over the registered corpus, cosine similarity, no external data. Registered under the
   very entry-point group the protocol defines, so a *pipelined* dist discovers it
   exactly as it would a third-party backend (and a dev checkout resolves it directly —
   the entry-point path is the installed-dist form of the same thing, not a second
   implementation).
   - Pros: zero deps, offline, Windows-safe, deterministic, instantly testable; a heavier
     backend can still be added later through the unchanged protocol without touching the
     router or the contract.
   - Cons: vocabulary-overlap semantics only — paraphrase wins are weak (no embeddings).
   ACCEPTED: for tool *names/capabilities/descriptions*, overlap is precisely the signal
   that matters, and the eval suite is the arbiter (25/25 preservation asserted).

2. **`fastembed` / ONNX runtime extra.** Stronger semantics (a real embedding model).
   - Cons: `onnxruntime` + model download (~100 MB), platform-specific wheels, slower
     cold start, and the AC2 bar is *no outcome change* — the stronger stage must first
     prove it does not *hurt*; it stays an option behind the same protocol instead of the
     default.

3. **Defer semantics entirely (no backend, AC2 stays as an absence claim).** Rejected:
   the session decision was to make the two-stage comparison real, not to keep a
   placeholder.

## Decision

Ship `LexicalSemantic` (name `lexical-tfidf`) in `core/semantic.py`. It is always
importable (zero deps) but **inactive unless `tools.semantic = true`** — the routing
default stays the pure lexical path, so no existing host sees a ranking change. When
active, `route` blends the normalized lexical score and the semantic cosine 50/50 and
keeps the deterministic id tie-break; each candidate carries `semantic_score` and the
response reports `mode: "semantic"`, `backend: "lexical-tfidf"`.

## Consequences

- `registry.route(semantic=True)` is now a genuine two-stage comparison; AC2 is asserted
  with the backend on: eval-suite hit-rate identical to lexical (25/25 @ k=5) **and**
  reordering observed on the suite (a stage that never changes an order would be a
  no-op, and a stage that changes outcomes would be a regression — both are asserted
  against).
- `tools.semantic = false` (default) keeps the previously shipped behavior byte-identical;
  the honest `note` for "no backend installed" disappears from the *shipped* state only
  because a backend now exists — the code path remains for exotic installs (e.g. the
  extra was uninstalled) and still says `mode: "lexical"` if it happens.
- A future embedding backend registers under `skeletonkey.semantic`; `route` picks the
  first backend by name (deterministic), and discovery reports `load_errors` instead of
  silently degrading — the router never guesses.
- Semantic quality is intentionally modest; the receipt discipline (reasons, scores,
  mode) means the ranking is auditable, which matters more than a fancy model that cannot
  be explained.
