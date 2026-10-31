# Interface ownership

`inspection.schema.json` is owned by B and covers `POST /v1/inspect`.
It is generated from `detector.schemas.inspection_contract()` and locked by T07.
The schema version is `1.0`. Breaking changes require a version change and
A/B contract tests before adoption. The event contract remains owned by C;
this directory does not define or change C's event ingestion interface.
The gateway's metadata-only journal DTO is a local PRD-derived allowlist, not
evidence that C has agreed to an event schema or that event delivery is integrated.

Inspection requests contain unique segment IDs and the complete text for that
direction, totaling at most 16,000 Unicode codepoints. Offsets are measured in
the original segment's Unicode codepoints, with an exclusive end. JavaScript
UTF-16 indexes require conversion. Empty text is valid; an empty segment list
is not. Unknown keys, implicit type coercion, and unknown entity types are rejected.

Successful inspection responses echo request/inspection IDs, identify the
model and threshold versions, and return total duration as numeric `timing_ms`.
`timing_breakdown_ms` contains queue/inference/rules/total durations, also in
milliseconds. `complete` is the only usable status for policy evaluation.
`incomplete` is a distinct non-authorizing result; no partial findings are
returned. Failures never become a clean `complete` response. Request-validation
and authentication errors use the minimal `ApiError` envelope because validated
request IDs may be unavailable; admitted errors use `InspectionResponse`.

Status codes: 400 invalid JSON/schema/framing, 401 missing/invalid bearer token,
413 wire-body or text limit, 429 full queue/wait timeout, 503 unavailable/invalid backend,
504 inspection timeout. No validation input or backend error text is echoed.
Authentication precedes ASGI body consumption. The wire-body limit is one MiB;
strict UTF-8 JSON rejects duplicate fields, nonfinite numbers and nesting deeper
than 32, independently of the aggregate 16,000-codepoint text limit.
`GET /healthz` is liveness only. In direct-backend mode, `GET /readyz` requires
a backend, token and nonempty model/threshold versions other than `unloaded`.
That mode relies on the injector to load and warm its backend. In manager mode,
readiness requires the manager's dynamic ready state and a token. The production
`detector.server` connects a concrete offline GLiNER M0 loader; local CPU loading
was verified separately. No GPU inspection service is currently deployed.

The app permits one active backend execution and eight waiting inspections by
default. Timed-out/cancelled work receives cancellation; the execution slot stays
occupied until that backend actually terminates. Backends must be asynchronous
and cancellation-aware; blocking GPU adapters require separate executor isolation.
The GLiNER adapter isolates synchronous work in the manager's thread executor.
Mock checks do not prove GPU warmup, latency or model quality. Local real TLS and
pinned CLI checks have separate evidence; they do not prove remote deployment.

## Local model package contract

See [A → B model intake checklist](MODEL_HANDOFF.md) for the current evidence,
pending A/B decisions and local preparation gates; it does not approve a candidate.

`model-package.schema.json` is B-owned and generated from
`detector.runtime.package_schema()`. It is a local package intake contract for
`ModelManager`, not a completed handoff or an adopted A/B model package.
Breaking changes require a version change and A/B contract tests. A retains
ownership of training, label/threshold selection, evaluation and the candidate
package; B owns serving-side acceptance and deployment.

The package root contains `manifest.json` and exactly the artifacts declared in
its `files` map. The manifest records model repository/revision, model and
threshold versions, label mapping, thresholds, preprocessing, license identity,
and paths to weights, configuration, tokenizer, license and evaluation artifacts.
Each declared file has a SHA-256 digest. Verification rejects symlinks, traversal,
undeclared files, missing artifacts and changed digests. Label and threshold keys
must agree; secret-key rules remain separate from model labels. JSON Schema
describes the structural contract; runtime validators enforce these cross-field
and filesystem constraints as well.

The manager drains active inference, verifies a local package, invokes a supplied
loader, requires warmup, rechecks artifacts, and publishes readiness only after
success. Replacement failure restores the previous ready backend. Tests cover
these transitions with synthetic packages and loaders. The manager does not
download or implement GLiNER M0, establish model accuracy, or verify the substance
of an evaluation report. `detector.app.create_app(manager=manager, token=...)`
connects this lifecycle to API readiness and per-request model/threshold versions;
it cannot be combined with a direct backend or manual version overrides.
`inspect_with_metadata()` captures the result and versions from the backend that
actually inspected the request, including during concurrent replacement. Tests
cover that connection, drain, repeated cancellation and rollback using synthetic
loaders. Actual M0 loading, GPU warmup, A/B package adoption and performance remain
separate serving gates.
