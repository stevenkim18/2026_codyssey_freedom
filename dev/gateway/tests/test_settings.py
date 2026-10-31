"""R01/R10: content-free configuration failures and fixed protected routing."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from gateway.settings import Settings, SettingsError


def environment():
    return {"PII_GATEWAY_LOCAL_TOKEN": "SYNTHETIC_LOCAL_TOKEN",
            "PII_DETECTOR_URL": "https://detector.example.invalid/v1/inspect",
            "PII_DETECTOR_TOKEN": "SYNTHETIC_DETECTOR_TOKEN",
            "OPENAI_API_KEY": "SYNTHETIC_OPENAI_KEY", "PII_MODEL_ID": "mock-model"}


def test_defaults_are_loopback_tls_and_fixed_external_provider_without_secret_repr():
    settings = Settings.from_env(environment())
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.upstream_url == "https://api.openai.com/v1/responses"
    assert settings.verify_tls is True
    assert settings.state_dir == Path.home() / ".local" / "state" / "ai-pii-gateway"
    assert settings.model_id == "mock-model"
    assert settings.local_token == "SYNTHETIC_LOCAL_TOKEN"
    assert settings.detector_token == "SYNTHETIC_DETECTOR_TOKEN"
    assert settings.openai_api_key == "SYNTHETIC_OPENAI_KEY"
    for secret in ("SYNTHETIC_LOCAL_TOKEN", "SYNTHETIC_DETECTOR_TOKEN", "SYNTHETIC_OPENAI_KEY"):
        assert secret not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.host = "0.0.0.0"


def test_explicit_port_and_state_path_are_validated_without_creating_directories(tmp_path):
    state = tmp_path / "not-yet-created" / "state"
    settings = Settings.from_env(environment() | {"PII_GATEWAY_PORT": "65535", "PII_STATE_DIR": str(state)})
    assert settings.port == 65535
    assert settings.state_dir == state
    assert not state.exists()
    assert Settings.from_env(environment() | {"PII_STATE_DIR": "~/.local/state/synthetic-gateway"}).state_dir == Path.home() / ".local/state/synthetic-gateway"


@pytest.mark.parametrize("key", ["PII_GATEWAY_LOCAL_TOKEN", "PII_DETECTOR_URL", "PII_DETECTOR_TOKEN", "OPENAI_API_KEY", "PII_MODEL_ID"])
def test_missing_required_configuration_never_uses_unprotected_fallback(key):
    env = environment()
    del env[key]
    with pytest.raises(SettingsError) as failure:
        Settings.from_env(env)
    assert failure.value.code
    assert "SYNTHETIC" not in str(failure.value)


@pytest.mark.parametrize("key", ["PII_GATEWAY_LOCAL_TOKEN", "PII_DETECTOR_TOKEN", "OPENAI_API_KEY"])
@pytest.mark.parametrize("value", [None, "", " ", "token\r\nX-Injected: synthetic", "token with spaces", "key\x00", "한글", "x" * 4097])
def test_invalid_credentials_fail_without_echoing_values(key, value):
    with pytest.raises(SettingsError) as failure:
        Settings.from_env(environment() | {key: value})
    assert str(failure.value) == failure.value.code
    assert "Injected" not in str(failure.value)


@pytest.mark.parametrize("url", [None, 1, "", "http://detector.example.invalid/v1/inspect",
    "https://user:pass@detector.example.invalid/v1/inspect", "https://detector.example.invalid/v1/inspect?",
    "https://detector.example.invalid/v1/inspect#", "https://detector.example.invalid/v1/inspect?x=1",
    "https://detector.example.invalid/v1/inspect/", "https://detector.example.invalid:0/v1/inspect",
    "https://detector.example.invalid:65536/v1/inspect", "https://detector.example.invalid:/v1/inspect",
    "https://detector.example.invalid/v1/inspect\n", "https://detector.example.invalid\\evil/v1/inspect",
    "https:///v1/inspect", "https://[malformed/v1/inspect", "https://detector%40evil/v1/inspect"])
def test_invalid_detector_destination_is_never_accepted(url):
    with pytest.raises(SettingsError):
        Settings.from_env(environment() | {"PII_DETECTOR_URL": url})


@pytest.mark.parametrize("url", ["https://10.0.0.5/v1/inspect", "https://[::1]:8443/v1/inspect",
    "https://detector.example.invalid:8443/v1/inspect"])
def test_private_network_detector_https_is_supported_without_disabling_tls(url):
    settings = Settings.from_env(environment() | {"PII_DETECTOR_URL": url})
    assert settings.detector_url == url
    assert settings.verify_tls is True


@pytest.mark.parametrize("port", [None, True, 8765, "", "0", "65536", "-1", "1.5", " 8765", "+8765", "٨٧٦٥"])
def test_port_has_no_implicit_coercion_or_ambiguous_integer_syntax(port):
    with pytest.raises(SettingsError):
        Settings.from_env(environment() | {"PII_GATEWAY_PORT": port})


@pytest.mark.parametrize("state", [None, "", "relative/state", "./state", "path\x00invalid", 1,
    "/tmp/synthetic\nunsafe", "/tmp/\ud800", "~pii_gateway_no_such_user_7f4b/state"])
def test_state_directory_must_be_explicit_absolute_path_when_overridden(state):
    with pytest.raises(SettingsError):
        Settings.from_env(environment() | {"PII_STATE_DIR": state})


@pytest.mark.parametrize("model", [None, "", [], "model\nprovider=evil", "model with spaces", "m" * 129])
def test_model_id_cannot_inject_profile_or_escape_allowlist_identity(model):
    with pytest.raises(SettingsError):
        Settings.from_env(environment() | {"PII_MODEL_ID": model})


@pytest.mark.parametrize("key,value", [("OPENAI_BASE_URL", "https://evil.invalid/v1"),
    ("OPENAI_API_BASE", "https://evil.invalid/v1"), ("PII_UPSTREAM_URL", "http://127.0.0.1/v1/responses"),
    ("PII_GATEWAY_HOST", "0.0.0.0"), ("PII_DETECTOR_VERIFY_TLS", "false"), ("PII_VERIFY_TLS", "0"),
    ("PYTHONHTTPSVERIFY", "0")])
def test_environment_cannot_override_provider_binding_or_certificate_verification(key, value):
    with pytest.raises(SettingsError):
        Settings.from_env(environment() | {key: value})


@pytest.mark.parametrize("env", [None, [], "synthetic", 1])
def test_non_mapping_configuration_fails_with_safe_code(env):
    with pytest.raises(SettingsError) as failure:
        Settings.from_env(env)
    assert str(failure.value) == failure.value.code


def test_unrelated_environment_variables_are_ignored():
    settings = Settings.from_env(environment() | {"HOME": "/unrelated/synthetic-home", "UNRELATED": None})
    assert settings.state_dir == Path.home() / ".local/state/ai-pii-gateway"
