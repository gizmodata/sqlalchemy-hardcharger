"""Every supported type: nullable, NOT NULL and empty, written and read through a real server,
and reflected back."""

import datetime
import decimal
import uuid

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, exc, inspect, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql

from sqlalchemy_hardcharger import (
    BIGNUM,
    BIT,
    HUGEINT,
    TIME_NS,
    TIMESTAMP_MS,
    TIMESTAMP_S,
    TINYINT,
    UBIGINT,
    UHUGEINT,
    UINTEGER,
    USMALLINT,
    UTINYINT,
    VARIANT,
    Map,
    Struct,
    Union,
)

UTC = datetime.timezone.utc

# (id, column type, a value, the class the column reflects as)
CASES = [
    ("integer", sqltypes.Integer(), 42, sqltypes.INTEGER),
    ("integer_negative", sqltypes.Integer(), -(2**31), sqltypes.INTEGER),
    ("biginteger", sqltypes.BigInteger(), 2**62, sqltypes.BIGINT),
    ("smallinteger", sqltypes.SmallInteger(), -7, sqltypes.SMALLINT),
    ("tinyint", TINYINT(), -128, TINYINT),
    ("utinyint", UTINYINT(), 255, UTINYINT),
    ("usmallint", USMALLINT(), 65535, USMALLINT),
    ("uinteger", UINTEGER(), 4_294_967_295, UINTEGER),
    ("ubigint", UBIGINT(), 2**64 - 1, UBIGINT),
    ("hugeint", HUGEINT(), -(2**100), HUGEINT),
    ("uhugeint", UHUGEINT(), 2**120, UHUGEINT),
    ("bignum", BIGNUM(), 10**50 + 7, BIGNUM),
    ("float", sqltypes.Float(), 15.7563827, sqltypes.DOUBLE),
    ("float24", sqltypes.Float(precision=24), 1.5, sqltypes.REAL),
    ("double", sqltypes.Double(), 1e300, sqltypes.DOUBLE),
    ("real", sqltypes.REAL(), -0.25, sqltypes.REAL),
    (
        "numeric",
        sqltypes.Numeric(precision=12, scale=3),
        decimal.Decimal("123456789.125"),
        sqltypes.DECIMAL,
    ),
    (
        "numeric_as_float",
        sqltypes.Numeric(precision=6, scale=2, asdecimal=False),
        12.25,
        sqltypes.DECIMAL,
    ),
    ("string", sqltypes.String(), "héllo ✓ 'quoted' \"double\"", sqltypes.VARCHAR),
    ("string_empty", sqltypes.String(), "", sqltypes.VARCHAR),
    ("string_length", sqltypes.String(length=20), "twenty chars or less", sqltypes.VARCHAR),
    ("text", sqltypes.Text(), "line one\nline two", sqltypes.VARCHAR),
    ("boolean", sqltypes.Boolean(), False, sqltypes.BOOLEAN),
    ("date", sqltypes.Date(), datetime.date(1969, 7, 20), sqltypes.DATE),
    ("time", sqltypes.Time(), datetime.time(23, 59, 59, 999999), sqltypes.TIME),
    ("time_ns", TIME_NS(), datetime.time(1, 2, 3, 456), TIME_NS),
    (
        "datetime",
        sqltypes.DateTime(),
        datetime.datetime(2026, 11, 13, 12, 34, 56, 789012),
        sqltypes.TIMESTAMP,
    ),
    ("timestamp_s", TIMESTAMP_S(), datetime.datetime(2026, 11, 13, 12, 34, 56), TIMESTAMP_S),
    (
        "timestamp_ms",
        TIMESTAMP_MS(),
        datetime.datetime(2026, 11, 13, 12, 34, 56, 789000),
        TIMESTAMP_MS,
    ),
    (
        "datetime_tz",
        sqltypes.DateTime(timezone=True),
        datetime.datetime(2026, 11, 13, 12, 34, 56, 789012, tzinfo=UTC),
        sqltypes.TIMESTAMP,
    ),
    (
        "interval",
        sqltypes.Interval(),
        datetime.timedelta(days=3, seconds=4, microseconds=5),
        postgresql.INTERVAL,
    ),
    (
        "interval_negative",
        sqltypes.Interval(),
        datetime.timedelta(seconds=-90),
        postgresql.INTERVAL,
    ),
    ("uuid", sqltypes.Uuid(), uuid.UUID("12345678-1234-5678-1234-567812345678"), sqltypes.UUID),
    (
        "uuid_text",
        sqltypes.Uuid(as_uuid=False),
        "12345678-1234-5678-1234-567812345678",
        sqltypes.UUID,
    ),
    ("binary", sqltypes.LargeBinary(), b"\x00\x01\xfe\xff binary", sqltypes.BLOB),
    ("binary_empty", sqltypes.LargeBinary(), b"", sqltypes.BLOB),
    ("json", sqltypes.JSON(), {"a": [1, 2.5, {"b": None}], "c": "d"}, sqltypes.JSON),
    ("json_list", sqltypes.JSON(), [1, "two", False], sqltypes.JSON),
    ("variant", VARIANT(), {"k": [1, "x", {"deep": True}]}, VARIANT),
    ("variant_number", VARIANT(), 42, VARIANT),
    ("enum", sqltypes.Enum("sad", "ok", "it's fine"), "it's fine", sqltypes.Enum),
    ("array_integer", sqltypes.ARRAY(sqltypes.Integer), [1, 2, 3], postgresql.ARRAY),
    ("array_string", sqltypes.ARRAY(sqltypes.String), ["a", "b c"], postgresql.ARRAY),
    ("bit", BIT(), "10110", BIT),
]


@pytest.mark.parametrize(("case", "type_", "value", "reflected"), CASES, ids=[c[0] for c in CASES])
def test_round_trip_nullable_not_null_and_empty(
    engine, metadata, name, case, type_, value, reflected
):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("maybe", type_, nullable=True),
        Column("always", type_, nullable=False),
    )
    metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # empty: no rows, and the columns are still described
        result = conn.execute(select(table))
        assert list(result.keys()) == ["id", "maybe", "always"]
        assert result.all() == []

        conn.execute(table.insert(), [{"id": 1, "maybe": value, "always": value}])
        conn.execute(table.insert(), [{"id": 2, "maybe": None, "always": value}])
        rows = conn.execute(select(table).order_by(table.c.id)).all()

    assert [row.id for row in rows] == [1, 2]
    assert rows[0].maybe == value and type(rows[0].maybe) is type(value)
    assert rows[1].maybe is None
    for row in rows:
        assert row.always == value and type(row.always) is type(value)

    # a filter on the column finds the row (the parameter is bound with the column's type)
    if case not in (
        "json",
        "json_list",
        "variant",
        "variant_number",
        "array_integer",
        "array_string",
    ):
        with engine.connect() as conn:
            found = conn.execute(select(table.c.id).where(table.c.maybe == value)).scalars().all()
        assert found == [1]

    # NOT NULL is enforced by the server. (SQLAlchemy's JSON type stores Python None as the
    # JSON value null, not SQL NULL, unless created with none_as_null=True.)
    if case not in ("json", "json_list"):
        with engine.connect() as conn:
            with pytest.raises(exc.IntegrityError, match="NOT NULL constraint failed"):
                conn.execute(table.insert(), [{"id": 3, "maybe": value, "always": None}])

    columns = {column["name"]: column for column in inspect(engine).get_columns(table_name=name)}
    assert isinstance(columns["maybe"]["type"], reflected)
    assert isinstance(columns["always"]["type"], reflected)
    assert columns["maybe"]["nullable"] is True
    assert columns["always"]["nullable"] is False

    # the reflected table can be created again and holds the same value
    reflected_metadata = MetaData()
    copy = Table(name, reflected_metadata, autoload_with=engine)
    copy.name = f"{name}_copy"
    copy_metadata = MetaData()
    clone = copy.to_metadata(copy_metadata, name=f"{name}_copy")
    try:
        clone.create(bind=engine)
        with engine.begin() as conn:
            conn.execute(clone.insert(), [{"id": 1, "maybe": None, "always": value}])
            again = conn.execute(select(clone.c.always)).scalar()
            # (a reflected UUID column reads as uuid.UUID: reflection can't know as_uuid=False)
            assert again == value or str(again) == value
    finally:
        clone.drop(bind=engine, checkfirst=True)


def test_timestamp_with_time_zone_is_an_instant(engine, metadata, name):
    table = Table(name, metadata, Column("at", sqltypes.DateTime(timezone=True)))
    metadata.create_all(bind=engine)
    tokyo = datetime.timezone(datetime.timedelta(hours=9))
    sent = datetime.datetime(2026, 1, 1, 9, 0, 0, tzinfo=tokyo)
    with engine.begin() as conn:
        conn.execute(table.insert().values(at=sent))
        got = conn.execute(select(table.c.at)).scalar()
        as_text = conn.execute(text(f'SELECT epoch_ms("at") FROM {name}')).scalar()
    assert got.tzinfo is not None
    assert got == sent  # the same instant, in the session's time zone
    assert as_text == int(sent.timestamp() * 1000)


def test_decimal_division_comes_back_as_the_type_asked_for(engine):
    # DuckDB answers DECIMAL / DECIMAL in DOUBLE; a Numeric expression still returns Decimal.
    from sqlalchemy import cast, literal

    numeric = sqltypes.Numeric(precision=10, scale=2)
    with engine.connect() as conn:
        value = conn.execute(
            select(
                cast(literal(decimal.Decimal("5.52")), numeric)
                / cast(literal(decimal.Decimal("2.4")), numeric)
            )
        ).scalar()
    assert value == decimal.Decimal("2.3") and isinstance(value, decimal.Decimal)


def test_mixed_floats_and_decimals_in_one_executemany(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("n", sqltypes.Numeric(precision=10, scale=4)),
        Column("f", sqltypes.Float()),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {"n": 15.7563, "f": decimal.Decimal("1.5")},
                {"n": decimal.Decimal("15.7563"), "f": 2.5},
                {"n": 3, "f": 4},
                {"n": None, "f": None},
            ],
        )
        rows = conn.execute(select(table)).all()
    assert sorted((r.n, r.f) for r in rows if r.n is not None) == [
        (decimal.Decimal("3.0000"), 4.0),
        (decimal.Decimal("15.7563"), 1.5),
        (decimal.Decimal("15.7563"), 2.5),
    ]


def test_named_enum_type_is_created_and_dropped_with_its_table(engine, metadata, name):
    mood = sqltypes.Enum("sad", "ok", "happy", name=f"{name}_mood")
    table = Table(name, metadata, Column("id", Integer, primary_key=True), Column("mood", mood))
    metadata.create_all(bind=engine)
    with engine.connect() as conn:
        assert engine.dialect.has_type(conn, f"{name}_mood")
    with engine.begin() as conn:
        conn.execute(table.insert().values(mood="happy"))
        assert conn.execute(select(table.c.mood)).scalar() == "happy"
        with pytest.raises(exc.DBAPIError, match="(?i)enum|convert"):
            conn.execute(text(f"INSERT INTO {name} (id, mood) VALUES (99, 'furious')"))
    metadata.drop_all(bind=engine)
    with engine.connect() as conn:
        assert not engine.dialect.has_type(conn, f"{name}_mood")


def test_nested_types_are_read_as_python_values(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("s", Struct(fields={"street": sqltypes.String, "zip": sqltypes.Integer})),
        Column("m", Map(key_type=sqltypes.String, value_type=sqltypes.Integer)),
        Column("u", Union(members={"num": sqltypes.Integer, "str": sqltypes.String})),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {name} VALUES "
                "(1, {'street': '1 Main St', 'zip': 12345}, MAP {'a': 1, 'b': 2}, "
                "'text'::VARCHAR), "
                "(2, NULL, NULL, NULL)"
            )
        )
        rows = conn.execute(select(table).order_by(table.c.id)).all()
    assert rows[0].s == {"street": "1 Main St", "zip": 12345}
    assert rows[0].m == [("a", 1), ("b", 2)]
    assert rows[0].u == "text"
    assert (rows[1].s, rows[1].m, rows[1].u) == (None, None, None)

    columns = {c["name"]: c["type"] for c in inspect(engine).get_columns(table_name=name)}
    assert isinstance(columns["s"], Struct) and list(columns["s"].fields) == ["street", "zip"]
    assert isinstance(columns["m"], Map)
    assert isinstance(columns["u"], Union) and list(columns["u"].members) == ["num", "str"]


def test_variant_keeps_each_values_own_type(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("v", VARIANT),
    )
    metadata.create_all(bind=engine)
    values = [42, "text", {"a": 1, "b": [1, 2]}, [1, 2, 3], None, 1.5, True, "", {}, []]
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"id": i, "v": v} for i, v in enumerate(values)])
        # written by another client as a type JSON doesn't have
        conn.execute(text(f"INSERT INTO {name} VALUES (100, DATE '2020-01-02')"))
        got = [row.v for row in conn.execute(select(table).order_by(table.c.id))]
        kinds = (
            conn.execute(text(f"SELECT variant_typeof(v) FROM {name} ORDER BY id")).scalars().all()
        )
        nulls = conn.execute(text(f"SELECT count(*) FROM {name} WHERE v IS NULL")).scalar()
        typed = conn.execute(text(f"SELECT CAST(v AS DATE) FROM {name} WHERE id = 100")).scalar()
    assert got == [*values, "2020-01-02"]
    assert kinds[2].startswith("OBJECT") and kinds[3].startswith("ARRAY") and kinds[-1] == "DATE"
    assert nulls == 1  # Python None is SQL NULL
    assert typed == datetime.date(2020, 1, 2)
