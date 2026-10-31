# Inspection service: synthetic development gate

This implements the B-owned API skeleton for R03/R06/R10/R11 with explicit
injected backends and a verified local M0 loading adapter. There is no fake
production fallback or automatic weight download. Actual M0/GPU validation is
still pending. Factory usage:

```python
from detector.app import create_app

app = create_app(
    backend=loaded_and_warmed_backend,
    token=inspection_server_token,
    model_version="locked-model-version",
    threshold_version="locked-threshold-version",
)
```

The backend receives `list[detector.schemas.Segment]` and returns either a list
of findings (strict dicts or `Finding` instances), or `BackendResult` with
`status="complete"` / `status="incomplete"`. It must inspect all original segments
without silent token truncation or normalization. A complete empty result is
valid only after full inspection. The API validates labels and original-text
span bounds, merges shared secret rules, and returns codepoint offsets.
Model instances are revalidated to reject unsafe constructed/mutated findings.

An ASGI boundary authenticates before receiving or parsing the request body.
Only `GET /healthz` and `GET /readyz` are public. Unauthorized requests consume
zero ASGI body messages. Empty or malformed configured tokens fail at startup;
omitting the token leaves inspection unavailable. Authenticated inspection
requests require JSON and identity content encoding. A declared body over one
MiB is rejected before receipt; streamed bodies stop at the first chunk that
would cross that limit. Duplicate framing/auth headers, invalid content lengths,
length mismatches, duplicate JSON keys, NaN/Infinity, overflowing floats, invalid
UTF-8, and JSON nesting deeper than 32 are rejected with content-free errors.
The one MiB limit bounds the wire body per request; parsed-object overhead is
additional. The independent 16,000-codepoint aggregate text limit still applies.

Defaults are one inference execution, eight waiting requests, two seconds queue
wait and ten seconds total queue-plus-inference budget. Timeout/cancellation
requests backend cancellation and retains the execution slot until actual
termination. Backend CPU/GPU work must not block the asyncio loop; executor
isolation and worker-process termination are part of the real-model gate.
`model_version` / `threshold_version` must be configured before readiness.
The service does not prove those labels correspond to loaded artifacts: the
production loader must verify the package, warm up, and then construct the app.

`detector.runtime.ModelManager` implements verified local package loading,
warmup, request draining, replacement and rollback using an injected loader.
It connects to the API through the alternative `manager` argument:

```python
from detector.runtime import ModelManager

manager = ModelManager(local_only_model_loader)
# In async startup, initialize the verified local package before accepting work.
await manager.initialize(package_directory)
app = create_app(manager=manager, token=inspection_server_token)
```

Manager mode derives readiness and model/threshold versions dynamically. Do not
combine it with `backend` or manual version overrides. Swapping marks readiness
false and rejects new requests with 503, drains existing inference, then loads,
warms and verifies the replacement. A failed or cancelled replacement restores
the previous backend and versions. In-flight responses preserve the versions
captured with the backend that actually inspected them, including when another
model commits before response assembly. `inspect_with_metadata()` returns that
result/version snapshot; `inspect()` retains its existing result-only interface.
Backend mutation of supplied segment text or IDs is rejected.

Cancellation cannot stop a native inference thread. Repeated cancellation keeps
the request/prepare drain occupied until that worker actually exits; readiness
does not return early. Synthetic runtime and API tests cover this behavior,
warmup and version rollback. The local M0 adapter described below is implemented;
actual weights and GPU behavior remain unverified. The manager never downloads
weights itself.

## Local M0 driver and process entrypoint

`gliner_backend.load_gliner()` requires a fully hashed local M0 package at the
locked revision and `gliner==0.2.29`. It rejects remote-code configuration, requires
bundled tokenizer metadata and uses local-only loading. Real cold-cache loading,
backbone/tokenizer dependencies, label quality and GPU resource use are separate
gates; fake model tests do not establish them.

The driver inspects original-codepoint windows with model-span overlap and checks
label-prompt subtoken coverage before inference. It plans the whole request first.
Prefix-fit search is bounded by a shared 2,048-check budget; exceeding that budget
returns `incomplete` with zero model calls. An accepted window is individually
validated, so nonmonotonic tokenizer behavior can conservatively reject input but
cannot authorize silently truncated text. This is coverage within the model's
span width, not a guarantee that all PII is detected.

From `dev`, set the three variables shown in `detector/.env.example` and run:

```sh
rtk proxy python -m detector
```

The entrypoint defaults to `127.0.0.1` and disables access logs. Configuration
errors exit safely; package/load/warmup failure leaves liveness available and
readiness at 503. There is no model download, dotenv auto-load, or fake fallback.
The default entrypoint remains HTTP loopback. Optional direct Uvicorn TLS uses
`PII_DETECTOR_TLS_CERT_FILE` and `PII_DETECTOR_TLS_KEY_FILE` together. Setting
`PII_DETECTOR_HOST=0.0.0.0` requires that TLS pair; the token remains mandatory.
Certificate/key loading and private-key ownership/mode checks precede startup.
See [GPU TLS preparation](../deployment/GPU_TLS.md) for the local-tested deployment
contract and pending operator certificate/remote verification steps. No GPU server
was changed by this implementation. See `M0_INTEGRATION_NOTES.md` for pinned primary
sources and the remaining real-model gates.

Run from `dev`:

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/bin/conda run -n py312 python -m pytest detector/tests -q
```

TDD record for this cycle:

| PRD test | Red evidence | Green evidence and scope |
|---|---|---|
| T05/T06/T07/T10 | Initial collection failed: `detector.app` absent | 28 initial API tests passed |
| T05/T06 | Added boundary tests: 400 instead of 413; constructed findings accepted; NaN/infinity/bool timeouts accepted (5 failures) | Aggregate/single text bound, strict revalidation and finite configuration passed |
| T07 | Published contract exporter absent (1 failure) | Runtime-derived JSON contract equality passed |
| T06/T07 | Numeric timing and unloaded-version readiness checks failed (2 failures) | Numeric `timing_ms`, separate breakdown, unloaded readiness block passed |
| T03 | Weak secret patterns differed from shared gateway rules (1 failure) | Typed wrapper delegates to the same rule implementation |
| T06 | Queue wait was excluded from total timeout (1 failure) | Combined deadline regression passed |
| T10 | Body parsed before authentication; oversized bodies unbounded; duplicate JSON accepted; invalid token config accepted (12 failures) | Pre-receive authentication, bounded streaming, strict JSON and token config passed |
| T07 | Manager argument unsupported; backend text mutation accepted (6 failures) | Dynamic API readiness, atomic per-request version metadata, draining, rollback and input mutation rejection passed |
| T06/T07 | Second cancellation released a still-running native worker drain (1 failure) | Repeated cancellation retains ownership until actual worker completion; independent reproduction also passed |

Use the command above for the current suite result. The inspection API checks are
synthetic ASGI tests, covering
Unicode, strict input/output, identity/version fields, auth, readiness,
overload/queue timeout, request cancellation, delayed backend cancellation,
no raw exception echo, HTTP body boundaries, and contract consistency. Separate
runtime tests cover package verification and lifecycle rollback with fake loaders.
Actual M0 loading, GPU, tokenization/window coverage, model warmup, TLS transport,
real model replacement/rollback, A's label mapping and service evaluation, real
CLI, performance and deployment are **unverified** and remain separate gates.

### R11/T12 opt-in internal stage observation

`create_app(timing_observer=callback)` optionally passes a content-free snapshot
into a trusted synchronous callback. It is disabled by default, sends no telemetry,
and changes no response fields. The callback must be fast and nonblocking; its
exceptions are swallowed without printing raw errors or changing response status.
The existing public `queue/inference/rules/total` keys remain unchanged; queue
expiry now reports actual elapsed waiting rather than zero.

Internal snapshots contain only a fixed scope/outcome, `total_ms`, and fixed
stage names. Each stage has `elapsed_ms`, `completed`, `failed`, and
`active_at_snapshot`. Completed/failed count whether the measured block exited
normally or exceptionally; a coverage failure caught inside preprocessing can
finish that block normally while the request outcome remains `incomplete`.
No text, findings, IDs, labels, tokens, paths, or exception messages are included.

- `queue`: semaphore acquisition, including queue expiry and cancellation.
- `preprocessing`: adapter input validation, word splitting, window planning and
  repeated no-truncation tokenizer coverage checks.
- `model_call`: wall time around `predict_entities`, including its internal
  tokenization, forward pass and decoding. This is **not pure forward/kernel time**;
  it does not add CUDA synchronization or alter model invocation options.
- `postprocessing`: warning/output validation, span conversion, threshold handling
  and deduplication for each model window.
- `rules`: secret rule scanning.

`total_ms` covers the request handler until its response/error/cancellation
snapshot. It excludes authentication/body parsing, transport, response transmission,
callback execution and any native work still draining after timeout. Model stages
require the GLiNER adapter; arbitrary injected backends can therefore have no such
samples. Counts of zero mean no observed block, not proof of zero-cost execution.

The collector follows ContextVar propagation through asyncio tasks and the existing
`to_thread` runtime. A lock serializes updates and the final snapshot freezes the
shared collector. Active intervals are clipped at that instant and marked active;
late thread completion cannot change the report or feed another request's metrics.
Existing slot ownership and cancellation draining are unchanged. Nested intervals
may overlap, and uninstrumented work remains, so stage sums are not forced to equal
total. These local wall observations are not p50/p95, GPU utilization, SLO evidence,
or a substitute for client-side latency and first-safe-output measurements.

`detector/tests/test_stage_timing.py` covers deterministic intervals, composite model
calls, exceptional exits, frozen late workers, cancellation, timeout and callback
isolation with synthetic backends. Real L4 measurements remain a separate gate.
