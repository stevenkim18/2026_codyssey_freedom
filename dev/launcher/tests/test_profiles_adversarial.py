"""R01/R10/T11: adversarial override and crash-safe restoration boundaries.

All paths are isolated test homes; real Codex profiles are never read.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib

import pytest

from launcher.profiles import PINNED_CLI, PROFILE, ProfileError, install, launch_arguments, uninstall


CONFIG = {"port": 8765, "model": "mock-model", "cli_version": PINNED_CLI}
ORIGINAL = b'# synthetic personal profile\nmodel = "old-model"\n'


def existing_home(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    (home / PROFILE).write_bytes(ORIGINAL)
    return home


@pytest.mark.parametrize("override", ["-cmodel_provider=evil", "-pother", "-cfeatures.shell_tool=true"])
def test_compact_provider_profile_and_feature_overrides_cannot_bypass_gateway(tmp_path, override):
    install(tmp_path, **CONFIG)
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", override, "synthetic prompt"], codex_home=tmp_path)


def test_uninstall_retries_after_original_restored_and_backup_removed(tmp_path, monkeypatch):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    real_unlink = Path.unlink
    failed = False

    def fail_manifest_once(path, *args, **kwargs):
        nonlocal failed
        if path.name == "manifest.json" and not failed:
            failed = True
            raise OSError("synthetic interruption after backup removal")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_manifest_once)
    with pytest.raises(ProfileError):
        uninstall(home)
    assert (home / PROFILE).read_bytes() == ORIGINAL
    assert not (home / ".pii-gateway-install" / "original.toml").exists()
    uninstall(home)
    assert (home / PROFILE).read_bytes() == ORIGINAL
    assert not (home / ".pii-gateway-install").exists()


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_repeated_install_cannot_claim_healthy_when_original_backup_is_unrecoverable(tmp_path, damage):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    installed = (home / PROFILE).read_bytes()
    backup = home / ".pii-gateway-install" / "original.toml"
    if damage == "missing":
        backup.unlink()
    else:
        backup.write_bytes(b"synthetic altered backup")
    with pytest.raises(ProfileError):
        install(home, **CONFIG)
    assert (home / PROFILE).read_bytes() == installed


@pytest.mark.parametrize("override", ["--config=model_provider=evil", "--config", "-c", "-c=model_provider=evil",
    "--profile=other", "--profile", "-p", "-p=other", "--oss", "--local-provider=ollama",
    "--enable=shell_tool", "--enable"])
def test_all_supported_provider_or_feature_override_spellings_are_denied(tmp_path, override):
    install(tmp_path, **CONFIG)
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", override], codex_home=tmp_path)


@pytest.mark.parametrize("version,args", [("unverified", ["exec"]), (PINNED_CLI, None),
    (PINNED_CLI, "exec"), (PINNED_CLI, ["exec", None])])
def test_invalid_launch_configuration_never_produces_unprotected_command(version, args):
    with pytest.raises(ProfileError):
        launch_arguments(version, args)


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink"])
def test_uninstall_never_replaces_installed_profile_with_unverified_backup(tmp_path, damage):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    installed = (home / PROFILE).read_bytes()
    backup = home / ".pii-gateway-install" / "original.toml"
    if damage == "changed":
        backup.write_bytes(b"synthetic untrusted restoration")
    else:
        backup.unlink()
        if damage == "symlink":
            victim = tmp_path / "victim"
            victim.write_bytes(ORIGINAL)
            backup.symlink_to(victim)
    with pytest.raises(ProfileError):
        uninstall(home)
    assert (home / PROFILE).read_bytes() == installed


@pytest.mark.parametrize("malformed", [None, [], {"schema_version": "1.0"},
    {"schema_version": "2.0", "installed_sha256": "a" * 64, "original_sha256": None},
    {"schema_version": "1.0", "installed_sha256": "INVALID", "original_sha256": None},
    {"schema_version": "1.0", "installed_sha256": "a" * 64, "original_sha256": []},
])
@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_corrupted_manifest_never_overwrites_existing_profile(tmp_path, malformed, operation):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    installed = (home / PROFILE).read_bytes()
    manifest = home / ".pii-gateway-install" / "manifest.json"
    manifest.write_text(json.dumps(malformed))
    with pytest.raises(ProfileError):
        install(home, **CONFIG) if operation == "install" else uninstall(home)
    assert (home / PROFILE).read_bytes() == installed
    assert (home / ".pii-gateway-install" / "original.toml").read_bytes() == ORIGINAL


def test_reconfiguration_and_user_changes_require_explicit_recovery_without_overwrite(tmp_path):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    installed = (home / PROFILE).read_bytes()
    with pytest.raises(ProfileError):
        install(home, **(CONFIG | {"port": 8766}))
    assert (home / PROFILE).read_bytes() == installed
    edited = b"# synthetic newer user edits\n"
    (home / PROFILE).write_bytes(edited)
    with pytest.raises(ProfileError):
        install(home, **CONFIG)
    assert (home / PROFILE).read_bytes() == edited


@pytest.mark.parametrize("original_exists", [False, True])
def test_failed_atomic_install_preserves_original_and_exact_state_can_resume(tmp_path, monkeypatch, original_exists):
    home = existing_home(tmp_path) if original_exists else tmp_path / "fresh-codex"
    real_replace = os.replace
    failed = False

    def fail_profile_replace_once(source, target):
        nonlocal failed
        if Path(target) == home / PROFILE and not failed:
            failed = True
            raise OSError("synthetic atomic replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_profile_replace_once)
    with pytest.raises(ProfileError):
        install(home, **CONFIG)
    if original_exists:
        assert (home / PROFILE).read_bytes() == ORIGINAL
        assert (home / ".pii-gateway-install" / "original.toml").read_bytes() == ORIGINAL
    else:
        assert not (home / PROFILE).exists()
    assert not list(home.glob(".pii-profile-*"))
    install(home, **CONFIG)
    assert (home / PROFILE).read_bytes() != ORIGINAL
    uninstall(home)
    if original_exists:
        assert (home / PROFILE).read_bytes() == ORIGINAL
    else:
        assert not (home / PROFILE).exists()


def test_profile_file_fsync_failure_preserves_original_and_backup(tmp_path, monkeypatch):
    home = existing_home(tmp_path)
    real_mkstemp, real_fsync = tempfile.mkstemp, os.fsync
    profile_descriptors = set()

    def track_profile_temporary(*args, **kwargs):
        descriptor, name = real_mkstemp(*args, **kwargs)
        if Path(kwargs["dir"]) == home:
            profile_descriptors.add(descriptor)
        return descriptor, name

    def fail_profile_sync(descriptor):
        if descriptor in profile_descriptors:
            raise OSError("synthetic fsync failure")
        return real_fsync(descriptor)

    with monkeypatch.context() as fault:
        fault.setattr(tempfile, "mkstemp", track_profile_temporary)
        fault.setattr(os, "fsync", fail_profile_sync)
        with pytest.raises(ProfileError):
            install(home, **CONFIG)
    assert (home / PROFILE).read_bytes() == ORIGINAL
    assert (home / ".pii-gateway-install" / "original.toml").read_bytes() == ORIGINAL
    assert not list(home.glob(".pii-profile-*"))
    install(home, **CONFIG)
    uninstall(home)
    assert (home / PROFILE).read_bytes() == ORIGINAL


def test_restore_failure_keeps_installed_profile_and_verified_backup_for_retry(tmp_path, monkeypatch):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    installed = (home / PROFILE).read_bytes()
    real_replace = os.replace

    def fail_restore(source, target):
        if Path(target) == home / PROFILE:
            raise OSError("synthetic restoration failure")
        return real_replace(source, target)

    with monkeypatch.context() as fault:
        fault.setattr(os, "replace", fail_restore)
        with pytest.raises(ProfileError):
            uninstall(home)
    assert (home / PROFILE).read_bytes() == installed
    assert (home / ".pii-gateway-install" / "original.toml").read_bytes() == ORIGINAL
    uninstall(home)
    assert (home / PROFILE).read_bytes() == ORIGINAL


@pytest.mark.parametrize("target", ["home", "state", "state_file", "profile_directory", "profile_hardlink", "manifest"])
def test_unsafe_configuration_paths_are_never_followed_or_overwritten(tmp_path, target):
    home = tmp_path / "codex"
    victim = tmp_path / "victim"
    victim.mkdir()
    marker = victim / "preserve"
    marker.write_bytes(ORIGINAL)
    if target == "home":
        home.symlink_to(victim, target_is_directory=True)
    else:
        home.mkdir()
        if target == "state":
            (home / ".pii-gateway-install").symlink_to(victim, target_is_directory=True)
        elif target == "state_file":
            (home / ".pii-gateway-install").write_text("synthetic non-directory")
        elif target == "profile_directory":
            (home / PROFILE).mkdir()
        elif target == "profile_hardlink":
            os.link(marker, home / PROFILE)
        elif target == "manifest":
            state = home / ".pii-gateway-install"
            state.mkdir()
            (state / "manifest.json").symlink_to(marker)
    with pytest.raises(ProfileError):
        install(home, **CONFIG)
    assert marker.read_bytes() == ORIGINAL
    assert not list(victim.glob(".pii-profile-*"))


def test_install_state_and_backup_permissions_are_private(tmp_path):
    home = existing_home(tmp_path)
    install(home, **CONFIG)
    state = home / ".pii-gateway-install"
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    for path in (home / PROFILE, state / "original.toml", state / "manifest.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def command_configuration(command):
    assignments = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-c"]
    return tomllib.loads("\n".join(assignments))


def test_launch_requires_verified_installation_home_and_never_uses_implicit_user_config(tmp_path):
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", "synthetic prompt"])
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", "synthetic prompt"], codex_home=tmp_path)


def test_every_protection_setting_is_explicit_when_cli_ignores_profile_config(tmp_path, monkeypatch):
    install(tmp_path, **CONFIG)
    monkeypatch.setenv("OPENAI_API_KEY", "SYNTHETIC_EXTERNAL_KEY_MUST_NOT_BE_USED")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://synthetic-external.invalid/v1")
    command = launch_arguments(PINNED_CLI, ["exec", "synthetic prompt"], codex_home=tmp_path)
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" in command
    settings = command_configuration(command)
    assert settings["model"] == "mock-model"
    assert settings["model_provider"] == "pii-gateway"
    provider = settings["model_providers"]["pii-gateway"]
    assert provider["base_url"] == "http://127.0.0.1:8765/v1"
    assert provider["env_key"] == "PII_GATEWAY_LOCAL_TOKEN"
    assert provider["wire_api"] == "responses"
    assert provider["supports_websockets"] is False
    assert provider["request_max_retries"] == provider["stream_max_retries"] == 0
    assert settings["web_search"] == "disabled"
    assert settings["features"] == {"shell_tool": False, "unified_exec": False,
                                     "apply_patch_freeform": False, "apps": False}
    assert "SYNTHETIC_EXTERNAL_KEY" not in repr(command)
    assert "synthetic-external.invalid" not in repr(command)


@pytest.mark.parametrize("damage", ["profile_changed", "profile_missing", "manifest_missing", "manifest_invalid", "manifest_hash"])
def test_unverified_launch_state_never_falls_back_to_default_provider(tmp_path, damage):
    install(tmp_path, **CONFIG)
    profile, manifest = tmp_path / PROFILE, tmp_path / ".pii-gateway-install" / "manifest.json"
    if damage == "profile_changed":
        profile.write_text('model_provider = "openai"\n')
    elif damage == "profile_missing":
        profile.unlink()
    elif damage == "manifest_missing":
        manifest.unlink()
    elif damage == "manifest_invalid":
        manifest.write_text('{"schema_version":"untrusted"}')
    else:
        value = json.loads(manifest.read_text())
        value["installed_sha256"] = "0" * 64
        manifest.write_text(json.dumps(value))
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", "synthetic prompt"], codex_home=tmp_path)


@pytest.mark.parametrize("mutation", ["external_url", "external_key", "tools_enabled", "invalid_toml", "missing_model"])
def test_rehashed_untrusted_profile_cannot_override_canonical_protection(tmp_path, mutation):
    install(tmp_path, **CONFIG)
    profile = tmp_path / PROFILE
    content = profile.read_text()
    if mutation == "external_url":
        content = content.replace("http://127.0.0.1:8765/v1", "https://synthetic-external.invalid/v1")
    elif mutation == "external_key":
        content = content.replace("PII_GATEWAY_LOCAL_TOKEN", "OPENAI_API_KEY")
    elif mutation == "tools_enabled":
        content = content.replace("shell_tool = false", "shell_tool = true")
    elif mutation == "invalid_toml":
        content = "[SYNTHETIC_MALFORMED_TOML"
    else:
        content = content.replace('model = "mock-model"\n', "")
    profile.write_text(content)
    manifest = tmp_path / ".pii-gateway-install" / "manifest.json"
    value = json.loads(manifest.read_text())
    value["installed_sha256"] = hashlib.sha256(profile.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", "synthetic prompt"], codex_home=tmp_path)


def test_installed_profile_symlink_is_rejected_at_launch_without_reading_target(tmp_path):
    install(tmp_path, **CONFIG)
    profile = tmp_path / PROFILE
    victim = tmp_path / "synthetic-victim"
    victim.write_bytes(profile.read_bytes())
    profile.unlink()
    profile.symlink_to(victim)
    with pytest.raises(ProfileError):
        launch_arguments(PINNED_CLI, ["exec", "synthetic prompt"], codex_home=tmp_path)
    assert victim.read_bytes().startswith(b"# Protected text gateway")
