"""Validated local-PC settings; loading dotenv files or secrets is caller-owned.

Routing and TLS invariants are fixed. Validation does not create paths, read
profiles, contact endpoints, or claim the detector/model is ready. The journal
must still enforce private filesystem permissions and reject unsafe paths.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import urlsplit


class SettingsError(Exception):
    """A fixed safe code; environment values are never included in errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_PORT = re.compile(r"[0-9]{1,5}\Z")
_FORBIDDEN = frozenset({"OPENAI_BASE_URL", "OPENAI_API_BASE", "PII_UPSTREAM_URL",
                        "PII_GATEWAY_HOST", "PII_DETECTOR_VERIFY_TLS", "PII_VERIFY_TLS"})


def _credential(env, name):
    value = env.get(name)
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or any(not 33 <= ord(char) <= 126 for char in value)):
        raise SettingsError("INVALID_CREDENTIAL")
    return value


def _detector_url(value):
    try:
        if (not isinstance(value, str) or not 1 <= len(value) <= 2048
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or any(character in value for character in ("?", "#", "\\"))):
            raise ValueError
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path != "/v1/inspect"
                or parsed.username is not None or parsed.password is not None
                or port == 0 or parsed.netloc.endswith(":") or "%" in parsed.netloc):
            raise ValueError
    except (ValueError, TypeError):
        raise SettingsError("INVALID_DETECTOR_URL") from None
    return value


def _state_dir(env):
    if "PII_STATE_DIR" not in env:
        return Path.home() / ".local" / "state" / "ai-pii-gateway"
    value = env["PII_STATE_DIR"]
    if not isinstance(value, str) or not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SettingsError("INVALID_STATE_DIR")
    try:
        value.encode("utf-8")
        path = Path(value).expanduser()
    except (ValueError, RuntimeError, UnicodeError):
        raise SettingsError("INVALID_STATE_DIR") from None
    if not path.is_absolute():
        raise SettingsError("INVALID_STATE_DIR")
    return path


@dataclass(frozen=True)
class Settings:
    local_token: str = field(repr=False)
    detector_url: str
    detector_token: str = field(repr=False)
    openai_api_key: str = field(repr=False)
    model_id: str
    port: int
    state_dir: Path = field(repr=False)
    host: str = field(default="127.0.0.1", init=False)
    upstream_url: str = field(default="https://api.openai.com/v1/responses", init=False)
    verify_tls: bool = field(default=True, init=False)

    @classmethod
    def from_env(cls, env: Mapping):
        """Validate an explicit mapping without modifying it or loading .env.

        Required: PII_GATEWAY_LOCAL_TOKEN, PII_DETECTOR_URL,
        PII_DETECTOR_TOKEN, OPENAI_API_KEY and PII_MODEL_ID. Optional strings:
        PII_GATEWAY_PORT (8765), PII_STATE_DIR (platform home local state).
        Credentials are preserved exactly; whitespace is rejected, not stripped.
        """
        if not isinstance(env, Mapping):
            raise SettingsError("INVALID_ENVIRONMENT")
        if any(name in env for name in _FORBIDDEN) or ("PYTHONHTTPSVERIFY" in env and env["PYTHONHTTPSVERIFY"] != "1"):
            raise SettingsError("PROTECTED_SETTING_OVERRIDE")
        local_token = _credential(env, "PII_GATEWAY_LOCAL_TOKEN")
        detector_token = _credential(env, "PII_DETECTOR_TOKEN")
        openai_api_key = _credential(env, "OPENAI_API_KEY")
        detector_url = _detector_url(env.get("PII_DETECTOR_URL"))
        model_id = env.get("PII_MODEL_ID")
        if not isinstance(model_id, str) or _MODEL.fullmatch(model_id) is None:
            raise SettingsError("INVALID_MODEL_ID")
        port = env.get("PII_GATEWAY_PORT", "8765")
        if not isinstance(port, str) or _PORT.fullmatch(port) is None or not 1 <= int(port) <= 65535:
            raise SettingsError("INVALID_PORT")
        return cls(local_token=local_token, detector_url=detector_url, detector_token=detector_token,
                   openai_api_key=openai_api_key, model_id=model_id, port=int(port), state_dir=_state_dir(env))
