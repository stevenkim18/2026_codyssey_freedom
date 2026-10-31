# Model asset staging (local preparation only)

`model_stage.py` is a standalone Python 3.12 standard-library tool. It validates
and stages bytes without importing model libraries, contacting a network,
loading weights, starting an API, or using a GPU. This document does not authorize
server access. The GPU server remains handed off to A; a future upload/apply
requires explicit user authorization and coordination with A.

Use the script from the same reviewed repository revision as the release packet.
Before transfer, run `python -m deployment.prepare verify --output PACKET`
from `dev` using the approved Python environment against the complete packet and
retain the trusted release-manifest digest separately. Archive hashes establish
consistency, not publisher identity: a substituted manifest and archive can agree.
The standalone stager validates model metadata and model assets, not application
code, TLS configuration, or the contents of the other packet files.

After separately authorized transfer, the future operator can inspect the packet
without creating files (replace the Python and packet paths):

```sh
"$HOME/miniforge3/envs/py312/bin/python" -B model_stage.py \
  --archive /absolute/private-packet/model-assets.tar \
  --metadata /absolute/private-packet/release-manifest.json
```

PLAN is the default. APPLY additionally requires Linux, the exact hostname, and a
new private model root beneath the invoking user's real home. Existing parent
directories must be owned by that user and not writable by group or others.
The root itself must be mode `0700`; an existing root must have this tool's valid
ownership marker. Do not reuse another worker's directory. For a future approved
apply, the operator supplies the separately verified hostname explicitly:

```sh
"$HOME/miniforge3/envs/py312/bin/python" -B model_stage.py \
  --archive /absolute/private-packet/model-assets.tar \
  --metadata /absolute/private-packet/release-manifest.json \
  --apply --root "$HOME/codyssey-model-assets" --expected-host VERIFIED_HOSTNAME
```

The returned `model_path` is a directory named by the embedded manifest SHA-256.
It can later be reviewed as the deployment model path. Staging does not change
any running service or deployment configuration. Keep previous model versions for
rollback; selecting a previous version is a separate deployment action.

Validation rejects duplicate JSON keys, unsafe names, links, hardlinked inputs,
symlink ancestors, unexpected or missing members, invalid hashes, and non-USTAR
extension headers. Archives are limited to 2 GiB and 1,000 regular files; metadata
and the model manifest are limited to 1 MiB. The exact embedded manifest file map
must match release metadata. Nested safe ASCII paths are supported.

APPLY holds a per-root advisory lock, writes a unique `stage-<uuid>` directory
with `0700` directories and exclusive `0600` files, rehashes it, and publishes
with atomic no-replace rename. A matching existing version returns
`already_present` without writes. Changed existing versions are rejected.
Failures retain the marker, lock file and partial staging directory for review;
the tool has no delete or overwrite operation. A retry creates a different stage.
Do not remove preserved contents without a separate authorized cleanup review.

The trust boundary is a private home and cooperating processes under the operator
UID. Advisory locks do not defend against a malicious process with the same UID
or root privileges. Descriptor-based ancestor traversal and exclusive file opens
reject symlink substitution; native no-replace publication preserves even a late
empty target collision. Missing native atomic support fails closed. Linux uses
`renameat2(RENAME_NOREPLACE)`; local macOS fixture tests use
`renameatx_np(RENAME_EXCL)` only through injected test identities.

Verification scope: Anaconda py312 synthetic temporary-directory tests cover
PLAN, apply, idempotence, corruption, links, host/root guards, lock contention,
interrupted writes, and collision preservation. A fresh process exercises
standalone PLAN. A separate non-root, network-disabled, read-only-root local
container (Debian 13/aarch64, Python 3.12.14) also exercised the actual standalone
CLI PLAN/APPLY, repeat no-op, tamper rejection, host guard, and native Linux
`renameat2` publication/collision preservation. That container used the stager
before the subsequent argument-error redaction change; the final CLI parser is
covered by the Anaconda tests. This is supplemental local Linux evidence, not
Ubuntu 24.04/x86_64 or target-server verification. No real server apply, model
load, GPU inference, training, quality evaluation, or production performance is
claimed.
