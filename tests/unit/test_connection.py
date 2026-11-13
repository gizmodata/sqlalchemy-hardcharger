"""HardchargerConnection: BEGIN only when SQLAlchemy begins, COMMIT/ROLLBACK only after a BEGIN."""

import pytest

from sqlalchemy_hardcharger import HardchargerConnection, HardchargerDialect


class _Cursor:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, statement):
        self.owner.statements.append(statement)
        error = self.owner.fail.get(statement)
        if error is not None:
            raise error

    def close(self):
        pass


class _Driver:
    session_id = "s_123"

    def __init__(self):
        self.statements = []
        self.fail = {}
        self.closed = False

    def cursor(self):
        return _Cursor(owner=self)

    def close(self):
        self.closed = True


@pytest.fixture
def driver():
    return _Driver()


@pytest.fixture
def connection(driver):
    return HardchargerConnection(driver_connection=driver)


def test_commit_and_rollback_send_nothing_when_no_transaction_began(connection, driver):
    connection.commit()
    connection.rollback()
    assert driver.statements == []


def test_begin_commit(connection, driver):
    connection.begin()
    assert connection.in_transaction
    connection.begin()  # already begun: nothing more
    connection.commit()
    assert not connection.in_transaction
    assert driver.statements == ["BEGIN", "COMMIT"]


def test_begin_rollback(connection, driver):
    connection.begin()
    connection.rollback()
    connection.rollback()
    assert driver.statements == ["BEGIN", "ROLLBACK"]


def test_autocommit_sends_no_begin(connection, driver):
    connection.autocommit = True
    connection.begin()
    connection.commit()
    connection.rollback()
    assert driver.statements == []


def test_a_failed_commit_leaves_the_rollback_to_reach_the_server(connection, driver):
    driver.fail["COMMIT"] = RuntimeError("Conflict on commit")
    connection.begin()
    with pytest.raises(RuntimeError, match="Conflict on commit"):
        connection.commit()
    assert connection.in_transaction
    connection.rollback()
    assert not connection.in_transaction
    assert driver.statements == ["BEGIN", "COMMIT", "ROLLBACK"]


def test_rollback_ignores_only_no_transaction_is_active(connection, driver):
    driver.fail["ROLLBACK"] = RuntimeError(
        "TransactionContext Error: cannot rollback - no transaction is active"
    )
    connection.begin()
    connection.rollback()
    assert not connection.in_transaction

    driver.fail["ROLLBACK"] = RuntimeError("(session_ended, reason: killed, HTTP 401)")
    connection.begin()
    with pytest.raises(RuntimeError, match="session_ended"):
        connection.rollback()
    assert not connection.in_transaction


def test_other_attributes_are_the_driver_connections(connection, driver):
    assert connection.session_id == "s_123"
    connection.close()
    assert driver.closed


def test_isolation_levels():
    dialect = HardchargerDialect()
    connection = HardchargerConnection(driver_connection=_Driver())
    assert dialect.get_isolation_level_values(dbapi_connection=connection) == (
        "SNAPSHOT",
        "AUTOCOMMIT",
    )
    assert dialect.get_isolation_level(dbapi_connection=connection) == "SNAPSHOT"
    dialect.set_isolation_level(dbapi_connection=connection, level="AUTOCOMMIT")
    assert dialect.get_isolation_level(dbapi_connection=connection) == "AUTOCOMMIT"
    assert dialect.detect_autocommit_setting(dbapi_conn=connection) is True
    dialect.set_isolation_level(dbapi_connection=connection, level="SNAPSHOT")
    assert dialect.get_isolation_level(dbapi_connection=connection) == "SNAPSHOT"


def _error(message: str, status: str):
    import adbc_driver_manager

    return adbc_driver_manager.ProgrammingError(
        message, status_code=adbc_driver_manager.AdbcStatusCode[status]
    )


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        # the server ended the session, or no longer has it
        (
            "UNAUTHENTICATED",
            "... killed ... (session_ended, reason: killed, HTTP 401, request r_1)",
            True,
        ),
        (
            "UNAUTHENTICATED",
            "... (invalid_token, reason: token_revoked, HTTP 401, request r_1)",
            True,
        ),
        (
            "IO",
            "IO: [chargewire] ExecuteQuery: the server no longer has session s_1 (no such session "
            "(session_not_found, HTTP 404, request r_1)); settings and USE made in SQL ...",
            True,
        ),
        ("NOT_FOUND", "... (session_not_found, HTTP 404, request r_1)", True),
        # the transport failed
        (
            "IO",
            "IO: [chargewire] connecting ...: dial tcp 127.0.0.1:9: connect: connection refused",
            True,
        ),
        # errors about the statement: the connection is fine
        (
            "NOT_FOUND",
            "Catalog Error: Table with name t does not exist! (sql_error, HTTP 400, request r_1)",
            False,
        ),
        (
            "INTEGRITY",
            "Constraint Error: Duplicate key (execution_error, HTTP 422, request r_1)",
            False,
        ),
        # ... even when the message quotes data that reads like a lost connection
        (
            "INVALID_ARGUMENT",
            "Conversion Error: Could not convert string 'connection refused' to INT32 "
            "(execution_error, HTTP 422, request r_1)",
            False,
        ),
        (
            "INVALID_ARGUMENT",
            "Conversion Error: Could not convert string '(session_ended, HTTP 401)' to INT32 "
            "(execution_error, HTTP 422, request r_1)",
            False,
        ),
        (
            "INVALID_ARGUMENT",
            'Parser Error: syntax error at or near "EOF" (sql_error, HTTP 400)',
            False,
        ),
    ],
)
def test_is_disconnect_goes_by_the_drivers_status_not_by_words_in_the_message(
    status, message, expected
):
    dialect = HardchargerDialect()
    error = _error(message=message, status=status)
    assert dialect.is_disconnect(e=error, connection=None, cursor=None) is expected


def test_is_disconnect_without_a_status_code_reads_only_the_servers_error_code():
    dialect = HardchargerDialect()
    assert dialect.is_disconnect(
        e=RuntimeError("... (session_ended, reason: killed, HTTP 401, request r_1)"),
        connection=None,
        cursor=None,
    )
    assert not dialect.is_disconnect(
        e=RuntimeError("connection refused"), connection=None, cursor=None
    )


# -- transaction control sent as SQL text -------------------------------------------------------


def test_a_raw_begin_on_an_autocommit_connection_is_rolled_back_at_checkin(connection, driver):
    connection.autocommit = True
    connection.after_transaction_control(keyword="BEGIN")
    assert connection.in_transaction
    connection.rollback()  # what the pool does when the connection comes back
    assert driver.statements == ["ROLLBACK"]
    assert not connection.in_transaction


def test_a_raw_commit_on_an_autocommit_connection_clears_the_raw_begin(connection, driver):
    connection.autocommit = True
    connection.after_transaction_control(keyword="START")
    connection.after_transaction_control(keyword="COMMIT")
    connection.rollback()
    assert driver.statements == []


@pytest.mark.parametrize("keyword", ["COMMIT", "ROLLBACK", "END", "ABORT"])
def test_a_raw_commit_inside_a_sqlalchemy_transaction_begins_the_next_one(
    connection, driver, keyword
):
    connection.begin()
    connection.after_transaction_control(keyword=keyword)
    assert connection.in_transaction  # later work is still inside a transaction
    connection.rollback()
    assert driver.statements == ["BEGIN", "BEGIN", "ROLLBACK"]


@pytest.mark.parametrize(
    ("statement", "keyword"),
    [
        ("BEGIN", "BEGIN"),
        ("  begin transaction", "BEGIN"),
        ("START TRANSACTION", "START"),
        ("-- note\nCOMMIT", "COMMIT"),
        ("/* a */ /* b */ rollback;", "ROLLBACK"),
        ("END", "END"),
        ("SELECT 1", None),
        ("SELECT CASE WHEN a THEN 1 END FROM t", None),
        ("INSERT INTO t VALUES ('BEGIN')", None),
        ("BEGINNING_OF_TIME()", None),
        ("CREATE TABLE commits (a INT)", None),
    ],
)
def test_only_a_statement_that_is_transaction_control_counts(statement, keyword):
    from sqlalchemy_hardcharger.dialect import _TRANSACTION_CONTROL

    match = _TRANSACTION_CONTROL.match(statement)
    assert (match.group(1).upper() if match else None) == keyword
