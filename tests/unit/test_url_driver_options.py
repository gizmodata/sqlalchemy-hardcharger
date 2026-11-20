"""Which driver options a hardcharger:// URL can set, and passwords in a URL. No server needed.

A URL is often configuration that someone else supplies (a BI tool's connection form), so it may
set only the driver options that tune statements, by their exact names. Every other driver
option names a program to run, a file to read, or a session or token to adopt, and stays with the
application's own code (``connect_args``), under every spelling the driver accepts:
``hardcharger.*`` (adbc-driver-hardcharger 2.7.0), ``chargewire.*``, and the names from before
2.7.0 (``adbc.hardcharger.*``, ``adbc.chargewire.*``, ``adbc.warp.*``). Since the driver's options
share the ``hardcharger.`` prefix with session settings (``?hardcharger.query_timeout=60``), a
``hardcharger.X`` key must never reach the driver as a setting that is really an option either.
"""

import contextlib
import io
import warnings

import pytest
from adbc_driver_hardcharger import ConnectionOptions, DatabaseOptions, StatementOptions, _legacy
from sqlalchemy import exc
from sqlalchemy.engine import URL, make_url

from sqlalchemy_hardcharger import HardchargerDialect
from sqlalchemy_hardcharger.dialect import _ADBC_URL_OPTIONS, _ADBC_URL_OPTIONS_RENAMED

# Written out, not derived from the dialect: widening what a URL can set must change this test.
ALLOWED = {
    "hardcharger.query.async",
    "hardcharger.query.retain_result",
    "hardcharger.query.timeout_seconds",
    "hardcharger.query.result_partitions",
    "hardcharger.query.result_order",
    "hardcharger.ipc.compression",
    "hardcharger.ingest.parts",
    "hardcharger.retry.max_attempts",
    "hardcharger.session.reconnect",
}
ALLOWED_BEFORE_2_7 = {
    "adbc.chargewire.statement.async": "hardcharger.query.async",
    "adbc.chargewire.statement.retain_result": "hardcharger.query.retain_result",
    "adbc.chargewire.statement.timeout_seconds": "hardcharger.query.timeout_seconds",
    "adbc.chargewire.statement.result_partitions": "hardcharger.query.result_partitions",
    "adbc.chargewire.statement.result_order": "hardcharger.query.result_order",
    "adbc.chargewire.ipc_compression": "hardcharger.ipc.compression",
    "adbc.chargewire.ingest.parts": "hardcharger.ingest.parts",
    "adbc.hardcharger.ingest.parts": "hardcharger.ingest.parts",
    "adbc.chargewire.retry.max_attempts": "hardcharger.retry.max_attempts",
    "adbc.chargewire.session.reconnect": "hardcharger.session.reconnect",
}

# Options that name a program, a file, a session or a token, under each spelling: the sweep
# below must cover every one of them, whatever the driver's own lists say.
MUST_REFUSE = {
    # the browser command (run as a program) and the sign-in that shares it
    "hardcharger.oauth.browser",
    "chargewire.oauth.browser",
    "adbc.hardcharger.oauth.browser",
    "adbc.chargewire.oauth.browser",
    "adbc.warp.oauth.browser",
    "hardcharger.oauth.share_sign_in",
    "hardcharger.oauth.sign_in_key",
    "adbc.hardcharger.oauth.sign_in_key",
    "hardcharger.auth.type",
    "chargewire.auth.type",
    "adbc.hardcharger.auth_type",
    "adbc.chargewire.auth.type",
    # TLS files and PEM text, and turning verification off
    "hardcharger.tls.root_certs",
    "hardcharger.tls.client_cert",
    "hardcharger.tls.client_key",
    "hardcharger.tls.server_name",
    "hardcharger.tls.skip_verify",
    "chargewire.tls.root_certs",
    "chargewire.tls.client_key",
    "chargewire.tls.skip_verify",
    "adbc.chargewire.tls.root_certs",
    "adbc.chargewire.tls.client_cert",
    "adbc.chargewire.tls.client_key",
    "adbc.chargewire.tls.skip_verify",
    "adbc.warp.tls.root_certs",
    # tokens
    "hardcharger.auth.access_token",
    "hardcharger.auth.subject_token",
    "chargewire.auth.access_token",
    "chargewire.auth.subject_token",
    "adbc.chargewire.auth.access_token",
    "adbc.chargewire.auth.subject_token",
    "adbc.warp.auth.access_token",
    # sessions to adopt or keep
    "hardcharger.session.id",
    "hardcharger.session.close_on_release",
    "chargewire.session.id",
    "adbc.chargewire.session.id",
    "adbc.warp.session.id",
    # settings and tags as driver options (a URL has its own forms for those)
    "hardcharger.setting.query_timeout",
    "hardcharger.global_setting.query_timeout",
    "adbc.hardcharger.setting.query_timeout",
    "adbc.hardcharger.global_setting.query_timeout",
    "hardcharger.session.tag",
    "hardcharger.session.query_tag",
    "hardcharger.query.tag",
    "adbc.chargewire.statement.query_tag",
    # resuming (a URL keyword of its own, resume_timeout)
    "hardcharger.retry.resume_timeout_seconds",
    "adbc.hardcharger.resume_timeout",
    # an old prefix with the current name after it, which the driver also reads
    "adbc.chargewire.query.async",
    "adbc.chargewire.oauth.timeout_seconds",
    # the same key under another letter case, never matched loosely
    "HARDCHARGER.oauth.browser",
    "Hardcharger.session.id",
}


def every_driver_option_name() -> set[str]:
    """Every option name adbc-driver-hardcharger accepts, under every spelling it reads."""
    names: set[str] = set()
    for options in (DatabaseOptions, ConnectionOptions, StatementOptions):
        for member in options:
            key = member.value
            names.add(key + "query_timeout" if key.endswith(".") else key)
    for old in _legacy._OWN_RENAMED:
        names.add(_legacy._OLD_OWN_PREFIX + old)
    for prefix in ("setting.", "global_setting."):
        names.add(_legacy._OLD_OWN_PREFIX + prefix + "query_timeout")
    for old, current in _legacy._CHARGEWIRE_RENAMED.items():
        for prefix in _legacy._OLD_CHARGEWIRE_PREFIXES:
            names.add(prefix + old)
            names.add(prefix + current)
        names.add(_legacy._CHARGEWIRE_PREFIX + current)
    return names


REFUSED = sorted((every_driver_option_name() | MUST_REFUSE) - ALLOWED - set(ALLOWED_BEFORE_2_7))


def connect_args(url) -> dict:
    args, kwargs = HardchargerDialect().create_connect_args(url=make_url(url))
    assert args == []
    return kwargs


def test_the_url_allowlist_is_exactly_these_names():
    assert set(_ADBC_URL_OPTIONS) == ALLOWED
    assert _ADBC_URL_OPTIONS_RENAMED == ALLOWED_BEFORE_2_7


def test_the_allowlisted_names_are_the_drivers_current_ones():
    """No deprecation line on stderr, and no option the driver doesn't know."""
    for name in ALLOWED:
        assert _legacy.current_key(key=name) == (name, 0)
    # (the driver says once on stderr that each old name is deprecated: keep that out of the run)
    with contextlib.redirect_stderr(new_target=io.StringIO()):
        for old, current in ALLOWED_BEFORE_2_7.items():
            assert _legacy.current_key(key=old)[0] == current


def test_the_sweep_covers_every_option_the_driver_has():
    names = every_driver_option_name()
    assert len(names) > 100
    assert MUST_REFUSE - {"HARDCHARGER.oauth.browser", "Hardcharger.session.id"} <= names
    assert ALLOWED <= names


@pytest.mark.parametrize("option", REFUSED)
def test_no_other_driver_option_can_be_set_in_a_url(option):
    with pytest.raises(exc.ArgumentError) as caught:
        connect_args(f"hardcharger://h?{option}=tok_SECRET")
    message = str(caught.value)
    assert "tok_SECRET" not in message
    if option.startswith(("hardcharger.", "chargewire.", "adbc.")):
        assert f"the driver option {option!r} can't be set in a URL" in message
        assert "connect_args" in message


@pytest.mark.parametrize("option", REFUSED)
def test_a_refused_option_is_refused_with_settings_and_allowed_options_around_it(option):
    """Nothing about the other options in a URL lets one through."""
    url = (
        "hardcharger://u:p@h?hardcharger.query_timeout=60&hardcharger.query.async=true"
        f"&{option}=tok_SECRET&auth_type=external&open_browser=false"
    )
    with pytest.raises(exc.ArgumentError):
        connect_args(url)


def test_a_dotted_hardcharger_name_is_never_a_session_setting():
    """The driver's options under hardcharger. all have a dot after it; settings never do, so
    no dotted name can become a setting (which the driver would send as SET SESSION)."""
    for option in [
        *REFUSED,
        "hardcharger.hardcharger.oauth.browser",
        "hardcharger.hardcharger.session.id",
        "hardcharger.some.future_option",
        "hardcharger.query_timeout.x",
    ]:
        if option.startswith("hardcharger."):
            with pytest.raises(exc.ArgumentError, match="can't be set in a URL"):
                connect_args(f"hardcharger://h?{option}=x")


def test_a_percent_encoded_key_is_refused_too():
    for key in (
        "hardcharger%2Eoauth%2Ebrowser",
        "hardcharger.session%2Eid",
        "chargewire.oauth%2Ebrowser",
    ):
        with pytest.raises(exc.ArgumentError, match="can't be set in a URL"):
            connect_args(f"hardcharger://h?{key}=x")


def test_settings_keep_their_meaning():
    kwargs = connect_args(
        "hardcharger://h?hardcharger.query_timeout=60&hardcharger.hardcharger.memory_limit=1GB"
    )
    assert kwargs["settings"] == {"query_timeout": "60", "hardcharger.memory_limit": "1GB"}
    assert "db_kwargs" not in kwargs


@pytest.mark.parametrize("option", sorted(ALLOWED))
def test_an_allowlisted_option_reaches_the_driver_by_its_name(option):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        kwargs = connect_args(f"hardcharger://h?{option}=1")
    assert kwargs == {"uri": "hardcharger://h", "db_kwargs": {option: "1"}}


@pytest.mark.parametrize(("old", "current"), sorted(ALLOWED_BEFORE_2_7.items()))
def test_an_allowlisted_option_under_its_old_name_keeps_its_meaning(old, current):
    with pytest.warns(DeprecationWarning) as caught:
        kwargs = connect_args(f"hardcharger://h?{old}=tok_SECRET")
    assert kwargs == {"uri": "hardcharger://h", "db_kwargs": {current: "tok_SECRET"}}
    (warning,) = caught
    assert str(warning.message) == f"the URL option {old!r} is deprecated; use {current!r}"


def test_one_option_under_two_names_is_refused():
    for first, second in (
        ("hardcharger.ingest.parts", "adbc.chargewire.ingest.parts"),
        ("adbc.chargewire.ingest.parts", "adbc.hardcharger.ingest.parts"),
        ("hardcharger.query.result_partitions", "adbc.chargewire.statement.result_partitions"),
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            with pytest.raises(exc.ArgumentError, match="twice") as caught:
                connect_args(f"hardcharger://h?{first}=tok_SECRET1&{second}=tok_SECRET8")
        assert "tok_SECRET" not in str(caught.value)


def test_the_driver_gets_what_the_url_says_and_nothing_else():
    """From URL to the driver's connect() call, through the dialect's own connect()."""
    calls = []

    class FakeDbapi:
        @staticmethod
        def connect(*args, **kwargs):
            calls.append(kwargs)
            return object()

    dialect = HardchargerDialect()
    dialect.dbapi = FakeDbapi()
    args, kwargs = dialect.create_connect_args(
        url=make_url(
            "hardcharger://u:p@h:31337/lake?tls=false&schema=s&hardcharger.query_timeout=60"
            "&hardcharger.query.result_partitions=1&hardcharger.ingest.parts=1"
            "&auth_type=password&open_browser=false&session_tag=%7B%7D"
        )
    )
    dialect.connect(*args, **kwargs)
    assert calls == [
        {
            "uri": "hardcharger://h:31337?tls=false",
            "username": "u",
            "password": "p",
            "catalog": "lake",
            "db_schema": "s",
            "auth_type": "password",
            "open_browser": False,
            "session_tag": "{}",
            "settings": {"query_timeout": "60"},
            "db_kwargs": {
                "hardcharger.query.result_partitions": "1",
                "hardcharger.ingest.parts": "1",
            },
        }
    ]


# -- passwords ------------------------------------------------------------------------------

PASSWORDS = [
    "p;)\"'@:/%",
    "semi;colon",
    "close)paren",
    'double"quote',
    "single'quote",
    "at@sign",
    "colon:colon",
    "slash/slash",
    "percent%41",
    "%2F%40already-encoded",
    "with space",
    "back\\slash",
    "nön-äscii-€",
    "?query=like&x=y#frag",
    "",
]


@pytest.mark.parametrize("password", PASSWORDS)
def test_a_password_round_trips_through_a_url(password):
    url = URL.create(drivername="hardcharger", username="u", password=password, host="h")
    for spelled in (url, url.render_as_string(hide_password=False)):
        assert connect_args(spelled)["password"] == password


@pytest.mark.parametrize("password", [p for p in PASSWORDS if p])
def test_a_password_never_appears_in_an_error(password):
    url = URL.create(drivername="hardcharger", username="u", password=password, host="h")
    for bad in (
        {"hardcharger.oauth.browser": "x"},
        {"tls": "maybe"},
        {"bogus": "x"},
        {"hardcharger.ingest.parts": "1", "adbc.chargewire.ingest.parts": "2"},
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            with pytest.raises(exc.ArgumentError) as caught:
                connect_args(url.update_query_dict(bad).render_as_string(hide_password=False))
        assert password not in str(caught.value)
    assert password not in str(url)
    assert password not in repr(url)
