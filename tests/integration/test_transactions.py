"""Transactions, autocommit, lost sessions: against a real server."""

import pytest
from sqlalchemy import Column, Integer, String, Table, create_engine, exc, func, select, text

from sqlalchemy_hardcharger import HardchargerConnection


@pytest.fixture
def items(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("v", String),
    )
    metadata.create_all(bind=engine)
    return table


def count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar()


@pytest.fixture
def control_statements(monkeypatch):
    """Every BEGIN/COMMIT/ROLLBACK the dialect sends, in order."""
    sent = []
    original = HardchargerConnection._run

    def spy(self, statement):
        sent.append(statement)
        return original(self, statement=statement)

    monkeypatch.setattr(HardchargerConnection, "_run", spy)
    return sent


def test_commit_makes_rows_visible_to_other_connections(engine, items):
    with engine.connect() as conn:
        conn.execute(items.insert().values(id=1, v="a"))
        assert count(engine=engine, table=items) == 0  # not committed yet
        conn.commit()
    assert count(engine=engine, table=items) == 1


def test_rollback_discards_rows(engine, items):
    with engine.connect() as conn:
        conn.execute(items.insert().values(id=1, v="a"))
        conn.rollback()
    assert count(engine=engine, table=items) == 0


def test_leaving_a_begin_block_on_an_error_rolls_back(engine, items):
    with pytest.raises(RuntimeError, match="boom"):
        with engine.begin() as conn:
            conn.execute(items.insert().values(id=1, v="a"))
            raise RuntimeError("boom")
    assert count(engine=engine, table=items) == 0


def test_ddl_is_transactional(engine, name):
    with engine.connect() as conn:
        conn.execute(text(f"CREATE TABLE {name} (a INTEGER)"))
        conn.rollback()
        with pytest.raises(exc.DBAPIError, match=f"Table with name {name} does not exist"):
            conn.execute(text(f"SELECT * FROM {name}"))


def test_a_failed_statement_aborts_the_transaction_until_rollback(engine, items):
    with engine.connect() as conn:
        conn.execute(items.insert().values(id=1, v="a"))
        with pytest.raises(exc.IntegrityError, match="(?i)duplicate key"):
            conn.execute(items.insert().values(id=1, v="again"))
        with pytest.raises(exc.DBAPIError, match="Current transaction is aborted"):
            conn.execute(select(items)).all()
        conn.rollback()
        assert conn.execute(select(func.count()).select_from(items)).scalar() == 0


def test_begin_is_sent_at_the_first_statement_not_at_checkout(engine, items, control_statements):
    with engine.connect() as conn:
        assert control_statements == []  # checked out: nothing sent
        raw = conn.connection.dbapi_connection
        assert isinstance(raw, HardchargerConnection) and not raw.in_transaction
        conn.execute(select(items)).all()
        assert control_statements == ["BEGIN"]
        assert raw.in_transaction
        conn.commit()
        assert control_statements == ["BEGIN", "COMMIT"]
        assert not raw.in_transaction
    # returned to the pool with no transaction open: no ROLLBACK to send
    assert control_statements == ["BEGIN", "COMMIT"]


def test_a_connection_that_ran_nothing_sends_nothing(engine, control_statements):
    with engine.connect():
        pass
    assert control_statements == []


def test_a_brand_new_connection_begins_nothing_until_its_first_statement(url, control_statements):
    fresh = create_engine(url=url)
    try:
        with fresh.connect() as conn:  # the engine's first connection: it reads the version
            assert control_statements == []
            assert not conn.connection.dbapi_connection.in_transaction
        fresh.dispose()
        with fresh.connect() as conn:  # a second, newly opened one
            assert control_statements == []
            assert not conn.connection.dbapi_connection.in_transaction
            conn.execute(text("SELECT 1"))
            assert control_statements == ["BEGIN"]
        assert control_statements == ["BEGIN", "ROLLBACK"]
    finally:
        fresh.dispose()


def test_returning_to_the_pool_mid_transaction_rolls_back(engine, items, control_statements):
    with engine.connect() as conn:
        conn.execute(items.insert().values(id=1, v="a"))
    assert control_statements == ["BEGIN", "ROLLBACK"]
    assert count(engine=engine, table=items) == 0


def test_no_transaction_is_left_open_on_the_server_by_a_pooled_connection(url, items):
    pooled = create_engine(url=url, pool_size=1, max_overflow=0)
    other = create_engine(url=url)
    try:
        with pooled.connect() as conn:
            conn.execute(items.insert().values(id=1, v="a"))
            conn.commit()
            conn.execute(items.update().values(v="b"))
            session_id = conn.connection.driver_connection.session_id
        # The connection rests in the pool, its update rolled back. Were its transaction still
        # open on the server, this update of the same row from another session would conflict
        # (see test_write_conflict_between_two_transactions).
        with other.connect() as conn:
            assert conn.connection.driver_connection.session_id != session_id
            conn.execute(items.update().values(v="c"))
            conn.commit()
            assert conn.execute(select(items.c.v)).scalar() == "c"
    finally:
        pooled.dispose()
        other.dispose()


def test_autocommit_isolation_level(engine, items, control_statements):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        assert conn.get_isolation_level() == "AUTOCOMMIT"
        conn.execute(items.insert().values(id=1, v="a"))
        assert count(engine=engine, table=items) == 1  # visible with no commit
    assert control_statements.count("BEGIN") == 1  # only count()'s own connection began one
    with engine.connect() as conn:
        assert conn.get_isolation_level() == "SNAPSHOT"  # the pool reset the level


def test_engine_wide_autocommit(url, items, engine):
    autocommit_engine = create_engine(url=url, isolation_level="AUTOCOMMIT")
    try:
        with autocommit_engine.connect() as conn:
            conn.execute(items.insert().values(id=1, v="a"))
        assert count(engine=engine, table=items) == 1
    finally:
        autocommit_engine.dispose()


def test_an_unknown_isolation_level_names_the_valid_ones(engine):
    with pytest.raises(exc.ArgumentError, match="SNAPSHOT, AUTOCOMMIT"):
        with engine.connect().execution_options(isolation_level="SERIALIZABLE"):
            pass


def test_savepoints_are_refused_with_the_reason(engine):
    with engine.connect() as conn:
        with pytest.raises(exc.InvalidRequestError, match="has no savepoints"):
            conn.begin_nested()


def test_write_conflict_between_two_transactions(engine, items):
    with engine.begin() as conn:
        conn.execute(items.insert().values(id=1, v="a"))
    with engine.connect() as first, engine.connect() as second:
        first.execute(items.update().values(v="first"))
        with pytest.raises(exc.DBAPIError, match="(?i)conflict"):
            second.execute(items.update().values(v="second"))
        second.rollback()
        first.commit()
    with engine.connect() as conn:
        assert conn.execute(select(items.c.v)).scalar() == "first"


def test_a_killed_session_is_a_disconnect_and_the_pool_recovers(url):
    engine = create_engine(url=url, pool_size=1, max_overflow=0)
    admin = create_engine(url=url)
    try:
        with engine.connect() as conn:
            session_id = conn.connection.driver_connection.session_id
            conn.execute(text("SELECT 1")).scalar()
            conn.commit()
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as killer:
                killer.execute(text(f"KILL SESSION '{session_id}'"))
            with pytest.raises(exc.DBAPIError, match="session_ended") as caught:
                conn.execute(text("SELECT 1")).scalar()
            assert caught.value.connection_invalidated is True
        # The pool threw the dead connection away: the next checkout is a new session.
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 2")).scalar() == 2
            assert conn.connection.driver_connection.session_id != session_id
    finally:
        engine.dispose()
        admin.dispose()


def test_pool_pre_ping_replaces_a_killed_session(url):
    engine = create_engine(url=url, pool_size=1, max_overflow=0, pool_pre_ping=True)
    admin = create_engine(url=url)
    try:
        with engine.connect() as conn:
            session_id = conn.connection.driver_connection.session_id
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as killer:
            killer.execute(text(f"KILL SESSION '{session_id}'"))
        with engine.connect() as conn:  # the ping finds it dead; no error reaches the caller
            assert conn.execute(text("SELECT 3")).scalar() == 3
    finally:
        engine.dispose()
        admin.dispose()


def test_a_raw_driver_connection_from_a_creator_is_refused_with_the_fix(server, url):
    from adbc_driver_hardcharger import dbapi

    def raw():
        return dbapi.connect(
            uri=f"hardcharger://{server.host}:{server.port}?tls=false",
            username=server.username,
            password=server.password,
        )

    engine = create_engine(url=url, creator=raw)
    try:
        with pytest.raises(exc.InvalidRequestError, match="HardchargerConnection"):
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()

    wrapped = create_engine(url=url, creator=lambda: HardchargerConnection(driver_connection=raw()))
    try:
        with wrapped.begin() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        wrapped.dispose()
