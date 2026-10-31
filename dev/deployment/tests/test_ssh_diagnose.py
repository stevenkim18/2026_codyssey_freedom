import pytest

from deployment.ssh_diagnose import classify, command_for, route_observation


@pytest.mark.parametrize("message,phase,code,connected", [
    ("connect to address 192.0.2.10 port 22: Operation timed out", "tcp_connect", "timeout", False),
    ("Connection refused", "tcp_connect", "refused", False),
    ("No route to host", "routing", "no_route", False),
    ("Could not resolve hostname example.invalid", "resolution", "failed", False),
    ("Connection established.\nConnection timed out during banner exchange", "ssh_banner", "timeout", True),
    ("Host key verification failed.", "host_key", "unverified", True),
    ("REMOTE HOST IDENTIFICATION HAS CHANGED", "host_key", "changed", True),
    ("Permission denied (publickey,password).", "authentication", "not_attempted", True),
    ("Operation not permitted", "local_permissions", "denied", False),
])
def test_failure_stage_is_distinct_from_password_rejection(message, phase, code, connected):
    result = classify(255, message + " SYNTHETIC_SECRET")
    assert result == {"phase":phase, "code":code, "tcp_established":connected}
    assert "SYNTHETIC_SECRET" not in str(result)


def test_diagnostic_disables_authentication_commands_and_config_inheritance(tmp_path):
    known = tmp_path / "folder with spaces" / "known_hosts"
    args = command_for({"private_ip":"192.0.2.10", "username":"synthetic", "ssh_port":22}, known)
    assert args[args.index("-F")+1] == "/dev/null"
    assert "-N" in args and args[-1] == "synthetic@192.0.2.10"
    for value in ("PreferredAuthentications=none", "PubkeyAuthentication=no", "PasswordAuthentication=no",
                  "KbdInteractiveAuthentication=no", "StrictHostKeyChecking=yes", "NumberOfPasswordPrompts=0"):
        assert value in args
    assert 'UserKnownHostsFile="'+str(known)+'"' in args


def test_route_facts_do_not_claim_vpn_or_root_cause():
    target = "destination: default\n gateway: 192.0.2.1\n interface: en0\n"
    default = "gateway: 192.0.2.1\n interface: en0\n"
    assert route_observation(target, default) == {"interface":"en0", "matches_default_route":True}
    assert route_observation("gateway: 10.0.0.1\n interface: utun9", default) == {"interface":"utun9", "matches_default_route":False}


@pytest.mark.parametrize("access", [
    {"private_ip":"-oProxyCommand=bad", "username":"synthetic", "ssh_port":22},
    {"private_ip":"192.0.2.10", "username":"x;bad", "ssh_port":22},
    {"private_ip":"192.0.2.10", "username":"synthetic", "ssh_port":True},
])
def test_access_injection_rejected(access, tmp_path):
    with pytest.raises(ValueError):
        command_for(access, tmp_path / "known_hosts")
