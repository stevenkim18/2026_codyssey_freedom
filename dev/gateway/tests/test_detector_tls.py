import os
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

from gateway.settings import Settings, SettingsError
from gateway.tls import detector_tls_context
from gateway.tests.test_settings import environment


@pytest.fixture
def certificate(tmp_path):
    cert = tmp_path / "ca.pem"
    key = tmp_path / "key.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-days", "1", "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    cert.chmod(0o644)
    return cert


def test_default_verify_remains_true():
    assert Settings.from_env(environment()).detector_ca_file is None
    assert detector_tls_context(None) is True


def test_ca_setting_and_context(certificate):
    settings = Settings.from_env(environment() | {"PII_DETECTOR_CA_FILE": str(certificate)})
    assert settings.detector_ca_file == certificate
    assert str(certificate) not in repr(settings)
    context = detector_tls_context(settings.detector_ca_file)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert len(context.get_ca_certs(binary_form=True)) == 1


@pytest.mark.parametrize("value", [None, "", "relative.pem", 42, "/tmp/bad\nca", "/tmp/\ud800"])
def test_invalid_setting(value):
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        Settings.from_env(environment() | {"PII_DETECTOR_CA_FILE": value})


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "hardlink", "writable", "oversize", "invalid"])
def test_unsafe_ca_fails_closed(tmp_path, certificate, kind):
    path = tmp_path / "unsafe.pem"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(certificate)
    elif kind == "hardlink":
        os.link(certificate, path)
    elif kind == "writable":
        path.write_bytes(certificate.read_bytes())
        path.chmod(0o666)
    elif kind == "oversize":
        path.write_bytes(b"x" * (1024 * 1024 + 1))
    elif kind == "invalid":
        path.write_text("SYNTHETIC_INVALID_CERTIFICATE")
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        detector_tls_context(path)


def handshake(client_context, certificate, hostname):
    """Exercise OpenSSL certificate verification without network dependencies."""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, certificate.parent / "key.pem")
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    client_done = server_done = False
    for _ in range(20):
        for peer, outbound, inbound, is_client in (
            (client, client_out, server_in, True), (server, server_out, client_in, False)
        ):
            try:
                peer.do_handshake()
                if is_client:
                    client_done = True
                else:
                    server_done = True
            except ssl.SSLWantReadError:
                pass
            data = outbound.read()
            if data:
                inbound.write(data)
        if client_done and server_done:
            return
    pytest.fail("TLS handshake did not complete")


def test_private_ca_handshake_and_wrong_hostname(certificate):
    context = detector_tls_context(certificate)
    handshake(context, certificate, "localhost")
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(context, certificate, "wrong.example.invalid")


def test_default_provider_trust_rejects_private_detector_ca(certificate):
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(ssl.create_default_context(), certificate, "localhost")


def test_unrelated_ca_rejects_detector_certificate(certificate, tmp_path):
    other = tmp_path / "unrelated.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-days", "1", "-subj", "/CN=unrelated", "-keyout", str(tmp_path / "other.key"),
                    "-out", str(other)], check=True, capture_output=True)
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(detector_tls_context(other), certificate, "localhost")


def test_writable_ancestor_rejected(tmp_path, certificate):
    directory = tmp_path / "unsafe-parent"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    ca = directory / "ca.pem"
    ca.write_bytes(certificate.read_bytes())
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        detector_tls_context(ca)


def test_pinned_ancestor_survives_path_substitution(tmp_path, certificate, monkeypatch):
    directory = tmp_path / "pinned-parent"
    directory.mkdir()
    ca = directory / "ca.pem"
    ca.write_bytes(certificate.read_bytes())
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "ca.pem").write_text("INVALID_REPLACEMENT")
    original_open = os.open
    swapped = False
    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = original_open(path, flags, *args, **kwargs)
        if str(path) == "pinned-parent" and kwargs.get("dir_fd") is not None:
            directory.rename(tmp_path / "original")
            directory.symlink_to(replacement, target_is_directory=True)
            swapped = True
        return descriptor
    monkeypatch.setattr(os, "open", racing_open)
    context = detector_tls_context(ca)
    assert swapped
    assert len(context.get_ca_certs(binary_form=True)) == 1


def test_writable_ancestor_above_direct_parent_rejected(tmp_path, certificate):
    unsafe = tmp_path / "unsafe-ancestor"
    safe_parent = unsafe / "safe-parent"
    safe_parent.mkdir(parents=True)
    unsafe.chmod(0o777)
    safe_parent.chmod(0o700)
    ca = safe_parent / "ca.pem"
    ca.write_bytes(certificate.read_bytes())
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        detector_tls_context(ca)


def test_ca_growth_after_stat_is_bounded_and_rejected(certificate, monkeypatch):
    original_fstat = os.fstat
    target = certificate.stat()
    grew = False
    def growing_fstat(descriptor):
        nonlocal grew
        info = original_fstat(descriptor)
        if not grew and (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            with certificate.open("ab") as writable:
                writable.write(b"x" * (1024 * 1024 + 1))
            grew = True
        return info
    monkeypatch.setattr(os, "fstat", growing_fstat)
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        detector_tls_context(certificate)
    assert grew
    assert certificate.stat().st_size > 1024 * 1024


@pytest.mark.parametrize("mode", [0o777, 0o700])
def test_nonplatform_ancestor_alias_cannot_bypass_validation(tmp_path, certificate, mode):
    directory = tmp_path / "alias-parent"
    directory.mkdir()
    directory.chmod(mode)
    (directory / "alias").symlink_to(certificate.parent, target_is_directory=True)
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        detector_tls_context(directory / "alias" / certificate.name)


def test_parent_traversal_is_rejected(tmp_path, certificate):
    directory = tmp_path / "child"
    directory.mkdir()
    with pytest.raises(SettingsError, match="^INVALID_DETECTOR_CA_FILE$"):
        detector_tls_context(directory / ".." / certificate.name)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin root-owned system alias")
def test_darwin_var_alias_accepts_verified_fixed_platform_mapping(certificate):
    if not certificate.is_relative_to("/private/var"):
        pytest.skip("fixture outside Darwin /private/var")
    alias = Path("/var") / certificate.relative_to("/private/var")
    context = detector_tls_context(alias)
    assert len(context.get_ca_certs(binary_form=True)) == 1
