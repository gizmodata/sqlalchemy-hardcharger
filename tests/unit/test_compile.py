"""SQL and DDL the dialect generates. No server needed (tests/integration runs it for real)."""

import pytest
from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    Identity,
    Index,
    Integer,
    MetaData,
    Sequence,
    String,
    Table,
    exc,
    func,
    select,
    try_cast,
)
from sqlalchemy import types as sqltypes
from sqlalchemy.schema import CreateIndex, CreateSequence, CreateTable, DropTable

from sqlalchemy_hardcharger import HardchargerDialect, insert

DIALECT = HardchargerDialect()


def sql(statement, **kw) -> str:
    return str(statement.compile(dialect=DIALECT, **kw))


def literal(statement) -> str:
    return sql(statement, compile_kwargs={"literal_binds": True})


@pytest.fixture
def metadata():
    return MetaData()


# -- auto-incrementing keys -------------------------------------------------------------------


def test_integer_primary_key_gets_a_sequence(metadata):
    table = Table(
        "users", metadata, Column("id", Integer, primary_key=True), Column("name", String)
    )
    assert sql(CreateTable(table)) == (
        "CREATE SEQUENCE IF NOT EXISTS users_id_seq;\n"
        "COMMENT ON SEQUENCE users_id_seq IS 'sqlalchemy-hardcharger: numbers users.id';\n"
        "\nCREATE TABLE users (\n"
        "\tid INTEGER DEFAULT nextval('users_id_seq') NOT NULL, \n"
        "\tname VARCHAR, \n"
        "\tPRIMARY KEY (id)\n)\n\n"
    )
    assert sql(DropTable(table)) == "\nDROP TABLE users;\nDROP SEQUENCE IF EXISTS users_id_seq"


def test_an_identity_column_that_is_not_the_key_gets_a_sequence_too(metadata):
    table = Table(
        "orders",
        metadata,
        Column("code", String, primary_key=True),
        Column("number", Integer, Identity(start=500)),
    )
    ddl = sql(CreateTable(table))
    assert "CREATE SEQUENCE IF NOT EXISTS orders_number_seq START WITH 500;\n" in ddl
    assert "number INTEGER DEFAULT nextval('orders_number_seq')" in ddl
    assert sql(DropTable(table)).endswith("DROP SEQUENCE IF EXISTS orders_number_seq")


def test_a_quote_in_a_table_name_is_escaped_in_the_sequence_comment(metadata):
    table = Table("it's", metadata, Column("id", Integer, primary_key=True))
    assert """IS 'sqlalchemy-hardcharger: numbers it''s.id';""" in sql(CreateTable(table))


def test_the_sequence_lives_in_the_tables_schema_and_is_quoted_like_it(metadata):
    table = Table(
        "Order Lines", metadata, Column("id", BigInteger, primary_key=True), schema="lake.sales"
    )
    ddl = sql(CreateTable(table))
    assert ddl.startswith('CREATE SEQUENCE IF NOT EXISTS lake.sales."Order Lines_id_seq";\n')
    assert """id BIGINT DEFAULT nextval('lake.sales."Order Lines_id_seq"') NOT NULL""" in ddl
    assert sql(DropTable(table)).endswith('DROP SEQUENCE IF EXISTS lake.sales."Order Lines_id_seq"')


def test_a_temporary_table_gets_a_temporary_sequence(metadata):
    table = Table(
        "scratch", metadata, Column("id", Integer, primary_key=True), prefixes=["TEMPORARY"]
    )
    assert sql(CreateTable(table)).startswith(
        "CREATE TEMPORARY SEQUENCE IF NOT EXISTS scratch_id_seq;\n"
    )


def test_identity_options_go_to_the_sequence(metadata):
    table = Table(
        "invoices",
        metadata,
        Column("id", Integer, Identity(start=1000, increment=5), primary_key=True),
    )
    ddl = sql(CreateTable(table))
    assert ddl.startswith(
        "CREATE SEQUENCE IF NOT EXISTS invoices_id_seq INCREMENT BY 5 START WITH 1000;\n"
    )
    assert "GENERATED" not in ddl and "IDENTITY" not in ddl


@pytest.mark.parametrize(
    "columns",
    [
        # not auto-incrementing: no sequence is made for any of these
        [Column("id", Integer, primary_key=True, autoincrement=False)],
        [Column("id", String, primary_key=True)],
        [Column("id", Integer, Sequence("my_seq"), primary_key=True)],
        [Column("id", Integer, primary_key=True, server_default="7")],
        [Column("a", Integer, primary_key=True), Column("b", Integer, primary_key=True)],
        [Column("id", Integer)],
    ],
)
def test_no_sequence_unless_the_column_auto_increments(metadata, columns):
    table = Table("t", metadata, *columns)
    assert "CREATE SEQUENCE" not in sql(CreateTable(table))
    assert "DROP SEQUENCE" not in sql(DropTable(table))
    assert "SERIAL" not in sql(CreateTable(table))


def test_create_sequence_has_no_type_clause():
    assert sql(CreateSequence(Sequence("my_seq", start=5, increment=2))) == (
        "CREATE SEQUENCE my_seq INCREMENT BY 2 START WITH 5"
    )
    assert (
        sql(CreateSequence(Sequence("big_seq", data_type=BigInteger))) == "CREATE SEQUENCE big_seq"
    )
    assert sql(select(Sequence("my_seq", schema="lake.sales").next_value())) == (
        "SELECT nextval('lake.sales.my_seq') AS next_value_1"
    )


# -- DDL --------------------------------------------------------------------------------------


def test_generated_columns_are_virtual(metadata):
    table = Table("t", metadata, Column("a", Integer), Column("b", Integer, Computed("a * 2")))
    assert "b INTEGER GENERATED ALWAYS AS (a * 2) VIRTUAL" in sql(CreateTable(table))
    stored = Table(
        "s", metadata, Column("a", Integer), Column("b", Integer, Computed("a", persisted=True))
    )
    with pytest.raises(exc.CompileError, match="generated columns are virtual"):
        sql(CreateTable(stored))


def test_create_index_is_plain(metadata):
    table = Table("t", metadata, Column("a", Integer), Column("b", String))
    assert sql(CreateIndex(Index("ix_t_a", table.c.a, unique=True))) == (
        "CREATE UNIQUE INDEX ix_t_a ON t (a)"
    )
    assert sql(CreateIndex(Index("ix_t_b", func.lower(table.c.b)), if_not_exists=True)) == (
        "CREATE INDEX IF NOT EXISTS ix_t_b ON t (lower(b))"
    )


def test_reserved_words_are_quoted(metadata):
    table = Table(
        "select",
        metadata,
        Column("pivot", Integer),
        Column("qualify", Integer),
        Column("plain", Integer),
    )
    assert sql(select(table)) == (
        'SELECT "select"."pivot", "select"."qualify", "select".plain \nFROM "select"'
    )


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ("sales", "sales.t"),
        ("lake.sales", "lake.sales.t"),
        ('"my.catalog".sales', '"my.catalog".sales.t'),
        ("Lake.My Schema", '"Lake"."My Schema".t'),
    ],
)
def test_schema_may_name_a_catalog(metadata, schema, expected):
    table = Table("t", metadata, Column("a", Integer), schema=schema)
    assert sql(select(table.c.a)) == f"SELECT {expected}.a \nFROM {expected}"


def test_a_schema_with_too_many_parts_is_refused(metadata):
    table = Table("t", metadata, Column("a", Integer), schema="a.b.c")
    with pytest.raises(exc.ArgumentError, match="must be 'schema' or 'catalog.schema'"):
        sql(select(table))


# -- statements -------------------------------------------------------------------------------


@pytest.fixture
def t(metadata):
    return Table(
        "t",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("n", Integer),
        Column("s", String),
        Column("doc", sqltypes.JSON),
    )


def test_parameters_are_question_marks(t):
    assert sql(select(t.c.id).where(t.c.s == "x")) == "SELECT t.id \nFROM t \nWHERE t.s = ?"


def test_integer_parameters_are_cast_so_functions_accept_them(t):
    # The driver binds a Python int as BIGINT, which DuckDB won't narrow for round(x, ?).
    assert sql(select(t.c.id).where(t.c.n == 5)) == (
        "SELECT t.id \nFROM t \nWHERE t.n = ?::INTEGER"
    )
    assert sql(select(func.round(t.c.n, 2))) == "SELECT round(t.n, ?::INTEGER) AS round_1 \nFROM t"
    big = select(t.c.id).where(t.c.n == 2**40)  # past 32 bits: SQLAlchemy types it BIGINT
    assert sql(big) == "SELECT t.id \nFROM t \nWHERE t.n = ?::INTEGER"
    from sqlalchemy import literal

    assert sql(select(literal(2**40))) == "SELECT ? AS anon_1"
    assert sql(select(literal(7))) == "SELECT ?::INTEGER AS anon_1"


def test_for_update_is_dropped(t):
    assert sql(select(t.c.id).with_for_update()) == "SELECT t.id \nFROM t"


def test_division(t):
    assert literal(select(t.c.n / 2)) == "SELECT t.n / 2 AS anon_1 \nFROM t"
    assert literal(select(t.c.n // 2)) == "SELECT t.n // 2 AS anon_1 \nFROM t"


def test_regular_expressions(t):
    assert literal(select(t.c.s.regexp_match("^a"))) == (
        "SELECT regexp_matches(t.s, '^a') AS anon_1 \nFROM t"
    )
    assert literal(select(t.c.s.regexp_match("^a", flags="i"))) == (
        "SELECT regexp_matches(t.s, '^a', 'i') AS anon_1 \nFROM t"
    )
    assert literal(select(~t.c.s.regexp_match("^a"))) == (
        "SELECT NOT regexp_matches(t.s, '^a') AS anon_1 \nFROM t"
    )
    assert literal(select(t.c.s.regexp_replace("a+", "b"))) == (
        "SELECT regexp_replace(t.s, 'a+', 'b') AS anon_1 \nFROM t"
    )


def test_try_cast_and_xor(t):
    assert sql(select(try_cast(t.c.s, Integer))) == "SELECT TRY_CAST(t.s AS INTEGER) AS s \nFROM t"
    assert literal(select(t.c.n.bitwise_xor(3))) == "SELECT xor(t.n, 3) AS anon_1 \nFROM t"


def test_ilike_and_limit_offset(t):
    assert literal(select(t.c.id).where(t.c.s.ilike("a%")).limit(5).offset(10)) == (
        "SELECT t.id \nFROM t \nWHERE t.s ILIKE 'a%' \n LIMIT 5 OFFSET 10"
    )


def test_json_access_uses_arrows_and_jsonpath(t):
    # A key is always a JSONPath: sent bare, DuckDB would read "$a" as a path and "/p" as a
    # JSON Pointer and address something else.
    assert literal(select(t.c.doc["a"])) == """SELECT t.doc -> '$."a"' AS anon_1 \nFROM t"""
    assert literal(select(t.c.doc["$a"])) == """SELECT t.doc -> '$."$a"' AS anon_1 \nFROM t"""
    assert literal(select(t.c.doc["/p"])) == """SELECT t.doc -> '$."/p"' AS anon_1 \nFROM t"""
    assert literal(select(t.c.doc[3])) == "SELECT t.doc -> '$[3]' AS anon_1 \nFROM t"
    assert literal(select(t.c.doc[("a", 0, "b")])) == (
        """SELECT t.doc -> '$."a"[0]."b"' AS anon_1 \nFROM t"""
    )
    assert literal(select(t.c.doc[("a", "b")].as_integer())) == (
        """SELECT CAST(t.doc ->> '$."a"."b"' AS INTEGER) AS anon_1 \nFROM t"""
    )
    assert literal(select(t.c.doc['q"\\'])) == (
        """SELECT t.doc -> '$."q\\"\\\\"' AS anon_1 \nFROM t"""
    )


def test_match_is_refused_with_a_pointer_to_full_text_search(t):
    with pytest.raises(exc.CompileError, match="no MATCH operator"):
        sql(select(t.c.s.match("x")))


def test_returning_and_upsert(t):
    assert sql(t.insert().values(n=1).returning(t.c.id)) == (
        "INSERT INTO t (n) VALUES (?::INTEGER) RETURNING t.id"
    )
    upsert = insert(t).values(id=1, n=2)
    assert sql(
        upsert.on_conflict_do_update(index_elements=[t.c.id], set_={"n": upsert.excluded.n})
    ) == (
        "INSERT INTO t (id, n) VALUES (?::INTEGER, ?::INTEGER) "
        "ON CONFLICT (id) DO UPDATE SET n = excluded.n"
    )
    assert sql(upsert.on_conflict_do_nothing()) == (
        "INSERT INTO t (id, n) VALUES (?::INTEGER, ?::INTEGER) ON CONFLICT DO NOTHING"
    )


def test_typed_parameters_are_cast(metadata):
    import datetime
    import uuid

    table = Table(
        "typed",
        metadata,
        Column("u", sqltypes.Uuid),
        Column("i", sqltypes.Interval),
        Column("j", sqltypes.JSON),
    )
    statement = table.insert().values(u=uuid.uuid4(), i=datetime.timedelta(days=1), j={"a": 1})
    assert sql(statement) == "INSERT INTO typed (u, i, j) VALUES (?::UUID, ?::INTERVAL, ?::JSON)"


def test_variant_is_written_and_read_through_json(metadata):
    from sqlalchemy_hardcharger import VARIANT

    table = Table("v", metadata, Column("id", Integer), Column("value", VARIANT))
    assert sql(table.insert().values(id=1, value={"a": 1})) == (
        "INSERT INTO v (id, value) VALUES (?::INTEGER, CAST(CAST(? AS JSON) AS VARIANT))"
    )
    assert sql(select(table)) == "SELECT v.id, CAST(v.value AS JSON) AS value \nFROM v"


def test_values_written_as_literals_are_quoted(metadata):
    """literal_binds / literal_execute / Alembic's offline SQL write values into the statement."""
    import datetime

    hostile = "00000000-0000-0000-0000-000000000000' OR 1=1 --"
    table = Table(
        "lit",
        metadata,
        Column("u", sqltypes.Uuid(as_uuid=False)),
        Column("i", sqltypes.Interval),
    )
    assert literal(select(table.c.u).where(table.c.u == hostile)) == (
        "SELECT lit.u \nFROM lit \nWHERE lit.u = '00000000-0000-0000-0000-000000000000'' OR 1=1 --'"
    )
    assert literal(select(table.c.i).where(table.c.i == "1 day' + INTERVAL '100 years")) == (
        "SELECT lit.i \nFROM lit \nWHERE lit.i = '1 day'' + INTERVAL ''100 years'"
    )
    assert literal(select(table.c.i).where(table.c.i == datetime.timedelta(days=2))) == (
        "SELECT lit.i \nFROM lit \nWHERE lit.i = INTERVAL '2 days 0 seconds 0 microseconds'"
    )


def test_datetime_and_time_with_time_zone_are_sent_as_text_and_cast(metadata):
    table = Table(
        "zoned",
        metadata,
        Column("naive", sqltypes.DateTime),
        Column("aware", sqltypes.DateTime(timezone=True)),
        Column("t", sqltypes.Time),
        Column("tz", sqltypes.Time(timezone=True)),
    )
    assert sql(table.insert()) == (
        "INSERT INTO zoned (naive, aware, t, tz) "
        "VALUES (?, ?::TIMESTAMP WITH TIME ZONE, ?, ?::TIME WITH TIME ZONE)"
    )
    assert sql(select(table)) == (
        "SELECT zoned.naive, zoned.aware, zoned.t, CAST(zoned.tz AS VARCHAR) AS tz \nFROM zoned"
    )


def test_schema_translate_map_and_quoted_names(metadata):
    from sqlalchemy.sql.elements import quoted_name

    table = Table("t", metadata, Column("a", Integer), schema="per_tenant")
    for target, expected in [
        ("plain", "plain.t"),
        ("Tenant_A", '"Tenant_A".t'),
        ("tenant-1", '"tenant-1".t'),
        ("lake.sales", "lake.sales.t"),
    ]:
        compiled = select(table.c.a).compile(
            dialect=DIALECT,
            schema_translate_map={"per_tenant": target},
            render_schema_translate=True,
        )
        assert str(compiled) == f"SELECT {expected}.a \nFROM {expected}"
    # a quoted_name is one schema name, dots and all
    dotted = Table("t", MetaData(), Column("a", Integer), schema=quoted_name("my.schema", True))
    assert sql(select(dotted.c.a)) == 'SELECT "my.schema".t.a \nFROM "my.schema".t'


def test_savepoints_are_refused_with_the_reason():
    with pytest.raises(exc.InvalidRequestError, match="has no savepoints"):
        DIALECT.do_savepoint(connection=None, name="sp1")
