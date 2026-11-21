"""A URL can't use what lives on the application's machine: its connection profiles and TLS
files. Against a real server, with the reviewer's cases from before 1.2.0, when a URL naming the
machine's profile signed in as the profile's user, and a URL naming the profile together with
its own host received the profile's password there."""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading

import hardcharger
import pytest
from sqlalchemy import create_engine, exc, text

from .conftest import server_url


@pytest.fixture
def host_profile(server: hardcharger.Server, tmp_path, monkeypatch) -> str:
    """The machine's connection profile "prod" for the test server, as an application's host
    would keep one (driver manifest and profile in their own directories). Returns its path."""
    drivers = tmp_path / "drivers"
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    subprocess.run(
        [sys.executable, "-m", "adbc_driver_hardcharger.manifest", "--dir", str(drivers)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("ADBC_DRIVER_PATH", str(drivers))
    monkeypatch.setenv("ADBC_PROFILE_PATH", str(profiles))
    profile = profiles / "prod.toml"
    profile.write_text(
        'profile_version = 1\ndriver = "hardcharger"\n\n[Options]\n'
        f"uri = {json.dumps(server.uri)}\nusername = {json.dumps(server.username)}\n"
        f"password = {json.dumps(server.password)}\n"
    )
    return str(profile)


# What a Chargewire server answers on discovery, so that a driver goes on to sign in.
_DISCOVERY = json.dumps(
    {
        "protocol": "chargewire",
        "protocol_versions": ["1"],
        "server": {"name": "Hardcharger", "version": "2.13.1"},
        "capabilities": ["transactions"],
        "auth": {"grant_types": ["password"]},
        "links": {},
    }
).encode()


@pytest.fixture
def listener():
    """A stand-in for someone else's server: answers discovery like a Chargewire server and
    records each request's method and path (and whether it carried credentials, never which)."""
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _record(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            carries = bool(self.headers.get("Authorization")) or b"password" in body
            requests.append(f"{self.command} {self.path}{' with credentials' if carries else ''}")
            if self.command == "GET" and self.path.rstrip("/") == "/chargewire":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_DISCOVERY)))
                self.end_headers()
                self.wfile.write(_DISCOVERY)
                return
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _record

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], requests
    httpd.shutdown()
    httpd.server_close()


def user_of(url, **connect_args) -> str:
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT HARDCHARGER_USER()")).scalar()
    finally:
        engine.dispose()


def test_the_application_still_signs_in_with_its_profile(server, host_profile):
    """The positive twin: the profile works, from connect_args, by path and by name."""
    assert user_of("hardcharger://", profile=host_profile) == server.username
    assert user_of("hardcharger://", profile="prod") == server.username


def test_a_url_cannot_sign_in_with_the_machines_profile(host_profile):
    for url in ("hardcharger:///?profile=prod", f"hardcharger:///?profile={host_profile}"):
        with pytest.raises(exc.ArgumentError, match="'profile' can't be set in a URL"):
            user_of(url)


def test_a_url_cannot_send_the_profiles_password_to_its_own_host(host_profile, listener):
    port, requests = listener
    url = f"hardcharger://127.0.0.1:{port}/?tls=false&profile={host_profile}"
    with pytest.raises(exc.ArgumentError, match="'profile' can't be set in a URL"):
        user_of(url)
    assert requests == []


def test_a_url_cannot_name_tls_files(tls_server: hardcharger.Server, tls_files):
    """tls_root_certs (and the client certificate and key) are files on the application's
    machine: a URL can't point the driver at them."""
    for option, path in (
        ("tls_root_certs", tls_files["cert"]),
        ("tls_client_cert", tls_files["cert"]),
        ("tls_client_key", tls_files["key"]),
    ):
        with pytest.raises(exc.ArgumentError, match=f"'{option}' can't be set in a URL"):
            user_of(server_url(server=tls_server, **{option: path}))


def test_connect_args_still_name_tls_files(tls_server: hardcharger.Server, tls_files):
    assert (
        user_of(server_url(server=tls_server), tls_root_certs=tls_files["cert"])
        == tls_server.username
    )
