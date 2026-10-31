# M0 runtime evidence

From `dev`, run `python -m integration.verify_m0_runtime PACKAGE --device cpu --plan`
to print the fixed two-case budget. Plan mode does not read the package, import the
model runtime, create files, contact networks, or execute inference. Use the approved
Anaconda `py312` Python executable. Execution remains opt-in by omitting `--plan`
and supplying a new `--output` path.

The plan is case bounded, not a hard time or memory sandbox. Each inspection keeps
the production 10-second API deadline; startup has no deadline, and native worker
work can outlive an API timeout. Actual weight execution requires separate resource
review and external process supervision.

Evidence keeps public API durations separate from the existing detector timing
observer. The observer wraps each ASGI request; its stage durations are host wall
intervals, and model-call duration includes wrapper work. They are not GPU kernel
measurements. No new detector timing instrumentation or API fields are introduced.

Startup includes loading and normal warmup. Warmup duration is explicitly null:
there is no independently observed warmup duration. Planned and executed inspection
counts are recorded; window counts are null because the observer does not expose
planned/executed window counts.

RSS snapshots are process lifetime high-water marks, before torch import and after
execution. macOS native units are bytes; Linux native units are KiB and are also
normalized to bytes. Unknown platforms retain native values with null normalized
bytes. Neither snapshot is current RSS, and their difference is not model memory.
The legacy `max_rss_native`, `startup_seconds`, and public timing fields remain.

Synthetic tests establish exporter behavior only. They do not establish actual
model quality, GPU operation, production latency, TLS, or deployed-server readiness.
