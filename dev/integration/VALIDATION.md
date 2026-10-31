# Independent security validation — 2026-10-31

This review examined B-owned code against PRD R01–R08/R10–R12, especially
T03–T08/T10/T11. It does not establish completion of the product or GPU deployment.
The reviewer changed no application source or tests. A separate implementation
worker repaired the confirmed defect below; the reviewer then independently
repeated its original reproduction in a fresh Python process.

## Confirmed defect and repair verification

**Repeated cancellation released native inference ownership prematurely**
(`detector/runtime.py`, R07/R11/T07).

Before repair, `_call()` shielded a synchronous worker, then awaited that shield
only once after receiving cancellation. A second cancellation interrupted this
drain. `inspect()` consequently decremented `_active` and set `_drained` while the
native worker was still running. A model replacement could then load and warm a
new model concurrently with the old native inference. This defeats the intended
drain and can cause GPU resource contention or an out-of-memory failure. No
actual GPU failure or disclosure was demonstrated.

The independent reproduction used temporary synthetic packages, a synchronous
backend blocked on `threading.Event`, and two `task.cancel()` calls separated by
an event-loop yield. It then attempted `manager.replace(new)` while the backend
was still blocked.

| Observation while native inference remained blocked | Before repair | After repair |
| --- | --- | --- |
| Inference task finished | Yes | No |
| Active inference count | 0 | 1 |
| Replacement finished | Yes | No |
| Loaded model versions | old, new | old |
| Readiness during replacement | Ready | Not ready |

After releasing the native backend, cancellation completed and the replacement
loaded the new model successfully. The repair introduces `_settle(task)`, which
retains worker ownership across repeated cancellation. Both synchronous worker
drain and model preparation drain use it. The new implementation regression test
is `test_t06_repeated_cancel_cannot_release_native_worker_drain`.

A second independent probe blocked the synchronous new-model loader, cancelled
the replacement twice, then requested another replacement. Readiness stayed
false and the next loader did not start until the cancelled preparation actually
exited. The following replacement then completed successfully.

The runtime SHA-256 at successful independent revalidation was
`9487c9513ae238faa8ab92bb754925617e7e4dac7aaccc7bf8c51b220439362f`.

## Executed checks

All executable checks used the existing Anaconda `py312` interpreter:

```sh
rtk proxy env PYTHONPATH=dev COVERAGE_FILE=.cache/.coverage-independent \
  /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest \
  dev/gateway/tests dev/detector/tests dev/launcher/tests dev/integration/tests -q
```

Result at review time: **546 passed in 1.52 seconds**. This was a fresh process
separate from the worker that made the repair. This invocation did not enable
coverage measurement; its passing count is not a coverage percentage.

An earlier gateway/runtime-only run passed **375 tests** with `PYTHONPATH=dev`.
Without that variable, the journal process-crash test's independently launched
child could not import `gateway`; that command environment failed and was
corrected before rerunning. This was not treated as a product defect.

The executable regression suite and source review covered:

- Inspection of supported request instructions and conversation text; rejection
  of stored-history references, unsupported tools/modalities, malformed fields,
  invalid Unicode, duplicate JSON fields, and unsupported encodings.
- Complete-response buffering and reconstruction from accepted text; stripping
  arbitrary upstream IDs, metadata, annotations, URLs, and raw errors.
- Block/mask/allow precedence, Unicode codepoint spans, mask reinspection, and
  consistency of model and threshold versions throughout a request.
- Cancellation, disconnect, expired deadlines, admission limits, and failure at
  each journal stage before client delivery.
- Exact production upstream destination, credential validation, authenticated
  inspection transport, disabled redirects/retries, and bounded response bodies.
- Durable journal metadata validation, process-crash recovery, capacity behavior,
  and an unknown delivery record before upstream dispatch.
- Verified model-package loading, warmup/readiness, drain-before-replacement,
  model/API generation consistency, and rejection of backend text mutation.
- Dedicated launcher profile verification, mandatory command-line routing
  settings, configuration tampering, and provider override rejection.

No additional reproducible bypass or raw-content disclosure was confirmed in
these reviewed paths. This statement is limited to the inspected code and
synthetic cases; it is not proof that all sensitive text is detectable.

## Final production-entrypoint review

The final scope additionally included `gateway/settings.py`,
`gateway/server.py`, `gateway/__main__.py`, and the launcher's canonical profile
verification and mandatory `-c` routing. Source inspection confirmed that the
entrypoint binds to `127.0.0.1`, disables access logs, constructs separate HTTP
clients with `verify=True` and `trust_env=False`, and uses the exact fixed OpenAI
Responses URL. Settings reject provider/binding/TLS environment overrides and
exclude credential fields from their generated representation. Canonical profile
verification precedes generation of explicit provider, local URL, token-variable,
retry, and tool-feature command-line settings. No new confirmed security defect
was found in this additional scope.

A fresh process with a cleared environment independently exercised the actual
Python module entrypoint:

```sh
rtk proxy env -i PYTHONPATH=dev \
  /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m gateway
```

Observed result: exit code **2**, empty stdout, and only the fixed stderr line
`Gateway startup rejected: invalid or unavailable secure configuration.` No
server/network call or real configuration was involved.

The complete selected test command above was rerun after the final additions,
using `.cache/.coverage-independent-final` as the separate coverage destination:
**646 passed in 1.91 seconds**. Coverage collection was not enabled by this
independent command. Parent-reported coverage and actual CLI/TCP runs are
separate evidence and were not converted into independent verification claims.

The following final fingerprints delimit the reviewed sources. They remained
unchanged across a further fresh-process settings/server/launcher test run
(**167 passed in 0.50 seconds**). The
runtime fingerprint also matches the earlier repaired-runtime review; the other
four files were first fingerprinted during this final review.

| Source | SHA-256 |
| --- | --- |
| `dev/gateway/settings.py` | `c0ee0193092228f55b05868be3a37718cc6a2115cbc9b40193207952aed9916b` |
| `dev/gateway/server.py` | `e8a8754a9423abd89be06b75ac65aea57cc04c8f1fd5d195e50dfdacefa03557` |
| `dev/gateway/__main__.py` | `540fd0a5992ca535482a9d44b2e268ce3a39bb47a0576f301214afa5ef4494b9` |
| `dev/launcher/profiles.py` | `61c725a3eeb230580a024931e1e33e81d6638d20dc302b1180be9dd36aa38aeb` |
| `dev/detector/runtime.py` | `9487c9513ae238faa8ab92bb754925617e7e4dac7aaccc7bf8c51b220439362f` |

## Remaining limits

- The actual Codex CLI protocol gate remains incomplete. The pinned CLI's
  unsupported fields/tools and request size require a compatible adapter or an
  explicitly documented scope change. Synthetic HTTP success and launcher unit
  tests do not establish G0/T11 compatibility.
- This reviewer did not invoke the actual CLI or use the external OpenAI API.
  Parent-run CLI/TCP evidence, if reported elsewhere, is separate evidence.
- M0 model weights, real inference, GPU execution, performance targets, deployed
  TLS/authentication, and server-to-server behavior were not validated here.
- The lifecycle/API tests use injected synthetic backends. They establish the
  exercised state transitions, not model accuracy or production memory behavior.
- C-owned event ingestion, dashboard rendering/access control, and end-to-end
  event retransmission/recovery were outside this independent review.
- No packages were installed, real user credentials/configuration were read,
  or commits/pushes were performed by the reviewer.
