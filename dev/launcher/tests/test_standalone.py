"""Packaged client security boundaries (local mocks, no remote readiness claims)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from launcher import standalone as client


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "config" / "client.json"
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def invoke(self, *args):
        return client.main(["--settings", str(self.path), *args])

    def configure(self, origin="https://detector.example", ca=""):
        with patch("builtins.input", side_effect=[origin, "", ca]), patch(
                "getpass.getpass", side_effect=["detector-secret", "provider-secret"]):
            return self.invoke("configure")

    def test_configure_private_and_secret_free_output(self):
        self.assertEqual(self.configure(), 0)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("secret", self.output.getvalue())
        self.assertEqual(client.load_settings(self.path)["model_id"], "gpt-6.1-sol")

    def test_configure_never_overwrites(self):
        self.assertEqual(self.configure(), 0)
        before = self.path.read_bytes()
        self.assertEqual(self.configure(), 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_http_origin_rejected_without_persistence(self):
        self.assertEqual(self.configure("http://detector.example"), 2)
        self.assertFalse(self.path.exists())

    def test_bad_ca_rejected_without_persistence(self):
        self.assertEqual(self.configure(ca=str(self.root / "missing.pem")), 2)
        self.assertFalse(self.path.exists())

    def test_insecure_settings_permissions_rejected(self):
        self.configure()
        self.path.chmod(0o644)
        self.assertEqual(self.invoke("doctor"), 2)

    def test_symlink_parent_rejected(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.path.parent.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.configure(), 2)
        self.assertFalse((target / "client.json").exists())

    def test_hardlink_settings_rejected(self):
        self.configure()
        os.link(self.path, self.root / "alias")
        self.assertEqual(self.invoke("doctor"), 2)

    def test_duplicate_unknown_and_oversize_json_rejected(self):
        self.configure()
        original = self.path.read_text()
        for data in ('{"model_id":"a","model_id":"b"}',
                     original[:-1] + ',"OPENAI_BASE_URL":"evil"}', " " * 65537):
            self.path.write_text(data)
            self.assertEqual(self.invoke("doctor"), 2)

    def test_install_delegates_explicit_profile_model_and_port(self):
        self.configure()
        with patch("launcher.__main__.main", return_value=0) as delegate:
            self.assertEqual(self.invoke("install"), 0)
        args = delegate.call_args.args[0]
        self.assertEqual(args[0], "install")
        self.assertIn("--codex-home", args)
        self.assertEqual(args[-4:], ["--port", "8765", "--model", "gpt-6.1-sol"])

    def test_run_strips_gateway_credentials_and_restores_environment(self):
        self.configure()
        os.environ["OPENAI_API_KEY"] = "inherited-secret"
        os.environ["PII_DETECTOR_TOKEN"] = "inherited-detector"
        def delegate(args):
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("PII_DETECTOR_TOKEN", os.environ)
            self.assertTrue(os.environ["PII_GATEWAY_LOCAL_TOKEN"])
            self.assertEqual(args[-3:], ["--", "exec", "hello"])
            return 0
        with patch("launcher.__main__.main", side_effect=delegate):
            self.assertEqual(self.invoke("run", "--", "exec", "hello"), 0)
        self.assertEqual(os.environ["OPENAI_API_KEY"], "inherited-secret")

    def test_protected_override_refused_before_serve(self):
        self.configure()
        os.environ["OPENAI_BASE_URL"] = "https://evil.example"
        with patch("gateway.server.main") as serve:
            self.assertEqual(self.invoke("serve"), 2)
            serve.assert_not_called()

    def test_serve_uses_validated_configuration_in_process(self):
        self.configure()
        def serve():
            self.assertEqual(os.environ["PII_DETECTOR_URL"], "https://detector.example/v1/inspect")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "provider-secret")
            return 0
        with patch("gateway.server.main", side_effect=serve):
            self.assertEqual(self.invoke("serve"), 0)

    def test_doctor_probe_is_exact_loopback_and_uninstall_delegates(self):
        self.configure()
        with patch("launcher.__main__.main", return_value=0) as delegate:
            self.assertEqual(self.invoke("doctor", "--probe"), 0)
            self.assertEqual(delegate.call_args.args[0][-2:],
                             ["--gateway-url", "http://127.0.0.1:8765"])
            self.assertEqual(self.invoke("uninstall"), 0)
            self.assertEqual(delegate.call_args.args[0][0], "uninstall")

    def test_invalid_arguments_redact_values_and_version_needs_no_settings(self):
        self.assertEqual(self.invoke("--evil-secret"), 2)
        self.assertNotIn("evil-secret", self.output.getvalue())
        self.assertEqual(self.invoke("version"), 0)


if __name__ == "__main__":
    unittest.main()
