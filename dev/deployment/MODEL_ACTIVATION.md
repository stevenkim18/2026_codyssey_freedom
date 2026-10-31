# Completed-model activation

`model_activate.py` is the explicit publish hook for a completed model export.
It does not watch training checkpoints, train, download, load weights itself,
or modify A's training environment. The training/export pipeline can invoke it
after publishing a complete immutable package and handing over its manifest hash.
One invocation verifies the package, changes the B service package setting,
restarts the fixed B user service, and verifies its response automatically.

Use the B service Python environment with the installed application directory on
`PYTHONPATH`, so `detector.runtime` and `detector.schemas` are importable. The script
otherwise uses Python standard-library modules. The service must already exist as
`codyssey-pii-gpu.service`, and a previous valid package must remain available.

```sh
python model_activate.py \
  --candidate /home/USER/b-models/COMPLETED_PACKAGE \
  --root /home/USER/b-private \
  --env-file /home/USER/b-private/service.env \
  --manifest-sha256 EXPECTED_64_CHARACTER_SHA256 \
  --origin https://127.0.0.1:8766 \
  --ca-file /home/USER/b-tls/ca.pem \
  --allow-engineering-test
```

The root must be a real user-owned directory with mode 0700 outside the installed
application. The environment file must be a single-link user-owned regular file
with mode 0600 directly inside that root. It uses unquoted `KEY=value` ASCII lines,
without whitespace in values. It already contains `PII_MODEL_PACKAGE` and
`PII_DETECTOR_TOKEN`; no token is passed on the command line or printed. Other
environment lines are preserved byte-for-byte.

The fixed unit must declare exactly this environment file, mandatory, without
additional EnvironmentFiles. The hook checks `systemctl --user show` before
mutation and again after restart. It verifies the live MainPID's
`PII_MODEL_PACKAGE` through `/proc/PID/environ` without printing any environment
data, then rechecks MainPID to reject a process change during verification.
After each successful HTTP probe (including idempotence and rollback), it also
matches the configured IPv4 LISTEN address/port in `/proc/net/tcp` against socket
inodes in MainPID's `/proc/PID/fd`. A different process answering on the requested
port cannot satisfy activation. Only IPv4 loopback or wildcard binding is supported.
The HTTPS origin is restricted to `127.0.0.1` and the environment's
`PII_DETECTOR_PORT` (default 8766). The certificate therefore needs a 127.0.0.1 IP
SAN. An arbitrary endpoint cannot receive the detector token or simulate success.

The caller must provide the accepted export's manifest SHA-256 through a trusted
handoff channel. Full ModelPackage validation checks exact files, hashes, declared
artifacts, pinned source revision, mappings, thresholds, and preprocessing. It does
not prove quality or pre-load loader compatibility. The real service validates
loading and warmup after restart; any restart or readiness/inspection failure
triggers rollback. Keep completed package directories immutable during activation
and service startup. A's partial checkpoints are never activation candidates.

Current support is explicitly engineering-only: `--allow-engineering-test` is
required and successful output includes `quality_approved: false`. No quality
approval schema is invented and no A quality approval is inferred. A production
quality approval workflow remains a separate handoff requirement.

A private nonblocking `flock` serializes operations within the root. The previous
environment is retained as `previous.env` for the first activation; subsequent
activations preserve additional `previous-UUID.env` snapshots rather than deleting
prior evidence. Atomic writes use private staged files, fsync, and rename. Failed
staged files remain private for investigation. Never manually choose the oldest
`previous.env` as a replacement for the active transaction's newer snapshot.

Verification uses a dedicated CA with hostname and certificate verification,
disabled environment proxies, and refused redirects. Health and readiness are
polled for up to 180 seconds (individual HTTP timeout 10 seconds). One authenticated
public synthetic inspection must complete with the expected model and threshold
versions. A timeout does not cancel native inference work; systemd service shutdown
and restart policies must manage process draining. This hook does not alter them.

On failure the exact prior environment is restored, the old service is restarted,
and its old model identity is verified. Output uses fixed error codes and reports
`rollback: success` or `rollback: failure`. A rollback failure requires operator
attention; it is never reported as successful activation. Repeating an already
active candidate verifies its live identity and does not restart it.

During a transaction, SIGINT, SIGTERM, and KeyboardInterrupt trigger the same
rollback. Further SIGINT/SIGTERM requests are deferred while restoration and
verification finish; original handlers are restored afterward. The interrupted
invocation reports `activation_interrupted` and exits unsuccessfully. Invoke the
hook from the main thread. SIGKILL, host loss, and power failure cannot be handled
by Python and require operator recovery from the preserved environment snapshots.

Synthetic local tests cover success, idempotence, integrity rejection before
restart, mandatory engineering permission, restart/probe failure, restoration
failure, lock contention, partial atomic writes, and HTTP identity/header handling.
These tests are not evidence of a real GPU deployment or model quality.
