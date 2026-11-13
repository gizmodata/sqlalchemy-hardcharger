"""Type parsing and value conversion. No server needed."""

import datetime
import decimal
import uuid

import pytest
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql

from sqlalchemy_hardcharger import (
    BIGNUM,
    BIT,
    GEOMETRY,
    HUGEINT,
    TIME_NS,
    TIMESTAMP_MS,
    TIMESTAMP_NS,
    TIMESTAMP_S,
    TINYINT,
    UBIGINT,
    UHUGEINT,
    UINTEGER,
    USMALLINT,
    UTINYINT,
    VARIANT,
    HardchargerDialect,
    Map,
    Struct,
    Union,
)
from sqlalchemy_hardcharger import types as hctypes
from sqlalchemy_hardcharger.dialect import _generated_expression, _index_expressions, _unwrap
from sqlalchemy_hardcharger.types import parse_type

DIALECT = HardchargerDialect()


def ddl(type_) -> str:
    return DIALECT.type_compiler_instance.process(type_)


@pytest.mark.parametrize(
    ("text", "expected_class"),
    [
        ("BIGINT", sqltypes.BIGINT),
        ("BIGNUM", BIGNUM),
        ("BIT", BIT),
        ("BLOB", sqltypes.BLOB),
        ("BOOLEAN", sqltypes.BOOLEAN),
        ("DATE", sqltypes.DATE),
        ("DOUBLE", sqltypes.DOUBLE),
        ("FLOAT", sqltypes.REAL),
        ("GEOMETRY", GEOMETRY),
        ("HUGEINT", HUGEINT),
        ("INTEGER", sqltypes.INTEGER),
        ("INTERVAL", postgresql.INTERVAL),
        ("JSON", sqltypes.JSON),
        ("SMALLINT", sqltypes.SMALLINT),
        ("TIME", sqltypes.TIME),
        ("TIME_NS", TIME_NS),
        ("TIMESTAMP", sqltypes.TIMESTAMP),
        ("TIMESTAMP_MS", TIMESTAMP_MS),
        ("TIMESTAMP_NS", TIMESTAMP_NS),
        ("TIMESTAMP_S", TIMESTAMP_S),
        ("TINYINT", TINYINT),
        ("UBIGINT", UBIGINT),
        ("UHUGEINT", UHUGEINT),
        ("UINTEGER", UINTEGER),
        ("USMALLINT", USMALLINT),
        ("UTINYINT", UTINYINT),
        ("UUID", sqltypes.UUID),
        ("VARCHAR", sqltypes.VARCHAR),
        ("VARIANT", VARIANT),
    ],
)
def test_scalar_types_parse_and_compile_back_to_the_same_text(text, expected_class):
    type_ = parse_type(text=text)
    assert isinstance(type_, expected_class)
    assert ddl(type_) == ("REAL" if text == "FLOAT" else text)


def test_timezone_types():
    timestamp = parse_type(text="TIMESTAMP WITH TIME ZONE")
    assert isinstance(timestamp, sqltypes.TIMESTAMP) and timestamp.timezone is True
    assert ddl(timestamp) == "TIMESTAMP WITH TIME ZONE"
    time = parse_type(text="TIME WITH TIME ZONE")
    assert isinstance(time, sqltypes.TIME) and time.timezone is True
    assert ddl(time) == "TIME WITH TIME ZONE"
    assert parse_type(text="TIMESTAMP").timezone is False


def test_decimal():
    type_ = parse_type(text="DECIMAL(10,2)")
    assert isinstance(type_, sqltypes.DECIMAL)
    assert (type_.precision, type_.scale) == (10, 2)
    assert ddl(type_) == "DECIMAL(10, 2)"


def test_enum_labels_with_quotes_and_commas():
    type_ = parse_type(text="ENUM('sad', 'it''s ok', 'a, b')")
    assert isinstance(type_, sqltypes.Enum)
    assert type_.enums == ["sad", "it's ok", "a, b"]
    assert ddl(type_) == "ENUM('sad', 'it''s ok', 'a, b')"


def test_lists_and_fixed_size_arrays():
    one = parse_type(text="INTEGER[]")
    assert isinstance(one, postgresql.ARRAY) and one.dimensions == 1
    assert isinstance(one.item_type, sqltypes.INTEGER)
    assert ddl(one) == "INTEGER[]"
    two = parse_type(text="VARCHAR[][]")
    assert two.dimensions == 2 and ddl(two) == "VARCHAR[][]"
    fixed = parse_type(text="DOUBLE[3]")
    assert isinstance(fixed, postgresql.ARRAY) and isinstance(fixed.item_type, sqltypes.DOUBLE)


def test_struct_map_union_nest():
    struct = parse_type(text='STRUCT(a INTEGER, "b c" VARCHAR, d STRUCT(e DECIMAL(4,1))[])')
    assert isinstance(struct, Struct)
    assert list(struct.fields) == ["a", "b c", "d"]
    assert isinstance(struct.fields["d"], postgresql.ARRAY)
    assert isinstance(struct.fields["d"].item_type, Struct)
    assert ddl(struct) == 'STRUCT(a INTEGER, "b c" VARCHAR, d STRUCT(e DECIMAL(4, 1))[])'

    map_ = parse_type(text="MAP(VARCHAR, INTEGER[])")
    assert isinstance(map_, Map)
    assert isinstance(map_.key_type, sqltypes.VARCHAR)
    assert ddl(map_) == "MAP(VARCHAR, INTEGER[])"

    union = parse_type(text="UNION(num INTEGER, str VARCHAR)")
    assert isinstance(union, Union)
    assert ddl(union) == "UNION(num INTEGER, str VARCHAR)"


@pytest.mark.parametrize("text", ["WIDGET", "STRUCT(a", "DECIMAL(10", "INTEGER junk", ""])
def test_an_unknown_or_malformed_type_is_none_never_a_guess(text):
    assert parse_type(text=text) is None


@pytest.mark.parametrize(
    ("type_", "expected"),
    [
        (sqltypes.Float(), "DOUBLE"),
        (sqltypes.Float(precision=24), "FLOAT"),
        (sqltypes.Float(precision=53), "DOUBLE"),
        (sqltypes.FLOAT(), "FLOAT"),
        (sqltypes.REAL(), "REAL"),
        (sqltypes.Double(), "DOUBLE"),
        (sqltypes.DateTime(), "TIMESTAMP"),
        (sqltypes.DateTime(timezone=True), "TIMESTAMP WITH TIME ZONE"),
        (sqltypes.Time(), "TIME"),
        (sqltypes.LargeBinary(), "BLOB"),
        (sqltypes.Uuid(), "UUID"),
        (sqltypes.Interval(), "INTERVAL"),
        (sqltypes.JSON(), "JSON"),
        (postgresql.JSONB(), "JSON"),
        (sqltypes.String(length=50), "VARCHAR(50)"),
        (sqltypes.Text(), "TEXT"),
        (sqltypes.Boolean(), "BOOLEAN"),
        (sqltypes.Numeric(precision=12, scale=3), "NUMERIC(12, 3)"),
        (sqltypes.Enum("a", "b"), "ENUM('a', 'b')"),
        (sqltypes.Enum("a", "b", name="mood"), "mood"),
        (sqltypes.ARRAY(sqltypes.Integer), "INTEGER[]"),
        (
            Struct(fields={"x": sqltypes.Integer, "y": sqltypes.String}),
            "STRUCT(x INTEGER, y VARCHAR)",
        ),
        (Map(key_type=sqltypes.String, value_type=sqltypes.Integer), "MAP(VARCHAR, INTEGER)"),
    ],
)
def test_generic_types_compile_to_duckdb_types(type_, expected):
    assert ddl(type_) == expected


def test_interval_values():
    type_ = sqltypes.Interval().dialect_impl(DIALECT)
    assert isinstance(type_, hctypes._HardchargerInterval)
    bind = type_.bind_processor(dialect=DIALECT)
    assert bind(datetime.timedelta(days=1, seconds=5, microseconds=7)) == (
        "1 days 5 seconds 7 microseconds"
    )
    assert bind(datetime.timedelta(seconds=-1)) == "-1 days 86399 seconds 0 microseconds"
    assert bind(None) is None
    result = type_.result_processor(dialect=DIALECT, coltype=None)
    assert result((0, 1, 5_000_007_000)) == datetime.timedelta(days=1, seconds=5, microseconds=7)
    assert result((2, 3, 0)) == datetime.timedelta(days=63)
    assert result(None) is None


def test_uuid_values():
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    as_uuid = sqltypes.Uuid().dialect_impl(DIALECT)
    assert as_uuid.bind_processor(dialect=DIALECT)(value) == str(value)
    assert as_uuid.result_processor(dialect=DIALECT, coltype=None)(str(value)) == value
    as_text = sqltypes.Uuid(as_uuid=False).dialect_impl(DIALECT)
    assert as_text.result_processor(dialect=DIALECT, coltype=None)(str(value)) == str(value)
    assert as_uuid.result_processor(dialect=DIALECT, coltype=None)(None) is None


def test_numeric_and_float_send_one_python_type_per_column():
    numeric = sqltypes.Numeric(precision=10, scale=2).dialect_impl(DIALECT)
    bind = numeric.bind_processor(dialect=DIALECT)
    assert bind(1.5) == decimal.Decimal("1.5") and isinstance(bind(1.5), decimal.Decimal)
    assert bind(2) == decimal.Decimal("2")
    assert bind(decimal.Decimal("3.25")) == decimal.Decimal("3.25")
    assert bind(None) is None

    float_ = sqltypes.Float().dialect_impl(DIALECT)
    bind = float_.bind_processor(dialect=DIALECT)
    assert bind(decimal.Decimal("1.5")) == 1.5 and isinstance(bind(decimal.Decimal("1.5")), float)
    assert bind(None) is None


def test_numeric_results_are_what_the_sqlalchemy_type_promises():
    as_decimal = sqltypes.Numeric(precision=10, scale=2).dialect_impl(DIALECT)
    result = as_decimal.result_processor(dialect=DIALECT, coltype=None)
    assert result(2.3) == decimal.Decimal("2.30") and isinstance(result(2.3), decimal.Decimal)
    assert result(decimal.Decimal("2.30")) == decimal.Decimal("2.30")
    assert result(None) is None

    as_float = sqltypes.Numeric(precision=10, scale=2, asdecimal=False).dialect_impl(DIALECT)
    result = as_float.result_processor(dialect=DIALECT, coltype=None)
    assert result(decimal.Decimal("2.5")) == 2.5 and isinstance(
        result(decimal.Decimal("2.5")), float
    )


def test_variant_values_travel_as_json():
    type_ = VARIANT()
    bind = type_.bind_processor(dialect=DIALECT)
    assert bind({"a": [1, 2]}) == '{"a": [1, 2]}'
    assert bind(None) is None
    result = type_.result_processor(dialect=DIALECT, coltype=None)
    assert result('{"a": [1, 2]}') == {"a": [1, 2]}
    assert result("42") == 42
    assert result(None) is None


def test_json_keys_and_paths_are_jsonpath_text():
    path = sqltypes.JSON.JSONPathType().dialect_impl(DIALECT)
    assert isinstance(path, hctypes._HardchargerJSONPath)
    bind = path.bind_processor(dialect=DIALECT)
    assert bind(("a", 0, "b c")) == '$."a"[0]."b c"'
    assert bind(("$a", "/p", -1)) == '$."$a"."/p"[-1]'
    assert bind("$.already.a.path") == "$.already.a.path"

    key = sqltypes.JSON.JSONStrIndexType().dialect_impl(DIALECT)
    assert isinstance(key, hctypes._HardchargerJSONStrIndex)
    bind = key.bind_processor(dialect=DIALECT)
    assert bind("a") == '$."a"'
    assert bind("$a") == '$."$a"'  # not a path of its own
    assert bind("/p") == '$."/p"'  # not a JSON Pointer
    assert bind('q"q') == '$."q\\"q"'
    assert bind("back\\slash") == '$."back\\\\slash"'

    index = sqltypes.JSON.JSONIntIndexType().dialect_impl(DIALECT)
    assert index.bind_processor(dialect=DIALECT)(2) == "$[2]"


def test_integers_in_and_out():
    integer = sqltypes.Integer().dialect_impl(DIALECT)
    assert isinstance(integer, hctypes._HardchargerInteger) and integer.render_bind_cast
    bind = integer.bind_processor(dialect=DIALECT)
    assert bind(True) == 1 and type(bind(True)) is int
    assert bind(5) == 5 and bind(None) is None
    result = integer.result_processor(dialect=DIALECT, coltype=None)
    assert result(decimal.Decimal("5")) == 5 and type(result(decimal.Decimal("5"))) is int
    assert result(7) == 7 and result(None) is None

    big = sqltypes.BigInteger().dialect_impl(DIALECT)
    assert isinstance(big, hctypes._HardchargerBigInteger) and not big.render_bind_cast
    small = sqltypes.SmallInteger().dialect_impl(DIALECT)
    assert isinstance(small, hctypes._HardchargerSmallInteger) and small.render_bind_cast
    # the DuckDB integer types keep their own name for the cast
    for type_ in (TINYINT(), UTINYINT(), USMALLINT(), UINTEGER(), UBIGINT(), HUGEINT(), UHUGEINT()):
        impl = type_.dialect_impl(DIALECT)
        assert type(impl) is type(type_) and impl.render_bind_cast


def test_wide_integers_are_text_on_the_wire_and_never_truncate_a_float():
    bind = HUGEINT().bind_processor(dialect=DIALECT)
    assert bind(2**100) == str(2**100)
    assert bind(True) == "1"
    assert bind(1.9) == 1.9  # for the server to round or refuse, not for us to cut to 1
    assert bind(None) is None
    result = HUGEINT().result_processor(dialect=DIALECT, coltype=None)
    assert result(str(-(2**127))) == -(2**127)


def test_datetime_values_do_not_depend_on_their_batch():
    tokyo = datetime.timezone(datetime.timedelta(hours=9))
    aware = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=tokyo)
    naive = datetime.datetime(2024, 1, 1, 12, 0)

    plain = sqltypes.DateTime().dialect_impl(DIALECT)
    assert isinstance(plain, hctypes._HardchargerDateTime) and not plain.render_bind_cast
    bind = plain.bind_processor(dialect=DIALECT)
    assert bind(aware) == naive  # the wall-clock time; the offset is dropped
    assert bind(naive) == naive
    assert bind(datetime.date(2024, 1, 1)) == datetime.datetime(2024, 1, 1)  # midnight
    assert bind(None) is None

    zoned = sqltypes.DateTime(timezone=True).dialect_impl(DIALECT)
    assert zoned.render_bind_cast
    bind = zoned.bind_processor(dialect=DIALECT)
    assert bind(aware) == "2024-01-01 12:00:00+09:00"
    assert bind(naive) == "2024-01-01 12:00:00"  # read in the session's time zone
    assert bind(datetime.date(2024, 1, 1)) == "2024-01-01"
    assert bind(None) is None

    # the DuckDB timestamp types go through the same handling
    assert isinstance(TIMESTAMP_S().dialect_impl(DIALECT), hctypes._HardchargerDateTime)


def test_time_with_time_zone_travels_as_text():
    plain = sqltypes.Time().dialect_impl(DIALECT)
    assert not plain.render_bind_cast and plain.bind_processor(dialect=DIALECT) is None

    zoned = sqltypes.Time(timezone=True).dialect_impl(DIALECT)
    assert isinstance(zoned, hctypes._HardchargerTime) and zoned.render_bind_cast
    india = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    assert zoned.bind_processor(dialect=DIALECT)(datetime.time(12, 0, tzinfo=india)) == (
        "12:00:00+05:30"
    )
    result = zoned.result_processor(dialect=DIALECT, coltype=None)
    assert result("12:00:00+05:30") == datetime.time(12, 0, tzinfo=india)
    assert result("12:00:00-05") == datetime.time(
        12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
    )
    assert result("01:02:03.123456-08").microsecond == 123456
    assert result(None) is None


def test_struct_and_union_types_can_be_statement_cache_keys():
    struct = Struct(fields={"a": sqltypes.Integer, "b": sqltypes.String})
    union = Union(members={"n": sqltypes.Integer})
    assert hash(struct._static_cache_key) == hash(
        Struct(fields={"a": sqltypes.Integer, "b": sqltypes.String})._static_cache_key
    )
    assert struct._static_cache_key != Struct(fields={"a": sqltypes.Integer})._static_cache_key
    hash(union._static_cache_key)
    hash(Map(key_type=sqltypes.String, value_type=struct)._static_cache_key)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[x]", [("x", True)]),
        ("[x, y]", [("x", True), ("y", True)]),
        ("['\"name\"']", [("name", True)]),
        ('[\'"q""uote"\', \'"we,ird"\']', [('q"uote', True), ("we,ird", True)]),
        (
            "['(lower(x))', z, '(lower(y))']",
            [("lower(x)", False), ("z", True), ("lower(y)", False)],
        ),
        ("['(COALESCE(x, \\'a, b\\'))']", [("COALESCE(x, 'a, b')", False)]),
        ("[]", []),
    ],
)
def test_index_expressions(text, expected):
    assert _index_expressions(text=text) == expected


@pytest.mark.parametrize(
    ("expression", "data_type", "expected"),
    [
        ("CAST((normal + 42) AS INTEGER)", "INTEGER", "normal + 42"),
        ("CAST(upper(a) AS VARCHAR)", "VARCHAR", "upper(a)"),
        (
            "CAST((a) AS INTEGER) + CAST((b) AS INTEGER)",
            "INTEGER",
            "CAST((a) AS INTEGER) + CAST((b) AS INTEGER)",
        ),
        ("(a + b)", "INTEGER", "(a + b)"),
        ("CAST((a + b) AS BIGINT)", "INTEGER", "CAST((a + b) AS BIGINT)"),
    ],
)
def test_generated_expression(expression, data_type, expected):
    assert _generated_expression(expression=expression, data_type=data_type) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("(a > 1)", "a > 1"),
        ("((a > 0) AND (a < 9))", "(a > 0) AND (a < 9)"),
        ("(a) > (b)", "(a) > (b)"),
    ],
)
def test_unwrap(expression, expected):
    assert _unwrap(expression=expression) == expected
