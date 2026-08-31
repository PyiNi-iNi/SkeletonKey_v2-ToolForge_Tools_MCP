# ADR-0004 — Hand-rolled JSON-Schema subset instead of `jsonschema`

- **Status:** accepted, monitored
- **Date:** 2026-08
- **Deciders:** Dime

## Context

Every tool call is validated against the manifest's `input_schema` before a handler runs,
and the *error* is a contract: `BAD_ARGS` must name the offending path, the expected type,
and carry a runnable `minimal_example`. The same schema is forwarded verbatim to MCP hosts
as `inputSchema`.

`jsonschema` would give us ~95 % of the vocabulary. It would also give us:

- exceptions designed for humans reading a traceback, not for a model that must repair the
  call — `best_match` exists but the message still reads like a validator log;
- a hard dependency in the layer that must run everywhere (ADR-0001), including inside the
  `dropin`/`skill` loading path where importing a third-party module is a trust question;
- silent permissiveness we would have to fight: our manifests use `"additionalProperties":
  false` as a *safety* property (an agent's typo'd key must be an error, not ignored), and
  the libraries differ on formats, `default` handling, and whether `$schema` changes
  behaviour;
- and `jsonschema` does not know our `format` registry (`path`, `glob`, `duration`,
  `sha256`), which is exactly where our path-handling policy lives.

The vocabulary our 31 manifests actually use is small and closed: `type` (incl. unions),
`properties`, `required`, `additionalProperties`, `items`, `enum`, `const`,
`anyOf`/`oneOf`/`allOf`, `pattern`, `min/max(imum|Length|Items)`, `uniqueItems`,
`default`, `format`, `description`. Nullability is written as
`type: ["string", "null"]`, not the `nullable` extension keyword.

## Decision

`core/validate.py` implements that subset — about 300 lines, no dependencies — and three
things the library does not do for us:

1. **apply defaults during validation**, so handlers see complete args and the schema is
   the single source of truth for defaults (mirrored to MCP hosts, which apply defaults
   themselves);
2. **structured, path-addressed errors**: each entry is
   `{"path": "edits[0].old_text", "message": "required property 'old_text' is missing",
   "keyword": "required", "missing": "old_text"}` (the engine drops `keyword` before the host
   sees it, and `MISSING_ARG` re-raises the single-entry case as
   `details.at`/`details.missing` because that is the branch an agent acts on), plus
   `minimal_example` synthesised from the schema when the caller sent nothing usable;
3. **a refusal to guess about the schema itself**: `format` is advisory in JSON Schema,
   and deliberately *not* here for the seven we implement (`date-time`, `date`, `uuid`,
   `ipv4`, `ipv6`, `uri`, `regex`) — a malformed regex or timestamp is caught before a
   handler crashes on it. Unknown `format` names are ignored, per spec. But a schema we
   cannot **honour** is refused at registration: `check_schema` rejects `$ref` ("inline the
   subschema"), unknown keywords, and unknown `type`s, and `ToolManifest.validate()` raises
   with `details.problems`. A validator that quietly accepts what it does not understand is
   how a `deny` list becomes advice.

`_check_format` is the extension point; `path`/`glob`/`duration`/`sha256` are implemented
and each is a test case.

## Rejected alternatives

- **`jsonschema` as an extra, subset as fallback.** Two validation semantics for the price
  of one, and the interesting bugs live exactly where they differ.
- **pydantic models per tool.** We already generate schemas from manifests for MCP; hand
  writing models too means two contracts that drift. (If P2's synthesized tools ever need
  real models, that is the moment to revisit — for a subprocess contract, a schema suffices.)
- **Trust the host to validate.** MCP hosts *do* validate, against the same schema; but our
  own `sk`, drop-in tools, and skill-authored tools would then behave differently from the
  server, which is a support nightmare with no user-visible upside.

## Consequences

- (+) `MISSING_ARG` vs `BAD_ARGS` vs unexpected-key are *our* taxonomy; agents recover from
  it (see the recovery-rate metric in `PLAN.md` §6).
- (+) Zero imports at validation time, so validation still works in a `--read-only`/
  minimal bootstrap environment.
- (−) We own a validator. Mitigated by (a) the closed keyword list, (b) ~40 direct tests
  including "unknown keyword is refused", and (c) the manifest schema in
  `schemas/tool-manifest.schema.json` documenting what authors may write.
- (−) `$ref`/`$defs` are unsupported; every manifest is authored self-contained, and the
  loader reports a schema that uses them rather than accepting it half-way.

## Verification

`tests/test_core_contracts.py` — validator semantics, path-addressed errors, defaults,
combinators, `format` enforcement (including that an enforced format actually fails a
bad value), and `check_schema` refusing a `$ref` — plus `tests/test_engine_policy.py` for
the `BAD_ARGS` envelope with its runnable `minimal_example`.
