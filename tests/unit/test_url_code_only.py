"""What only the application's code can set, never a hardcharger:// URL. No server needed.

A URL is often configuration that someone else supplies (a BI tool's connection form, a dlt or
Superset setting), and the application runs it on its own machine. So a URL can't load the
machine's connection profile (with the credentials in it), read files there (TLS client
certificate and key, CA certificates), or join a browser sign-in the application shares: those
are create_engine(connect_args={...}). The tests here failed before 1.2.0, when a URL could set
all of them (the profile one sent the profile's password to the URL's host).
"""

import contextlib
import io

import pytest
from adbc_driver_hardcharger import _legacy
from sqlalchemy import create_engine, exc
from sqlalchemy.engine import make_url

from sqlalchemy_hardcharger import HardchargerDialect
from sqlalchemy_hardcharger.dialect import _ADBC_URL_OPTIONS, _URL_OPTIONS

# Written out: a URL may set exactly these connect() keywords (besides tls, catalog, the
# hardcharger.<setting> settings and the tuning driver options).
URL_KEYWORDS = {
    "auth_type",
    "access_token",
    "subject_token",
    "oauth_timeout",
    "open_browser",
    "share_sign_in",
    "resume_timeout",
    "session_tag",
    "query_tag",
    "tls_skip_verify",
    "tls_server_name",
    "schema",
}
CODE_ONLY = ["profile", "tls_root_certs", "tls_client_cert", "tls_client_key"]


def connect_args(url: str) -> dict:
    args, kwargs = HardchargerDialect().create_connect_args(url=make_url(url))
    assert args == []
    return kwargs


class FakeDbapi:
    paramstyle = "qmark"

    def __init__(self):
        self.calls = []

    def connect(self, *args, **kwargs):
        self.calls.append(kwargs)
        return object()


def driver_call(url: str, **connect_args) -> dict:
    """The keyword arguments the driver's connect() gets for this URL and connect_args, through
    create_engine as an application calls it."""
    engine = create_engine(url, connect_args=connect_args, module=FakeDbapi())
    try:
        engine.pool._creator()
        (call,) = engine.dialect.dbapi.calls
    finally:
        engine.dispose()
    return call


def test_the_url_keywords_are_exactly_these():
    assert set(_URL_OPTIONS) == URL_KEYWORDS


@pytest.mark.parametrize("option", CODE_ONLY)
def test_a_url_cannot_set_a_code_only_option(option):
    for url in (
        f"hardcharger://h?{option}=/home/app/SECRET_PATH",
        f"hardcharger://u:p@h:31337?tls=false&{option}=SECRET_PATH",
        f"hardcharger:///?{option}=SECRET_PATH",
    ):
        with pytest.raises(exc.ArgumentError) as caught:
            connect_args(url)
        message = str(caught.value)
        assert f"the option {option!r} can't be set in a URL" in message
        assert f'connect_args={{"{option}": ...}}' in message
        assert "SECRET_PATH" not in message


@pytest.mark.parametrize("option", CODE_ONLY)
def test_connect_args_still_set_a_code_only_option(option):
    call = driver_call("hardcharger://u:p@h", **{option: "/etc/hardcharger/x"})
    assert call[option] == "/etc/hardcharger/x"


def test_a_url_attacker_host_plus_the_hosts_profile_is_refused():
    """The reviewer's case: the URL names its own host and the machine's profile, whose stored
    user name and password the driver would then send to that host."""
    with pytest.raises(exc.ArgumentError, match="'profile' can't be set in a URL"):
        create_engine("hardcharger://attacker.example:443/?profile=prod", module=FakeDbapi())


def test_a_url_without_a_host_takes_the_profile_from_connect_args_only():
    assert driver_call("hardcharger://", profile="warehouse") == {"profile": "warehouse"}
    with pytest.raises(exc.ArgumentError, match="needs a host"):
        driver_call("hardcharger://u:p@/lake")


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "finance-team", "SECRET_NAME"])
def test_a_url_cannot_share_a_sign_in(value):
    """share_sign_in=true joins the process's unnamed shared sign-in, and a name joins another
    engine's: a URL may only turn sharing off."""
    with pytest.raises(exc.ArgumentError, match="'share_sign_in' can only be false") as caught:
        connect_args(f"hardcharger://h?auth_type=external&share_sign_in={value}")
    assert "SECRET_NAME" not in str(caught.value)


def test_a_url_may_turn_sign_in_sharing_off():
    call = driver_call("hardcharger://h?auth_type=external&share_sign_in=false")
    assert call["share_sign_in"] is False
    # ... and without it, the engine's own sign-in scope applies, which a URL can't name
    call = driver_call("hardcharger://h?auth_type=external")
    assert call["share_sign_in"].startswith("sqlalchemy-hardcharger-")


def test_connect_args_still_share_a_sign_in():
    call = driver_call("hardcharger://h?auth_type=external", share_sign_in="finance-team")
    assert call["share_sign_in"] == "finance-team"


def test_one_setting_twice_with_different_values_is_refused():
    for query in (
        "hardcharger.query_timeout=1&hardcharger.hardcharger.query_timeout=2",
        "hardcharger.hardcharger.query_timeout=2&hardcharger.query_timeout=1",
    ):
        with pytest.raises(exc.ArgumentError, match="'query_timeout' twice") as caught:
            connect_args(f"hardcharger://h?{query}")
        assert "=1" not in str(caught.value) and "=2" not in str(caught.value)
    kwargs = connect_args(
        "hardcharger://h?hardcharger.query_timeout=1&hardcharger.hardcharger.query_timeout=1"
    )
    assert kwargs["settings"] == {"query_timeout": "1"}


def test_connect_args_db_kwargs_merge_with_the_urls():
    url = "hardcharger://u:p@h?hardcharger.query.result_partitions=2&hardcharger.ingest.parts=1"
    assert driver_call(url)["db_kwargs"] == {
        "hardcharger.query.result_partitions": "2",
        "hardcharger.ingest.parts": "1",
    }
    call = driver_call(url, db_kwargs={"hardcharger.retry.max_attempts": "3"})
    assert call["db_kwargs"] == {
        "hardcharger.query.result_partitions": "2",
        "hardcharger.ingest.parts": "1",
        "hardcharger.retry.max_attempts": "3",
    }


@pytest.mark.parametrize(
    "code_name",
    [
        "hardcharger.ingest.parts",
        "chargewire.ingest.parts",
        "adbc.chargewire.ingest.parts",
        "adbc.hardcharger.ingest.parts",
        "adbc.warp.ingest.parts",
    ],
)
def test_connect_args_win_over_the_url_under_any_name(code_name):
    """The URL's value is dropped, not sent beside the code's under another name (where the
    driver would prefer the current name, the URL's)."""
    call = driver_call("hardcharger://u:p@h?hardcharger.ingest.parts=1", db_kwargs={code_name: "8"})
    assert call["db_kwargs"] == {code_name: "8"}


def test_every_spelling_the_merge_knows_is_one_the_driver_reads():
    from sqlalchemy_hardcharger.dialect import _URL_OPTION_SPELLINGS

    with contextlib.redirect_stderr(new_target=io.StringIO()):
        for option in _ADBC_URL_OPTIONS:
            spellings = _URL_OPTION_SPELLINGS[option]
            assert option in spellings and len(spellings) >= 4
            for name in spellings:
                assert _legacy.current_key(key=name)[0] == option, name


def test_connect_args_settings_merge_with_the_urls_and_win():
    url = "hardcharger://u:p@h?hardcharger.query_timeout=5&hardcharger.threads=2"
    assert driver_call(url)["settings"] == {"query_timeout": "5", "threads": "2"}
    call = driver_call(url, settings={"memory_limit": "1GB", "query_timeout": 60})
    assert call["settings"] == {"threads": "2", "memory_limit": "1GB", "query_timeout": 60}
    call = driver_call(url, settings={"hardcharger.threads": 4})
    assert call["settings"] == {"query_timeout": "5", "hardcharger.threads": 4}
