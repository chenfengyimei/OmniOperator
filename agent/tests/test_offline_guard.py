import os
from pathlib import Path
import subprocess
import sys

from offline_guard import local_host


def test_loopback_policy_rejects_dns_private_and_public_network():
    for address in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"):
        assert local_host(address)
    for address in ("192.168.1.1", "10.0.0.1", "8.8.8.8", "api.example.com", "127.0.0.1.evil.test"):
        assert not local_host(address)


def test_child_process_cannot_connect_even_if_error_is_caught(tmp_path):
    env = dict(os.environ, OMNI_TEST_VIOLATIONS=str(tmp_path / "expected-refusal.txt"))
    code = """
import socket
from offline_guard import OfflineViolation
try:
 socket.create_connection(('203.0.113.1',443),timeout=.1)
except OfflineViolation:
 print('offline blocked')
else:
 raise AssertionError('escaped test policy')
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "offline blocked" in result.stdout, result.stderr
    assert Path(env["OMNI_TEST_VIOLATIONS"]).read_text().strip() == "socket.connect"
