"""hardcharger:// URLs -> the driver's connect() arguments. No server needed."""

import pytest
from sqlalchemy import create_engine, exc
from sqlalchemy.engine import make_url

from sqlalchemy_hardcharger import HardchargerConnection, HardchargerDialect


def connect_args(url: str) -> dict:
    args, kwargs = HardchargerDialect().create_connect_args(url=make_url(url))
    assert args == []
    return kwargs


def test_entry_points_resolve_both_scheme_spellings():
    for scheme in ("hardcharger", "hardcharger+adbc"):
        engine = create_engine(f"{scheme}://user:secret@db.example.com")
        assert isinstance(engine.dialect, HardchargerDialect)
        assert engine.dialect.name == "hardcharger"
        assert engine.dialect.driver == "adbc"


def test_tls_is_on_by_default_and_the_port_is_left_to_the_driver():
    assert connect_args("hardcharger://user:secret@db.example.com") == {
        "uri": "hardcharger://db.example.com",
        "username": "user",
        "password": "secret",
    }


def test_host_port_and_plaintext():
    assert connect_args("hardcharger://user:secret@127.0.0.1:31337?tls=false") == {
        "uri": "hardcharger://127.0.0.1:31337?tls=false",
        "username": "user",
        "password": "secret",
    }


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_tls_true_spellings(value):
    assert connect_args(f"hardcharger://h?tls={value}")["uri"] == "hardcharger://h"


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_tls_false_spellings(value):
    assert connect_args(f"hardcharger://h?tls={value}")["uri"] == "hardcharger://h?tls=false"


def test_tls_must_be_a_boolean():
    with pytest.raises(exc.ArgumentError, match="URL option 'tls' must be true or false"):
        connect_args("hardcharger://h?tls=maybe")


def test_an_option_error_never_repeats_the_value():
    """A secret pasted into the wrong option must not come back in the error."""
    for url in (
        "hardcharger://h?tls=tok_SECRET",
        "hardcharger://h?tls_skip_verify=tok_SECRET",
        "hardcharger://h?oauth_timeout=tok_SECRET",
        "hardcharger://h?open_browser=tok_SECRET",
        "hardcharger://h?adbc.chargewire.auth.access_token=tok_SECRET",
        "hardcharger://h?bogus=tok_SECRET",
    ):
        with pytest.raises(exc.ArgumentError) as caught:
            connect_args(url)
        assert "tok_SECRET" not in str(caught.value)


def test_ipv6_host_keeps_its_brackets():
    assert connect_args("hardcharger://u:p@[::1]:31337")["uri"] == "hardcharger://[::1]:31337"


def test_password_with_url_reserved_characters_is_decoded():
    assert connect_args("hardcharger://user:p%40ss%2Fw%3Ard@h")["password"] == "p@ss/w:rd"


def test_path_is_the_catalog_and_schema_option_is_the_schema():
    kwargs = connect_args("hardcharger://u:p@h/analytics?schema=sales")
    assert kwargs["catalog"] == "analytics"
    assert kwargs["db_schema"] == "sales"


def test_catalog_option_is_the_same_as_the_path():
    assert connect_args("hardcharger://u:p@h?catalog=analytics")["catalog"] == "analytics"
    assert connect_args("hardcharger://u:p@h/analytics?catalog=analytics")["catalog"] == "analytics"


def test_two_different_catalogs_are_refused():
    with pytest.raises(exc.ArgumentError, match="names two catalogs"):
        connect_args("hardcharger://u:p@h/one?catalog=two")


def test_driver_options_are_passed_with_their_types():
    kwargs = connect_args(
        "hardcharger://h?auth_type=external&oauth_timeout=120&open_browser=false"
        "&share_sign_in=team&resume_timeout=0&tls_skip_verify=true"
        "&tls_root_certs=/etc/ca.pem&tls_client_cert=/etc/c.pem&tls_client_key=/etc/k.pem"
        "&tls_server_name=db.internal&session_tag=%7B%22app%22%3A%22etl%22%7D"
        "&query_tag=%7B%22job%22%3A7%7D"
    )
    assert kwargs == {
        "uri": "hardcharger://h",
        "auth_type": "external",
        "oauth_timeout": 120.0,
        "open_browser": False,
        "share_sign_in": "team",
        "resume_timeout": 0.0,
        "tls_skip_verify": True,
        "tls_root_certs": "/etc/ca.pem",
        "tls_client_cert": "/etc/c.pem",
        "tls_client_key": "/etc/k.pem",
        "tls_server_name": "db.internal",
        "session_tag": '{"app":"etl"}',
        "query_tag": '{"job":7}',
    }


def test_token_sign_in_options():
    assert connect_args("hardcharger://h?access_token=abc")["access_token"] == "abc"
    assert connect_args("hardcharger://h?subject_token=xyz")["subject_token"] == "xyz"


def test_a_url_cannot_name_a_program_to_run():
    """The driver runs open_browser's text as a command during browser sign-in, and URLs are
    often configuration someone else supplies: only true/false is accepted in a URL."""
    assert connect_args("hardcharger://h?open_browser=false")["open_browser"] is False
    assert connect_args("hardcharger://h?open_browser=true")["open_browser"] is True
    with pytest.raises(exc.ArgumentError, match="'open_browser' must be true or false"):
        connect_args("hardcharger://h?auth_type=external&open_browser=touch+/tmp/marker")
    for option in (
        "adbc.hardcharger.oauth.browser",
        "adbc.hardcharger.oauth.sign_in_key",
        "adbc.chargewire.session.id",
        "adbc.chargewire.auth.access_token",
        "adbc.chargewire.tls.skip_verify",
        "adbc.something.new",
    ):
        with pytest.raises(exc.ArgumentError, match="can't be set in a URL"):
            connect_args(f"hardcharger://h?{option}=x")


def test_connect_args_still_take_everything_the_driver_does():
    dialect = HardchargerDialect()
    dialect.dbapi = _FakeDbapi()
    dialect.connect(
        uri="hardcharger://h",
        auth_type="external",
        open_browser="my-browser --new-window",
        db_kwargs={"adbc.chargewire.session.id": "s_1"},
    )
    (call,) = dialect.dbapi.calls
    assert call["open_browser"] == "my-browser --new-window"
    assert call["db_kwargs"] == {"adbc.chargewire.session.id": "s_1"}


def test_number_options_must_be_numbers():
    with pytest.raises(exc.ArgumentError, match="URL option 'oauth_timeout' must be a number"):
        connect_args("hardcharger://h?oauth_timeout=soon")


def test_hardcharger_settings_and_adbc_options():
    kwargs = connect_args(
        "hardcharger://h?hardcharger.query_timeout=60&adbc.chargewire.statement.result_partitions=1"
    )
    assert kwargs["settings"] == {"query_timeout": "60"}
    assert kwargs["db_kwargs"] == {"adbc.chargewire.statement.result_partitions": "1"}


def test_an_unknown_option_is_refused_not_dropped():
    with pytest.raises(
        exc.ArgumentError, match="unknown hardcharger:// URL option 'useEncryption'"
    ):
        connect_args("hardcharger://h?useEncryption=true")


def test_an_option_given_twice_is_refused():
    with pytest.raises(exc.ArgumentError, match="'schema' is given more than once"):
        connect_args("hardcharger://h?schema=a&schema=b")


def test_a_url_needs_a_host_or_a_profile():
    with pytest.raises(exc.ArgumentError, match="needs a host"):
        connect_args("hardcharger://")
    assert connect_args("hardcharger:///?profile=warehouse") == {"profile": "warehouse"}


class _FakeDbapi:
    def __init__(self):
        self.calls = []

    def connect(self, *args, **kwargs):
        self.calls.append(kwargs)
        return object()


def test_browser_sign_in_is_shared_within_one_engine_only():
    first, second = HardchargerDialect(), HardchargerDialect()
    first.dbapi, second.dbapi = _FakeDbapi(), _FakeDbapi()
    for _ in range(2):
        first.connect(uri="hardcharger://h", auth_type="external")
    second.connect(uri="hardcharger://h", auth_type="external")
    scopes = [call["share_sign_in"] for call in first.dbapi.calls]
    other = second.dbapi.calls[0]["share_sign_in"]
    # one engine's connections share a sign-in ...
    assert scopes[0] == scopes[1] and isinstance(scopes[0], str) and len(scopes[0]) > 30
    # ... and another engine (another user of the same process) never does
    assert other != scopes[0]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            {"auth_type": "external", "share_sign_in": False},
            {"auth_type": "external", "share_sign_in": False},
        ),
        (
            {"auth_type": "external", "share_sign_in": "team"},
            {"auth_type": "external", "share_sign_in": "team"},
        ),
        ({"username": "u", "password": "p"}, {"username": "u", "password": "p"}),
    ],
)
def test_an_explicit_sign_in_choice_is_kept(given, expected):
    dialect = HardchargerDialect()
    dialect.dbapi = _FakeDbapi()
    connection = dialect.connect(uri="hardcharger://h", **given)
    assert isinstance(connection, HardchargerConnection)
    assert dialect.dbapi.calls == [{"uri": "hardcharger://h", **expected}]


class _RecordingAdbcConnection:
    def __init__(self):
        self.options = []

    def set_options(self, **options):
        self.options.append(options)


class _RecordingConnection:
    def __init__(self):
        self.adbc_connection = _RecordingAdbcConnection()
        self.statements = []

    def cursor(self):
        raise AssertionError("no SQL is to be sent at connect")


def test_a_schema_of_a_named_catalog_is_set_as_a_driver_option_after_connecting():
    """Not with USE in SQL: the driver re-applies options when the server replaces an idle
    session, and refuses to replace one whose state was set in SQL."""
    dialect = HardchargerDialect()
    dialect.dbapi = _FakeDbapi()
    driver_connection = _RecordingConnection()

    def connect(**kwargs):
        dialect.dbapi.calls.append(kwargs)
        return driver_connection

    dialect.dbapi.connect = connect
    dialect.connect(uri="hardcharger://h", catalog="Lake", db_schema="my sales")
    assert dialect.dbapi.calls == [{"uri": "hardcharger://h", "catalog": "Lake"}]
    assert driver_connection.adbc_connection.options == [{"adbc.connection.db_schema": "my sales"}]


def test_a_schema_alone_is_left_to_the_driver():
    dialect = HardchargerDialect()
    dialect.dbapi = _FakeDbapi()
    dialect.connect(uri="hardcharger://h", db_schema="sales")
    assert dialect.dbapi.calls == [{"uri": "hardcharger://h", "db_schema": "sales"}]
