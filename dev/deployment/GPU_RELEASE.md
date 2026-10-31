# GPU trial preparation

`python -m deployment.gpu_release plan` is offline. `prepare` explicitly contacts
only the GPU target from `.private/server-access.local.json`, using pinned
`.private/known_hosts` and one-use password authentication. It installs nothing.

Create a deployment packet with `deployment.prepare` before this workflow. Its
trusted release-manifest SHA256 must come from the independent packet preparation
record, never from untrusted remote output. The packet must specify CUDA, GPU
role, HTTPS health/readiness on the same port, exposed TLS binding, and:

- A fresh direct child of the SSH user's home named `b-gpu-trial-<32 lowercase hex>`.
- `env_file` equal to `<trial>/private.env`.
- `tls_ca_file` equal to `<trial>/server.pem`.
- A separate application release root outside the private environment directory.

From `dev`, using the approved Anaconda py312 interpreter:

```sh
python -m deployment.gpu_release plan --packet /absolute/packet --expected-manifest-sha256 TRUSTED_SHA256
python -m deployment.gpu_release prepare --packet /absolute/packet --expected-manifest-sha256 TRUSTED_SHA256 --output /absolute/new-private-output
```

Preparation refuses active NVIDIA compute processes, reserves the new private
directory exclusively, streams the archive with native SFTP-based `scp`, checks
its digest remotely, and calls the existing guarded `model_stage.apply`.
It creates a seven-day self-signed trial trust anchor/server certificate with
SANs for `127.0.0.1` and the configured private IP. No global trust is changed.
The private environment and key stay on the GPU host. The local output contains
the public certificate, a recovery copy of the random detector token, and
nonsecret preparation metadata; directory/file modes are 0700/0600.

Preparation does **not** activate an application or load a model. Use the existing
`deployment --role gpu --packet ... --expected-manifest-sha256 ...` commands for
`preflight`, `deploy`, and `verify`, with the same immutable packet. Recheck GPU
ownership immediately before activation: the preparation guard is a snapshot,
not a GPU reservation. Coordinate with A before any model startup.

Partial local/remote artifacts are retained on failure. Existing trial roots and
local output paths are never reused or overwritten. Investigate the retained
state; create a new independently prepared packet and fresh trial directory for
a new attempt. No deletion, rollback, firewall or service mutation occurs here.

Local tests mock SSH/SCP and execute the reservation guard against temporary
directories. The prepare body also runs local OpenSSL, loads its actual certificate
and key, parses the generated environment, and exercises the existing archive and
release metadata validators. Linux model publication is stubbed in that test.
These checks do not establish remote OpenSSL compatibility, a real
large transfer, CUDA model startup, API readiness, or model quality. Trial CA
expiry and explicit service rollback remain operator responsibilities.
