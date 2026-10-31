# Inspection service: synthetic development gate

This implements the B-owned API skeleton for R03/R06/R10/R11 with explicit
injected asynchronous backends. There is no fake production fallback, automatic
weight download, or concrete M0 loader. Factory usage:

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
warmup and version rollback. A concrete M0 driver and actual GPU behavior remain
unverified; the manager never downloads weights itself.

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
