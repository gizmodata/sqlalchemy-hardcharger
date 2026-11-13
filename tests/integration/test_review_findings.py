"""Behaviour an independent pre-release review found wrong, each pinned against a real server."""

import datetime
import decimal

import hardcharger
import pytest
from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    Identity,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Time,
    cast,
    create_engine,
    exc,
    func,
    inspect,
    literal_column,
    select,
    text,
)

from sqlalchemy_hardcharger import HUGEINT, UHUGEINT, Struct

from .conftest import PASSWORD, USERNAME, server_url

TOKYO = datetime.timezone(datetime.timedelta(hours=9))


# -- values that depended on what else was in the batch ----------------------------------------


@pytest.fixture
def tokyo_engine(url):
    """An engine whose sessions are in Asia/Tokyo (UTC+9), so local time is never UTC."""
    engine = create_engine(url=url.update_query_dict({"hardcharger.query_timeout": "60"}))

    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def set_time_zone(dbapi_connection, record):
        cursor = dbapi_connection.driver_connection.cursor()
        cursor.execute("SET TimeZone = 'Asia/Tokyo'")
        cursor.close()

    yield engine
    engine.dispose()


def test_naive_timestamps_store_the_wall_clock_whatever_the_batch(tokyo_engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("at", DateTime),
    )
    metadata.create_all(bind=tokyo_engine)
    naive = datetime.datetime(2024, 1, 1, 12, 0)
    aware = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    with tokyo_engine.begin() as conn:
        conn.execute(table.insert(), [{"id": 1, "at": aware}])  # alone
        conn.execute(table.insert(), [{"id": 2, "at": naive}, {"id": 3, "at": aware}])  # mixed
        conn.execute(table.insert(), [{"id": 4, "at": aware}, {"id": 5, "at": naive}])
        # a date first in a batch must not cut the times that follow it
        conn.execute(
            table.insert(),
            [
                {"id": 6, "at": datetime.date(2024, 1, 1)},
                {"id": 7, "at": datetime.datetime(2024, 1, 2, 3, 4)},
            ],
        )
        stored = dict(conn.execute(text(f'SELECT id, CAST("at" AS VARCHAR) FROM {name}')).all())
    assert {stored[i] for i in (1, 2, 3, 4, 5)} == {"2024-01-01 12:00:00"}
    assert stored[6] == "2024-01-01 00:00:00"
    assert stored[7] == "2024-01-02 03:04:00"


def test_zoned_timestamps_store_the_instant_whatever_the_batch(tokyo_engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("at", DateTime(timezone=True)),
    )
    metadata.create_all(bind=tokyo_engine)
    naive = datetime.datetime(2024, 1, 1, 12, 0)  # read in the session's zone: 12:00 Tokyo
    aware = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)  # 21:00 Tokyo
    with tokyo_engine.begin() as conn:
        conn.execute(table.insert(), [{"id": 1, "at": aware}])
        conn.execute(table.insert(), [{"id": 2, "at": naive}])
        conn.execute(table.insert(), [{"id": 3, "at": naive}, {"id": 4, "at": aware}])
        conn.execute(table.insert(), [{"id": 5, "at": aware}, {"id": 6, "at": naive}])
        instants = dict(conn.execute(text(f'SELECT id, epoch_ms("at") FROM {name}')).all())
        read = dict(conn.execute(select(table.c.id, table.c.at)).all())
        found = (
            conn.execute(select(table.c.id).where(table.c.at == aware).order_by(table.c.id))
            .scalars()
            .all()
        )
    utc_noon = int(aware.timestamp() * 1000)
    tokyo_noon = int(naive.replace(tzinfo=TOKYO).timestamp() * 1000)
    assert [instants[i] for i in (1, 4, 5)] == [utc_noon] * 3
    assert [instants[i] for i in (2, 3, 6)] == [tokyo_noon] * 3
    assert read[1] == aware and read[2] == naive.replace(tzinfo=TOKYO)
    assert found == [1, 4, 5]


def test_time_with_time_zone_keeps_its_offset(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("at_time", Time(timezone=True)),
    )
    metadata.create_all(bind=engine)
    india = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    values = [
        datetime.time(12, 0, tzinfo=datetime.timezone.utc),
        datetime.time(12, 0, tzinfo=india),
        datetime.time(1, 2, 3, 456789, tzinfo=datetime.timezone(datetime.timedelta(hours=-8))),
        None,
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"id": i, "at_time": v} for i, v in enumerate(values)])
        got = [row.at_time for row in conn.execute(select(table).order_by(table.c.id))]
        stored = (
            conn.execute(text(f"SELECT CAST(at_time AS VARCHAR) FROM {name} ORDER BY id"))
            .scalars()
            .all()
        )
    assert got == values
    assert [value.utcoffset() for value in got[:3]] == [value.utcoffset() for value in values[:3]]
    assert stored == ["12:00:00+00", "12:00:00+05:30", "01:02:03.456789-08", None]
    column = inspect(engine).get_columns(table_name=name)[1]
    assert column["type"].timezone is True


def test_booleans_and_integers_mix_in_one_batch(engine, metadata, name):
    table = Table(name, metadata, Column("n", Integer))
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"n": True}, {"n": 2}, {"n": None}, {"n": False}])
        assert conn.execute(select(table.c.n)).scalars().all() == [1, 2, None, 0]


# -- wrong Python types ------------------------------------------------------------------------


def test_integer_aggregates_are_python_ints(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("n", Integer),
        Column("f", Float),
        Column("d", Numeric(10, 4)),
        Column("day", Date),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {
                    "n": 2,
                    "f": 1.2345,
                    "d": decimal.Decimal("1.2345"),
                    "day": datetime.date(2024, 1, 1),
                },
                {"n": 3, "f": 2.0, "d": 2, "day": None},
            ],
        )
        total = conn.execute(select(func.sum(table.c.n))).scalar()
        assert total == 5 and type(total) is int
        assert type(conn.execute(select(func.count()).select_from(table)).scalar()) is int
        # integer parameters reach functions that take an INTEGER
        row = conn.execute(
            select(
                func.round(table.c.f, 2),
                func.round(table.c.d, 1),
                table.c.day + 5,
                func.chr(65),
                func.lpad("x", 3, "-"),
            ).where(table.c.n == 2)
        ).one()
    assert tuple(row) == (1.23, decimal.Decimal("1.2"), datetime.date(2024, 1, 6), "A", "--x")


def test_128_bit_integers_cover_their_whole_range(engine, metadata, name):
    table = Table(name, metadata, Column("h", HUGEINT), Column("u", UHUGEINT))
    metadata.create_all(bind=engine)
    extremes = [
        {"h": 2**127 - 1, "u": 2**128 - 1},
        {"h": -(2**127), "u": 0},
        {"h": None, "u": None},
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), extremes)
        got = [dict(row._mapping) for row in conn.execute(select(table))]
        found = conn.execute(select(table.c.u).where(table.c.h == -(2**127))).scalar()
    assert got == extremes
    assert found == 0


def test_struct_types_work_inside_expressions(engine):
    """(Their statement cache key used to be unhashable.)"""
    struct = Struct(fields={"a": Integer, "b": String})
    with engine.connect() as conn:
        for _ in range(2):  # the second run comes from the statement cache
            assert conn.execute(select(literal_column("{'a': 1, 'b': 'x'}", struct))).scalar() == {
                "a": 1,
                "b": "x",
            }
            assert conn.execute(
                select(cast(literal_column("{'a': 2, 'b': 'y'}"), struct))
            ).scalar() == {"a": 2, "b": "y"}


# -- JSON keys ---------------------------------------------------------------------------------


def test_json_keys_that_look_like_paths_or_pointers_are_keys(engine, metadata, name):
    table = Table(name, metadata, Column("doc", JSON))
    metadata.create_all(bind=engine)
    doc = {
        "a": 1,
        "$a": "dollar",
        "/p": "slash",
        "$.a": "dollarpath",
        "a.b": "dot",
        'q"q': "quote",
        "\\": "backslash",
        "l": [10, 20, 30],
    }
    with engine.begin() as conn:
        conn.execute(table.insert().values(doc=doc))
        for key, expected in doc.items():
            assert conn.execute(select(table.c.doc[key])).scalar() == expected, key
            assert conn.execute(select(table.c.doc[(key,)])).scalar() == expected, key
        assert conn.execute(select(table.c.doc[("l", 1)].as_integer())).scalar() == 20
        assert conn.execute(select(table.c.doc[("l", -1)].as_integer())).scalar() == 30
        assert conn.execute(select(table.c.doc["missing"])).scalar() is None


# -- reflection --------------------------------------------------------------------------------


def test_a_temporary_table_hides_the_permanent_one_of_its_name(engine, name):
    with engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE TABLE {name} "
                "(id INTEGER PRIMARY KEY, total DECIMAL(10,2), code VARCHAR UNIQUE)"
            )
        )
        conn.execute(text(f"CREATE TEMPORARY TABLE {name} (tmp_a VARCHAR, tmp_b VARCHAR)"))
        inspector = inspect(conn)
        # ... as it does in a query on the same connection
        assert list(conn.execute(text(f"SELECT * FROM {name}")).keys()) == ["tmp_a", "tmp_b"]
        assert [c["name"] for c in inspector.get_columns(table_name=name)] == ["tmp_a", "tmp_b"]
        assert inspector.get_pk_constraint(table_name=name)["constrained_columns"] == []
        assert inspector.get_unique_constraints(table_name=name) == []
        conn.execute(text(f"DROP TABLE temp.main.{name}"))
        inspector = inspect(conn)
        assert [c["name"] for c in inspector.get_columns(table_name=name)] == [
            "id",
            "total",
            "code",
        ]
        conn.rollback()


def test_reflection_finds_the_current_schema_in_whatever_case_the_url_gave_it(engine, server, name):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"ATTACH ':memory:' AS {name}_lake"))
        conn.execute(text(f"CREATE SCHEMA {name}_lake.Sales"))
        conn.execute(text(f"CREATE TABLE {name}_lake.Sales.orders (id INTEGER PRIMARY KEY)"))
    try:
        for catalog, schema in [
            (f"{name}_lake", "Sales"),
            (f"{name}_LAKE", "SALES"),
            (f"{name}_Lake", "sales"),
        ]:
            other = create_engine(
                url=server_url(server=server, schema=schema).set(database=catalog)
            )
            try:
                inspector = inspect(other)
                assert inspector.get_table_names() == ["orders"], (catalog, schema)
                assert inspector.has_table(table_name="orders")
                metadata = MetaData()
                Table("orders", metadata, Column("id", Integer, primary_key=True))
                metadata.create_all(bind=other)  # sees the table: creates nothing, raises nothing
            finally:
                other.dispose()
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DETACH {name}_lake"))


def test_reflected_index_columns_with_quotes_and_non_integer_sequence_defaults(engine, name):
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SEQUENCE {name}_seq"))
        conn.execute(
            text(
                f'CREATE TABLE {name} ("q""uote" INTEGER, "we,ird" INTEGER, '
                f"s VARCHAR DEFAULT nextval('{name}_seq'))"
            )
        )
        conn.execute(text(f'CREATE INDEX {name}_ix ON {name} ("we,ird", "q""uote")'))
    try:
        inspector = inspect(engine)
        (index,) = inspector.get_indexes(table_name=name)
        assert index["column_names"] == ["we,ird", 'q"uote'] and "expressions" not in index
        columns = {c["name"]: c for c in inspector.get_columns(table_name=name)}
        assert columns["s"]["autoincrement"] is False  # a text column isn't auto-incrementing
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {name}"))
            conn.execute(text(f"DROP SEQUENCE {name}_seq"))


# -- identity ----------------------------------------------------------------------------------


def test_identity_on_a_column_that_is_not_the_key(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("code", String, primary_key=True),
        Column("number", Integer, Identity(start=500, increment=10)),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"code": "a"}, {"code": "b"}])
        assert conn.execute(select(table.c.number).order_by(table.c.code)).scalars().all() == [
            500,
            510,
        ]
    assert f"{name}_number_seq" in inspect(engine).get_sequence_names()
    metadata.drop_all(bind=engine)
    assert f"{name}_number_seq" not in inspect(engine).get_sequence_names()


def test_composite_key_with_one_auto_incrementing_part(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("tenant", Integer, primary_key=True, autoincrement=False),
        Column("seq", Integer, primary_key=True, autoincrement=True),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        first = conn.execute(table.insert().values(tenant=7))
        second = conn.execute(table.insert().values(tenant=7))
    assert (first.inserted_primary_key, second.inserted_primary_key) == ((7, 1), (7, 2))


# -- transactions ------------------------------------------------------------------------------


def test_a_raw_begin_on_an_autocommit_connection_does_not_leak_into_the_pool(url, name):
    pooled = create_engine(url=url, pool_size=1, max_overflow=0, isolation_level="AUTOCOMMIT")
    other = create_engine(url=url)
    try:
        with pooled.connect() as conn:
            conn.execute(text(f"CREATE TABLE {name} (a INTEGER)"))
            conn.execute(text("BEGIN"))
            conn.execute(text(f"INSERT INTO {name} VALUES (1)"))
            # returned to the pool with the raw transaction still open: it is rolled back
        with pooled.connect() as conn:  # the same pooled connection, for its next user
            conn.execute(text(f"INSERT INTO {name} VALUES (2)"))
        with other.connect() as conn:
            assert conn.execute(text(f"SELECT a FROM {name}")).scalars().all() == [2]
        with pooled.connect() as conn:  # BEGIN ... COMMIT sent as text commits
            conn.execute(text("BEGIN"))
            conn.execute(text(f"INSERT INTO {name} VALUES (3)"))
            conn.execute(text("COMMIT"))
        with other.connect() as conn:
            assert conn.execute(text(f"SELECT a FROM {name} ORDER BY a")).scalars().all() == [2, 3]
    finally:
        with other.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
        pooled.dispose()
        other.dispose()


def test_work_after_a_raw_commit_is_still_in_a_transaction(engine, metadata, name):
    table = Table(name, metadata, Column("a", Integer))
    metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(table.insert().values(a=1))
        conn.execute(text("COMMIT"))
        conn.execute(table.insert().values(a=2))
        conn.rollback()  # must undo the second insert
        assert conn.execute(select(table.c.a)).scalars().all() == [1]
        conn.execute(table.insert().values(a=3))
        conn.execute(text("ROLLBACK"))
        conn.execute(table.insert().values(a=4))
        conn.commit()  # must not fail with "no transaction is active"
    with engine.connect() as conn:
        assert conn.execute(select(table.c.a).order_by(table.c.a)).scalars().all() == [1, 4]


def test_an_error_that_quotes_data_is_not_a_disconnect(engine, metadata, name):
    table = Table(name, metadata, Column("n", Integer))
    metadata.create_all(bind=engine)
    with engine.connect() as conn:
        with pytest.raises(
            exc.DBAPIError, match="Could not convert string 'connection refused'"
        ) as caught:
            conn.execute(
                text(f"INSERT INTO {name} VALUES (CAST(:v AS INTEGER))"),
                {"v": "connection refused"},
            )
        assert caught.value.connection_invalidated is False
        conn.rollback()
        assert (
            conn.execute(select(func.count()).select_from(table)).scalar() == 0
        )  # same connection, alive


def test_a_transaction_writes_to_one_catalog(engine, name):
    """A DuckDB limit the README states."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"ATTACH ':memory:' AS {name}_other"))
        conn.execute(text(f"CREATE TABLE {name} (a INTEGER)"))
        conn.execute(text(f"CREATE TABLE {name}_other.main.t (a INTEGER)"))
    try:
        with engine.connect() as conn:
            conn.execute(text(f"INSERT INTO {name} VALUES (1)"))
            with pytest.raises(
                exc.DBAPIError,
                match="a single transaction can only write to a single attached database",
            ):
                conn.execute(text(f"INSERT INTO {name}_other.main.t VALUES (1)"))
            conn.rollback()
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DROP TABLE {name}"))
            conn.execute(text(f"DETACH {name}_other"))


# -- schema_translate_map ----------------------------------------------------------------------


def test_schema_translate_map_with_names_that_need_quoting(engine, name):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"ATTACH ':memory:' AS {name}_lake"))
        for schema in (f'"{name}_Tenant-A"', f"{name}_lake.sales"):
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    metadata = MetaData()
    table = Table(
        "things",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("v", String),
        schema="per_tenant",
    )
    try:
        for target in (f"{name}_Tenant-A", f"{name}_lake.sales"):
            with engine.connect().execution_options(
                schema_translate_map={"per_tenant": target}
            ) as conn:
                metadata.create_all(bind=conn)
                inserted = conn.execute(table.insert().values(v=target))
                assert inserted.inserted_primary_key == (1,)
                assert conn.execute(select(table.c.v)).scalar() == target
                conn.commit()
            assert inspect(engine).has_table(
                table_name="things", schema=target if "." in target else f'"{target}"'
            )
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP SCHEMA "{name}_Tenant-A" CASCADE'))
            conn.execute(text(f"DETACH {name}_lake"))


# -- an idle session the server replaced --------------------------------------------------------


def test_catalog_and_schema_from_the_url_survive_an_idle_session_being_replaced():
    """The server evicts idle sessions (after 15 minutes by default; 2 seconds here) and the
    driver opens a new one for the next statement. The URL's catalog and schema must still be
    current then, with no error to the application."""
    import time

    with hardcharger.Server(
        username=USERNAME, password=PASSWORD, env={"HARDCHARGER_SESSION_IDLE_TIMEOUT": "2"}
    ) as server:
        admin = create_engine(url=server_url(server=server))
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("ATTACH ':memory:' AS lake"))
            conn.execute(text("CREATE SCHEMA lake.sales"))
        admin.dispose()
        engine = create_engine(
            url=server_url(server=server, schema="sales").set(database="lake"),
            pool_size=1,
            max_overflow=0,
        )
        try:
            with engine.connect() as conn:
                session = conn.connection.driver_connection.session_id
                assert (
                    conn.execute(
                        text("SELECT current_catalog() || '.' || current_schema()")
                    ).scalar()
                    == "lake.sales"
                )
                conn.commit()
            time.sleep(7)
            with engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT current_catalog() || '.' || current_schema()")
                    ).scalar()
                    == "lake.sales"
                )
                assert conn.connection.driver_connection.session_id != session  # it was replaced
        finally:
            engine.dispose()
