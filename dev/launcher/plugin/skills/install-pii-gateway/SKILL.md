---
name: install-pii-gateway
description: Install, configure, or diagnose the standalone macOS arm64 PII Gateway client from a trusted release archive. This skill does not process sensitive prompts in the current chat.
---

Install the bundled client using [the installer](../../scripts/install-macos.sh).
The distribution includes Python and gateway dependencies. Do not run pip,
install Python, install Codex automatically, or download model weights.

Obtain the macOS arm64 release archive and its SHA-256 from the trusted release
metadata. The checksum must not be derived from an untrusted download as a
substitute for verification. A repository/plugin installation supplies this
installer; it does not prove that a binary release has been published.
If release metadata or the archive is unavailable, report that prerequisite.

Run `/bin/bash INSTALLER --archive ABSOLUTE_ARCHIVE --sha256 TRUSTED_SHA256`
or use `--url HTTPS_RELEASE_URL` instead of `--archive`. Optional
`--destination ABSOLUTE_DIRECTORY` selects a fresh install location. The default
is `~/Library/Application Support/AI PII Gateway`. Existing destinations are
preserved; no sudo, profile replacement, or host package installation is needed.

Verify from a fresh process with `DESTINATION/pii-gateway version` and `--help`.
Check platform, resolved executable, expected version, and the actual installed
path rather than relying on the installer exit code. Report which checks ran.

Have the user run `DESTINATION/pii-gateway configure` directly in their terminal.
Never request or insert detector/OpenAI credentials into this chat, command-line
arguments, logs, or plugin settings. Configuration prompts hide both credentials
and create a private settings file. The detector must be an HTTPS origin; a
private CA may be supplied as an absolute PEM path. Model defaults to
`gpt-6.1-sol`, which does not constitute a provider-availability claim.

Then use the executable's `install`, foreground `serve`, and `doctor --probe`
commands. Install requires the separately available pinned Codex CLI version;
follow the executable's diagnosis rather than substituting another CLI version.
The default protected profile is `~/.local/share/ai-pii-gateway/codex-home`.
Use `--settings ABSOLUTE_JSON` before any command for a nondefault configuration.
`uninstall` restores the owned Codex profile and does not delete settings or the
binary. It works without a Codex executable when the settings remain valid.

Sensitive prompts must be entered by the user into the actual protected command
`DESTINATION/pii-gateway run -- exec ...`, while `serve` is running. Do not accept,
transform, or forward a sensitive prompt through the outer assistant chat. This
plugin does not intercept the host assistant's requests. Doctor's local probe
does not verify remote detector quality, provider connectivity, or central events.
