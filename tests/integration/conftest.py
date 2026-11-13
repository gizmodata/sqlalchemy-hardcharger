"""Fixtures for the integration tests: a real Hardcharger server.

The ``hardcharger`` package downloads the server binary for this platform on first use and runs
it on a free port (``HARDCHARGER_SERVER_BINARY`` names another build to test). The server is a
requirement, not an option: if it can't start, every test here errors. Nothing skips.
"""

from __future__ import annotations

import datetime
import ipaddress
import itertools
from collections.abc import Iterator

import hardcharger
import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import URL, Engine

USERNAME = "sa_test"
PASSWORD = "sa-test-password"

_names = itertools.count()


def server_url(server: hardcharger.Server, **query: str) -> URL:
    """The SQLAlchemy URL of a test server, with extra URL options."""
    if not server.tls:
        query = {"tls": "false", **query}
    return URL.create(
        drivername="hardcharger",
        username=server.username,
        password=server.password,
        host=server.host,
        port=server.port,
        query=query,
    )


@pytest.fixture(scope="session")
def server() -> Iterator[hardcharger.Server]:
    with hardcharger.Server(username=USERNAME, password=PASSWORD) as running:
        yield running


@pytest.fixture(scope="session")
def url(server: hardcharger.Server) -> URL:
    return server_url(server=server)


@pytest.fixture(scope="session")
def engine(url: URL) -> Iterator[Engine]:
    engine = create_engine(url=url)
    yield engine
    engine.dispose()


@pytest.fixture
def metadata(engine: Engine) -> Iterator[MetaData]:
    """A MetaData whose tables are dropped after the test."""
    metadata = MetaData()
    yield metadata
    metadata.drop_all(bind=engine)


@pytest.fixture
def name() -> str:
    """A table name no other test uses."""
    return f"it_{next(_names)}"


@pytest.fixture(scope="session")
def tls_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """A self-signed certificate for 127.0.0.1, with its key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(curve=ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sqlalchemy-hardcharger test")])
    now = datetime.datetime.now(tz=datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    cert_path = directory / "server.crt"
    key_path = directory / "server.key"
    cert_path.write_bytes(certificate.public_bytes(encoding=serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return {"cert": str(cert_path), "key": str(key_path)}


@pytest.fixture(scope="session")
def tls_server(tls_files: dict[str, str]) -> Iterator[hardcharger.Server]:
    with hardcharger.Server(
        username=USERNAME, password=PASSWORD, tls_cert=tls_files["cert"], tls_key=tls_files["key"]
    ) as running:
        yield running
