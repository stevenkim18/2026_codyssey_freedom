# Discovered-home GPU workflow

Run from `dev` with the approved Anaconda py312 interpreter. This command uses
the existing private server access file and pinned known-hosts automatically.
It never derives a home directory from the SSH username.

```sh
python -m deployment.gpu_workflow prepare --source . --model /absolute/approved-model-package --output .private/new-gpu-trial
```

Use `deploy` instead of `prepare` to perform preparation, then the existing
deployment CLI's preflight, deploy, and verify in the same invocation. The
optional `--port` defaults to 8766. Output must be a new path with an existing
parent. This workflow is for a fresh trial, not model replacement of an active
service. Existing GPU compute prevents progress.

The first operation over SSH is read-only discovery. It checks exact hostname,
Linux, the actual home directory and its ownership, no active GPU compute, and
exactly one valid home-local py312 environment under miniforge3, anaconda3, or
miniconda3. A short isolated Python process reports version and prefix and imports
`google.protobuf` and `sentencepiece` without importing Torch or any model.
Missing runtime imports fail with `gpu_runtime_probe_failed` before model transfer;
the command never installs missing packages. Ambiguous environments fail closed.

Before creating the output, role-owned application files are bundled in memory.
The workflow safely extracts that bounded snapshot into a private frozen source
directory, re-bundles it, and requires the same application digest. The prepared
packet is a sibling of this frozen source, so an output under `dev/.private`
does not conflict with packet containment checks or accidentally include itself.
The supplied model package retains the existing preparation schema/hash checks.

The application root is `<actual-home>/codyssey-pii-gpu`. Each private trial
directory is a fresh `<actual-home>/b-gpu-trial-<UUID>` with its own environment
and seven-day trial certificate. See [GPU_RELEASE.md](GPU_RELEASE.md).

The new private output records discovery, the immutable packet's trusted manifest
digest, context, connection material, and final status. Directories/files use
0700/0600. Standard output contains fixed aggregate status only; it never prints
tokens, host addresses, home directories, or raw SSH diagnostics.

For `deploy`, GPU-idle discovery is repeated immediately before preflight and
again before deployment. These are snapshots, not a GPU lock: coordinate with A
so training cannot start concurrently. Verification deliberately does not demand
an idle GPU after the B service starts. All three deployment steps use the exact
same packet and trusted digest. The existing deployment implementation owns
service lifecycle and rollback behavior.

Before preflight or service startup, `deploy` checks the SSH user's own systemd
linger property with a single `--property=Linger`. If disabled, it runs
`loginctl enable-linger` for that exact UID and verifies the result. Permission
failure stops before service startup; no sudo or password prompt is attempted.
After successful deployment verification, it enables only
`codyssey-pii-gpu.service` and verifies `is-enabled`. This keeps the user's systemd
manager after logout and starts the B API at boot. Other units and other users'
settings are not changed. `prepare` performs neither persistence operation.

Before A starts GPU training, coordinate an explicit stop of the B API; lingering
and boot enablement mean closing SSH is no longer a way to stop inference.
For later model exports use `gpu_model_publish` with the retained deployment
context. Do not bootstrap a new trial root over an existing managed service.

Failures preserve all generated artifacts and never retry uploads, erase trial
roots, install dependencies, change firewall rules, or alter global TLS trust.
Inspect retained private context before retrying with a new output/trial. This
workflow does not provide remote model-swap administration or approve model
quality. Local tests fake SSH, GPU queries, and service operations; actual server
transfer, GPU loading, TLS/API response, and client integration remain separate
deployment evidence.

For controlled read-only use by another local orchestration tool,
`deployment.gpu_workflow.discover()` returns the private home/runtime record and
prints nothing. Treat that result as private operational configuration.
