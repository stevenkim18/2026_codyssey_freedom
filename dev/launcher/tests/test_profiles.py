"""R01/T11: profile backup and recovery are independent of real user config."""
import json
from pathlib import Path
import stat
import tomllib

import pytest

from launcher.profiles import ProfileError, install, uninstall, launch_arguments


def test_reviewed_text_profile_uses_source_companion_and_disables_observed_optional_capabilities(tmp_path):
    install(tmp_path, port=8765, model="mock-model", cli_version="0.166.0")
    config = tomllib.loads((tmp_path / "pii-gateway.config.toml").read_text())
    companion = Path(config["model_instructions_file"])
    expected = b"You are a text-only assistant. Answer the user with plain text. Do not call tools."
    assert companion.is_absolute()
    assert companion.name == "text_only_instructions.md"
    assert companion.parent.name == "launcher"
    assert companion.read_bytes() == expected
    assert len(expected.decode()) == 82
    assert not (tmp_path / "text_only_instructions.md").exists()
    assert config["project_doc_max_bytes"] == 0
    assert config["check_for_update_on_startup"] is False
    assert config["personality"] == "none"
    assert config["agents"]["enabled"] is False
    for feature in ("multi_agent", "request_permissions_tool", "view_image", "image_generation", "sleep_tool", "goals",
        "plugins", "remote_plugin", "skill_search", "tool_suggest", "skill_mcp_dependency_install", "memories",
        "hooks", "browser_use", "computer_use", "in_app_browser", "code_mode_host", "default_mode_request_user_input",
        "code_mode_only", "incremental_tools", "api_key_model_discovery", "api_key_cyber_access_programs"):
        assert config["features"][feature] is False
    assert config["features"]["code_mode"]["enabled"] is False
    # Pinned 0.166.0 reports tools.view_image as ignored; the feature flag and
    # gateway output filtering enforce the supported text profile instead.
    assert "view_image" not in config["tools"]
    assert config["tools"]["experimental_request_user_input"]["enabled"] is False
    assert config["tools"]["update_plan"]["enabled"] is False
    assert config["skills"]["bundled"]["enabled"] is False
    assert config["skills"]["include_instructions"] is False
    assert config["analytics"]["enabled"] is False
    assert config["feedback"]["enabled"] is False


def test_install_isolated_profile_and_restore_existing_file(tmp_path):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    profile = codex_home / "pii-gateway.config.toml"
    profile.write_bytes(b'# existing personal profile\nmodel = "old-model"\n')
    original = profile.read_bytes()
    main = codex_home / "config.toml"
    main.write_text("# user's main config\n")
    install(codex_home, port=8765, model="mock-model", cli_version="0.166.0")
    parsed = tomllib.loads(profile.read_text())
    provider = parsed["model_providers"]["pii-gateway"]
    assert provider["base_url"] == "http://127.0.0.1:8765/v1"
    assert provider["env_key"] == "PII_GATEWAY_LOCAL_TOKEN"
    assert provider["request_max_retries"] == 0 and provider["stream_max_retries"] == 0
    assert provider["supports_websockets"] is False
    assert parsed["model_provider"] == "pii-gateway"
    assert "OPENAI_API_KEY" not in profile.read_text()
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
    assert main.read_text() == "# user's main config\n"
    uninstall(codex_home)
    assert profile.read_bytes() == original


def test_fresh_install_uninstall_and_reinstall_idempotency(tmp_path):
    install(tmp_path, port=8765, model="mock-model", cli_version="0.166.0")
    first = (tmp_path / "pii-gateway.config.toml").read_bytes()
    install(tmp_path, port=8765, model="mock-model", cli_version="0.166.0")
    assert (tmp_path / "pii-gateway.config.toml").read_bytes() == first
    uninstall(tmp_path)
    assert not (tmp_path / "pii-gateway.config.toml").exists()


def test_user_edits_after_install_are_never_overwritten_on_remove(tmp_path):
    install(tmp_path, port=8765, model="mock-model", cli_version="0.166.0")
    profile = tmp_path / "pii-gateway.config.toml"
    profile.write_text("# user edited\n")
    with pytest.raises(ProfileError):
        uninstall(tmp_path)
    assert profile.read_text() == "# user edited\n"


@pytest.mark.parametrize("kwargs", [{"port": 0}, {"port": True}, {"port": 65536},
    {"model": 'bad"\nkey="value'}, {"model": ""}, {"cli_version": "unverified"}])
def test_invalid_profile_inputs_do_not_write_files(tmp_path, kwargs):
    with pytest.raises(ProfileError):
        install(tmp_path, **{"port": 8765, "model": "mock-model", "cli_version": "0.166.0", **kwargs})
    assert list(tmp_path.iterdir()) == []


def test_launcher_pins_profile_ignores_user_provider_and_never_has_bypass_flags(tmp_path):
    install(tmp_path, port=8765, model="mock-model", cli_version="0.166.0")
    args = launch_arguments("0.166.0", ["exec", "synthetic prompt"], codex_home=tmp_path)
    assert args[:2] == ["codex", "exec"]  # ignore-user-config is an exec-only option.
    assert "--ignore-user-config" in args
    assert not any("bypass" in a for a in args)
    assert 'model_provider="pii-gateway"' in args
    assert 'model_providers.pii-gateway.base_url="http://127.0.0.1:8765/v1"' in args


def test_unverified_interactive_launch_is_rejected():
    with pytest.raises(ProfileError):
        launch_arguments("0.166.0", ["synthetic prompt"])


def test_symlink_profile_is_not_followed(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("preserve")
    (tmp_path / "pii-gateway.config.toml").symlink_to(secret)
    with pytest.raises(ProfileError):
        install(tmp_path, port=8765, model="mock-model", cli_version="0.166.0")
    assert secret.read_text() == "preserve"
