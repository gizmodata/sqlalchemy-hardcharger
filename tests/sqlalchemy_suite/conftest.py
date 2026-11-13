"""Run SQLAlchemy's dialect compliance suite against a real Hardcharger server.

    pytest tests/sqlalchemy_suite

SQLAlchemy's pytest plugin takes over collection and reads its database from ``--dburi``; this
conftest starts a Hardcharger server (the ``hardcharger`` package runs the real server binary on
a free port) and hands the plugin its URL, so the suite needs nothing started beforehand. If the
server can't start, the run fails: nothing here skips.
"""

import hardcharger
from sqlalchemy.dialects import registry
from sqlalchemy.testing.plugin import pytestplugin
from sqlalchemy.testing.plugin.pytestplugin import *  # noqa: F401, F403

import sqlalchemy_hardcharger.provision  # noqa: F401  (registers the provisioning hooks)

registry.register("hardcharger", "sqlalchemy_hardcharger", "HardchargerDialect")
registry.register("hardcharger.adbc", "sqlalchemy_hardcharger", "HardchargerDialect")

_sqlalchemy_configure = pytestplugin.pytest_configure
_server = None


def pytest_configure(config):  # noqa: ANN001, ANN201
    global _server
    if not hasattr(config, "workerinput"):
        _server = hardcharger.Server(username="suite", password="suite-password")
        _server.start()
        with _server.connect() as conn, conn.cursor() as cur:
            # The schemas the suite's cross-schema tests expect to exist.
            cur.execute("CREATE SCHEMA test_schema")
            cur.execute("CREATE SCHEMA test_schema_2")
        config.option.dburi = [
            f"hardcharger://suite:suite-password@{_server.host}:{_server.port}?tls=false"
        ]
    _sqlalchemy_configure(config)


def pytest_unconfigure(config):  # noqa: ANN001, ANN201
    global _server
    if _server is not None:
        _server.stop()
        _server = None
