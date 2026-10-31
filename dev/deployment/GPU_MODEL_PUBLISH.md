# Publish a completed GPU model export

This local command operates on an existing verified GPU workflow deployment.
It does not watch checkpoints, train, download, install, upload candidates, or
create an administrative HTTP endpoint. The candidate must already be a complete
remote model package with immutable identity, mappings, thresholds and evaluation
artifacts. Coordinate with A before requesting a service restart.

From `dev`, using the approved py312 interpreter:

```sh
python -m deployment.gpu_model_publish --deployment .private/verified-workflow-output --candidate /absolute/remote/completed-package --manifest-sha256 TRUSTED_CANDIDATE_MANIFEST_SHA256 --allow-engineering-test
```

The engineering flag is mandatory before private configuration or SSH is read.
The workflow context must be owned, private, and bound to its locally verified
packet and application digest. SSH uses the existing private access file and
pinned host keys; the remote interpreter is the context's verified py312 path.

The remote wrapper validates exact host/home, the managed deployment marker, the
current release's ownership boundary and full file digests using the existing
deployment verifier. Its digest must equal the original workflow application
digest. Only then does the wrapper import detector validators from that release
and invoke the trusted local `model_activate.py` source streamed over stdin.
No operator script is installed or overwritten on the server.

Activation verifies the candidate manifest digest and package, preserves private
environment recovery evidence, changes only the model package setting, restarts
the managed user service, and verifies authenticated TLS inspection/version and
process binding. Failed activation reports whether rollback succeeded. Publishing
the already-active candidate performs verification without a restart.

Standard output contains only fixed statuses (`already_active`, `activated`, or
`failed`) and bounded rollback/quality flags. Tokens, remote paths, host addresses,
raw SSH diagnostics and model errors are not printed. An `activated` result is an
engineering API verification, never approval of model quality.

Local tests use temporary contexts and fake SSH only. Actual no-op verification,
controlled replacement, failure rollback and GPU readiness require independent
server evidence. Retain workflow/activation recovery artifacts; do not delete or
overwrite them to force a retry.
