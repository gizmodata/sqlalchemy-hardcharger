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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("... (session_ended, reason: killed, HTTP 401, request r_1)", True),
        ("... (session_not_found, HTTP 404, request r_1)", True),
        ("... (invalid_token, reason: token_revoked, HTTP 401, request r_1)", True),
        (
            "IO: [chargewire] connecting ...: dial tcp 127.0.0.1:9: connect: connection refused",
            True,
        ),
        (
            'Parser Error: syntax error at or near "EOF" LINE 1: SELECT EOF (sql_error, HTTP 400)',
            False,
        ),
        (
            "Catalog Error: Table with name t does not exist! (sql_error, HTTP 400, request r_1)",
            False,
        ),
        ("Constraint Error: Duplicate key (execution_error, HTTP 422, request r_1)", False),
    ],
)
def test_is_disconnect(message, expected):
    dialect = HardchargerDialect()
    assert dialect.is_disconnect(e=RuntimeError(message), connection=None, cursor=None) is expected
