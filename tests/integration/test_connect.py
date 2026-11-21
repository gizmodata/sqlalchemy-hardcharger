"""Connecting: URL options against a real server."""

import hardcharger
import pytest
from sqlalchemy import create_engine, exc, text

from .conftest import server_url


def scalar(url, sql: str):
    engine = create_engine(url=url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_connects_and_reads_the_server_version(engine, server):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 42 AS answer")).scalar() == 42
        assert conn.execute(text("SELECT HARDCHARGER_USER()")).scalar() == server.username
        version = conn.execute(text("SELECT HARDCHARGER_VERSION()")).scalar()
    assert engine.dialect.server_version_info == tuple(
        int(p) for p in version.split("-")[0].split(".")
    )
    assert len(engine.dialect.server_version_info) == 3
    assert engine.dialect.default_schema_name == "main"


def test_the_url_as_a_plain_string_connects(server):
    url = f"hardcharger://{server.username}:{server.password}@{server.host}:{server.port}?tls=false"
    assert scalar(url=url, sql="SELECT 1") == 1


def test_a_wrong_password_is_refused(server):
    url = server_url(server=server).set(password="not-the-password")
    with pytest.raises(exc.DBAPIError, match="invalid_credentials"):
        scalar(url=url, sql="SELECT 1")


def test_tls_is_on_unless_the_url_turns_it_off(server):
    # The test server speaks plain HTTP: a URL without ?tls=false must try TLS and fail, not
    # quietly fall back.
    url = server_url(server=server).difference_update_query(names=["tls"])
    with pytest.raises(exc.DBAPIError, match="(?i)tls|https|certificate|handshake|http response"):
        scalar(url=url, sql="SELECT 1")


def test_path_and_schema_option_choose_the_catalog_and_schema(engine, server, name):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"ATTACH ':memory:' AS {name}"))
        conn.execute(text(f"CREATE SCHEMA {name}.reports"))
    try:
        url = server_url(server=server, schema="reports").set(database=name)
        # Many connects, not one: a driver before adbc-driver-hardcharger 2.5.0 applied the two
        # options in a random order, so one connect in five or so found the schema by luck.
        for _ in range(40):
            assert scalar(url=url, sql="SELECT current_catalog() || '.' || current_schema()") == (
                f"{name}.reports"
            )
        url = server_url(server=server, catalog=name)
        assert scalar(url=url, sql="SELECT current_catalog()") == name
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DETACH {name}"))


def test_a_catalog_that_does_not_exist_is_an_error(server):
    url = server_url(server=server).set(database="no_such_catalog")
    with pytest.raises(exc.DBAPIError, match="no_such_catalog"):
        scalar(url=url, sql="SELECT 1")


def test_hardcharger_settings_in_the_url_apply_to_the_session(server):
    url = server_url(server=server, **{"hardcharger.query_timeout": "77"})
    engine = create_engine(url=url)
    try:
        with engine.connect() as conn:
            driver_connection = conn.connection.driver_connection
            assert driver_connection.get_setting(name="query_timeout") == "77"
    finally:
        engine.dispose()


def test_driver_options_in_the_url_reach_the_driver_by_their_names(server, capfd):
    url = server_url(
        server=server,
        **{"hardcharger.query.result_partitions": "1", "hardcharger.ingest.parts": "1"},
    )
    engine = create_engine(url=url)
    try:
        with engine.connect() as conn:
            database = conn.connection.driver_connection.adbc_database
            assert database.get_option("hardcharger.query.result_partitions") == "1"
            assert database.get_option("hardcharger.ingest.parts") == "1"
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()
    assert "deprecated" not in capfd.readouterr().err


def test_a_driver_option_under_its_old_name_in_the_url_still_works(server, capfd):
    """The dialect hands the driver the current name: the driver prints no deprecation line,
    and the URL's author gets a DeprecationWarning instead."""
    url = server_url(server=server, **{"adbc.chargewire.statement.result_partitions": "1"})
    with pytest.warns(DeprecationWarning, match="hardcharger.query.result_partitions"):
        engine = create_engine(url=url)
    try:
        with engine.connect() as conn:
            database = conn.connection.driver_connection.adbc_database
            assert database.get_option("hardcharger.query.result_partitions") == "1"
    finally:
        engine.dispose()
    assert "deprecated" not in capfd.readouterr().err


def test_a_url_cannot_run_the_browser_command(server, tmp_path):
    """Whatever spelling it uses, a browser command in a URL is refused before anything runs."""
    marker = tmp_path / "marker"
    for key in (
        "hardcharger.oauth.browser",
        "chargewire.oauth.browser",
        "adbc.hardcharger.oauth.browser",
        "adbc.chargewire.oauth.browser",
    ):
        url = server_url(server=server, auth_type="external", **{key: f"touch {marker}"})
        with pytest.raises(exc.ArgumentError, match="can't be set in a URL"):
            scalar(url=url.set(username=None, password=None), sql="SELECT 1")
    assert not marker.exists()


@pytest.mark.parametrize(
    "password", ["p;)\"'@:/% \\n\u00f6\u20ac", "%2F%40already-encoded", "?q=1&x=y#frag"]
)
def test_a_password_with_punctuation_signs_in_and_stays_out_of_errors(password):
    with hardcharger.Server(username="sa_punct", password=password) as punct:
        url = server_url(server=punct)
        # as a URL object, and as the string a configuration file would hold
        for spelled in (url, url.render_as_string(hide_password=False)):
            assert scalar(url=spelled, sql="SELECT HARDCHARGER_USER()") == "sa_punct"
        wrong = url.set(password=password + "x")
        with pytest.raises(exc.DBAPIError, match="invalid_credentials") as caught:
            scalar(url=wrong.render_as_string(hide_password=False), sql="SELECT 1")
        assert password not in str(caught.value)
        missing = url.set(database="no_such_catalog")
        with pytest.raises(exc.DBAPIError, match="no_such_catalog") as caught:
            scalar(url=missing, sql="SELECT 1")
        assert password not in str(caught.value)


def test_connect_args_reach_the_driver(server):
    engine = create_engine(
        url=server_url(server=server),
        connect_args={"session_tag": {"app": "sqlalchemy-hardcharger tests"}},
    )
    try:
        with engine.connect() as conn:
            tag = conn.connection.driver_connection.get_setting(name="session_tag")
        assert tag == '{"app":"sqlalchemy-hardcharger tests"}'
    finally:
        engine.dispose()


def test_an_access_token_signs_in_without_a_password(engine, server):
    with engine.connect() as conn:
        token = conn.connection.driver_connection.access_token
    url = server_url(server=server, access_token=token).set(username=None, password=None)
    assert scalar(url=url, sql="SELECT HARDCHARGER_USER()") == server.username


def test_driver_connection_is_the_drivers_own(engine):
    from adbc_driver_hardcharger import dbapi

    with engine.connect() as conn:
        driver_connection = conn.connection.driver_connection
        assert isinstance(driver_connection, dbapi.Connection)
        assert driver_connection.session_id.startswith("s_")
        # ... and its Arrow interface is there for bulk work
        with driver_connection.cursor() as cur:
            cur.execute(operation="SELECT 1 AS one")
            assert cur.fetch_arrow_table().to_pydict() == {"one": [1]}


# -- TLS --------------------------------------------------------------------------------------


def test_tls_with_the_servers_certificate_trusted(tls_server: hardcharger.Server, tls_files):
    url = server_url(server=tls_server)
    assert "tls" not in url.query
    engine = create_engine(url=url, connect_args={"tls_root_certs": tls_files["cert"]})
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 7")).scalar() == 7
    finally:
        engine.dispose()


def test_tls_refuses_a_certificate_it_was_not_told_to_trust(tls_server: hardcharger.Server):
    with pytest.raises(exc.DBAPIError, match="(?i)certificate"):
        scalar(url=server_url(server=tls_server), sql="SELECT 7")


def test_tls_skip_verify_connects_anyway(tls_server: hardcharger.Server):
    assert scalar(url=server_url(server=tls_server, tls_skip_verify="true"), sql="SELECT 7") == 7
