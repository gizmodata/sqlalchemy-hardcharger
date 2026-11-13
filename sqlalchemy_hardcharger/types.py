"""Hardcharger (DuckDB 2.0) column types for SQLAlchemy.

SQLAlchemy's generic types (``Integer``, ``String``, ``Numeric``, ``DateTime``, ``Uuid``, ``JSON``,
``Interval``, ``Enum``, ``ARRAY`` ...) work as they are. This module adds the DuckDB types that have
no generic counterpart, and the parser that turns the type text the server reports
(``DECIMAL(10,2)``, ``STRUCT(a INTEGER, b VARCHAR)[]``) back into SQLAlchemy types for reflection.
"""

from __future__ import annotations

import datetime
import decimal
import json
import uuid as _uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import ArgumentError
from sqlalchemy.sql import cast as sql_cast
from sqlalchemy.sql import type_coerce
from sqlalchemy.sql.type_api import TypeEngine, to_instance

# SQLAlchemy 2.1 asks every type which operators apply to it (2.0 has no such attribute).
try:
    from sqlalchemy.sql.operators import OperatorClass

    _ANY_OPERATORS: Any = OperatorClass.ANY
except ImportError:  # SQLAlchemy 2.0
    _ANY_OPERATORS = None

__all__ = [
    "BIGNUM",
    "BIT",
    "GEOMETRY",
    "HUGEINT",
    "TIMESTAMP_MS",
    "TIMESTAMP_NS",
    "TIMESTAMP_S",
    "TIME_NS",
    "TINYINT",
    "UBIGINT",
    "UHUGEINT",
    "UINTEGER",
    "USMALLINT",
    "UTINYINT",
    "VARIANT",
    "Map",
    "Struct",
    "Union",
    "parse_type",
]


# ---------------------------------------------------------------------------------------------
# Integers
# ---------------------------------------------------------------------------------------------


def _to_int(value: Any) -> Any:
    # sum() over an integer column is a HUGEINT, which the driver hands over as a Decimal.
    return int(value) if type(value) is decimal.Decimal else value


class _IntegerValues:
    """Python integers in and out.

    Sending: the driver binds every Python ``int`` as BIGINT, and DuckDB doesn't narrow a BIGINT
    by itself (``round(x, ?)``, ``chr(?)``, ``date + ?`` all want an INTEGER), so INTEGER and
    SMALLINT parameters are cast to their type. ``True``/``False`` go as 1/0, so a batch that
    mixes them with integers has one Arrow type.

    Receiving: integer aggregates wider than 64 bits (``sum()``) arrive as ``Decimal``.
    """

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> Any:
            return int(value) if isinstance(value, bool) else value

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        return _to_int


class _HardchargerInteger(_IntegerValues, sqltypes.Integer):
    render_bind_cast = True


class _HardchargerSmallInteger(_IntegerValues, sqltypes.SmallInteger):
    render_bind_cast = True


class _HardchargerBigInteger(_IntegerValues, sqltypes.BigInteger):
    pass


class TINYINT(_HardchargerInteger):
    """Signed 8-bit integer."""

    __visit_name__ = "TINYINT"


class UTINYINT(_HardchargerInteger):
    """Unsigned 8-bit integer."""

    __visit_name__ = "UTINYINT"


class USMALLINT(_HardchargerInteger):
    """Unsigned 16-bit integer."""

    __visit_name__ = "USMALLINT"


class UINTEGER(_HardchargerInteger):
    """Unsigned 32-bit integer."""

    __visit_name__ = "UINTEGER"


class _WideInteger:
    """Integers past 64 bits: sent as text and cast (Arrow has no integer that wide), and
    returned as a Python ``int``."""

    render_bind_cast = True

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> Any:
            if isinstance(value, bool):
                return str(int(value))
            # Anything else that isn't an integer goes as it is, for the server to refuse or
            # convert by its own rules.
            return str(value) if isinstance(value, int) else value

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: Any) -> Any:
            return None if value is None else int(value)

        return process


class _ReadAsText(_WideInteger):
    def column_expression(self, colexpr: Any) -> Any:
        # The driver can't return every value of this type as a number (a HUGEINT of 39 digits
        # doesn't fit Arrow's decimal, a BIGNUM comes in DuckDB's binary form): read its digits.
        return type_coerce(sql_cast(colexpr, sqltypes.String()), self)  # type: ignore[arg-type]


class UBIGINT(_WideInteger, _HardchargerBigInteger):
    """Unsigned 64-bit integer."""

    __visit_name__ = "UBIGINT"


class HUGEINT(_ReadAsText, _HardchargerBigInteger):
    """Signed 128-bit integer."""

    __visit_name__ = "HUGEINT"


class UHUGEINT(_ReadAsText, _HardchargerBigInteger):
    """Unsigned 128-bit integer."""

    __visit_name__ = "UHUGEINT"


class BIGNUM(_ReadAsText, _HardchargerBigInteger):
    """Arbitrary-precision integer (DuckDB ``BIGNUM``), read as a Python ``int``."""

    __visit_name__ = "BIGNUM"


# ---------------------------------------------------------------------------------------------
# Other scalars
# ---------------------------------------------------------------------------------------------


class TIMESTAMP_S(sqltypes.DateTime):
    """Timestamp with second precision."""

    __visit_name__ = "TIMESTAMP_S"


class TIMESTAMP_MS(sqltypes.DateTime):
    """Timestamp with millisecond precision."""

    __visit_name__ = "TIMESTAMP_MS"


class TIMESTAMP_NS(sqltypes.DateTime):
    """Timestamp with nanosecond precision.

    Python's ``datetime`` holds microseconds: a value with a nanosecond part needs pandas
    installed (the driver then returns a ``pandas.Timestamp``), or a cast in SQL.
    """

    __visit_name__ = "TIMESTAMP_NS"


class TIME_NS(sqltypes.Time):
    """Time of day with nanosecond precision."""

    __visit_name__ = "TIME_NS"


class BIT(sqltypes.TypeEngine[str]):
    """A string of bits (DuckDB ``BIT``), read and written as text such as ``'10110'``."""

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "BIT"
    render_bind_cast = True
    cache_ok = True

    @property
    def python_type(self) -> type:
        return str

    def column_expression(self, colexpr: Any) -> Any:
        # The driver returns a BIT in DuckDB's binary encoding: read its text form instead.
        return type_coerce(sql_cast(colexpr, sqltypes.String()), self)


class GEOMETRY(sqltypes.TypeEngine[Any]):
    """DuckDB's ``GEOMETRY``. Read it through a conversion function (``ST_AsText``,
    ``ST_AsWKB``) and write it through one (``ST_GeomFromText``)."""

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "GEOMETRY"


class _JSONText(sqltypes.TypeEngine[str]):
    """The JSON type as a cast target only: text in, text out, no (de)serializing."""

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "JSON"
    cache_ok = True


class VARIANT(sqltypes.TypeEngine[Any]):
    """DuckDB's ``VARIANT``: a column whose every value carries its own type (a number in one
    row, a list or an object in the next).

    Values travel as JSON: a Python ``int``, ``float``, ``str``, ``bool``, ``list`` or ``dict``
    is written with ``CAST(CAST(? AS JSON) AS VARIANT)`` and read back with
    ``CAST(column AS JSON)``, so a query returns Python values (the driver's own form of a
    VARIANT is its binary encoding). A value JSON has no type for, such as a date written by
    another client, reads back as text; ``cast(column, Date)`` in the query reads it typed.
    """

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "VARIANT"
    cache_ok = True

    def __init__(self, json_serializer: Any = None, json_deserializer: Any = None) -> None:
        self.json_serializer = json_serializer or json.dumps
        self.json_deserializer = json_deserializer or json.loads

    def bind_processor(self, dialect: Any) -> Any:
        serialize = self.json_serializer

        def process(value: Any) -> Any:
            return None if value is None else serialize(value)

        return process

    def bind_expression(self, bindvalue: Any) -> Any:
        return sql_cast(sql_cast(bindvalue, _JSONText()), self)

    def column_expression(self, colexpr: Any) -> Any:
        return type_coerce(sql_cast(colexpr, _JSONText()), self)

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        deserialize = self.json_deserializer

        def process(value: Any) -> Any:
            return deserialize(value) if isinstance(value, (str, bytes)) else value

        return process


# ---------------------------------------------------------------------------------------------
# Nested types
# ---------------------------------------------------------------------------------------------


class Struct(sqltypes.TypeEngine[Any]):
    """``STRUCT(name type, ...)``: read as a ``dict``.

    ``Struct({"street": String, "zip": Integer})``. Write one with a SQL expression
    (``func.struct_pack(...)`` or a ``{'street': ..., 'zip': ...}`` literal): the server doesn't
    take a struct as a bound parameter.
    """

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "HC_STRUCT"

    def __init__(self, fields: Mapping[str, Any]) -> None:
        if not fields:
            raise ArgumentError("a Struct needs at least one field")
        self.fields: dict[str, TypeEngine[Any]] = {
            name: to_instance(type_) for name, type_ in fields.items()
        }

    @property
    def python_type(self) -> type:
        return dict

    @property
    def _static_cache_key(self) -> Any:  # a dict of fields isn't hashable as it is
        return (self.__class__, tuple((n, t._static_cache_key) for n, t in self.fields.items()))


class Map(sqltypes.TypeEngine[Any]):
    """``MAP(key type, value type)``: read as a list of ``(key, value)`` pairs, as the driver
    returns it (a map's keys needn't be hashable in Python)."""

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "HC_MAP"

    def __init__(self, key_type: Any, value_type: Any) -> None:
        self.key_type: TypeEngine[Any] = to_instance(key_type)
        self.value_type: TypeEngine[Any] = to_instance(value_type)


class Union(sqltypes.TypeEngine[Any]):
    """``UNION(tag type, ...)``: a value of one of several types."""

    operator_classes = _ANY_OPERATORS

    __visit_name__ = "HC_UNION"

    def __init__(self, members: Mapping[str, Any]) -> None:
        if not members:
            raise ArgumentError("a Union needs at least one member")
        self.members: dict[str, TypeEngine[Any]] = {
            name: to_instance(type_) for name, type_ in members.items()
        }

    @property
    def _static_cache_key(self) -> Any:
        return (self.__class__, tuple((n, t._static_cache_key) for n, t in self.members.items()))


# ---------------------------------------------------------------------------------------------
# Types whose values the driver hands over in another form than SQLAlchemy expects
# ---------------------------------------------------------------------------------------------


class _NumericValues:
    """Decimals and floats, each way.

    Sending: the driver builds an Arrow column from the values of one parameter, and Arrow
    takes one Python type per column, so every value of a ``Numeric`` is sent as a ``Decimal``
    and every value of a ``Float`` as a ``float``, whichever the application passed.

    Receiving: the driver returns ``Decimal`` for DECIMAL and ``float`` for FLOAT/DOUBLE, and
    DuckDB answers some DECIMAL arithmetic (a division) in DOUBLE, so the value is converted to
    what the SQLAlchemy type promises (``asdecimal``) instead of trusting the column's type.
    """

    asdecimal: bool
    _effective_decimal_return_scale: int

    def _sends_decimal(self) -> bool:
        return self.asdecimal

    def bind_processor(self, dialect: Any) -> Any:
        if self._sends_decimal():

            def to_decimal(value: Any) -> Any:
                if value is None or isinstance(value, decimal.Decimal):
                    return value
                return decimal.Decimal(str(value))

            return to_decimal

        def to_float(value: Any) -> Any:
            return None if value is None else float(value)

        return to_float

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        if self.asdecimal:
            scale = self._effective_decimal_return_scale

            def to_decimal(value: Any) -> Any:
                if value is None or isinstance(value, decimal.Decimal):
                    return value
                if isinstance(value, float):
                    return decimal.Decimal(f"{value:.{scale}f}")
                return decimal.Decimal(value)

            return to_decimal

        def to_float(value: Any) -> Any:
            return None if value is None else float(value)

        return to_float


class _HardchargerNumeric(_NumericValues, sqltypes.Numeric):
    pass


class _HardchargerFloat(_NumericValues, sqltypes.Float):
    def _sends_decimal(self) -> bool:
        return False


class _HardchargerLargeBinary(sqltypes.LargeBinary):
    """The driver takes ``bytes`` as they are (it has no DB-API ``Binary`` wrapper)."""

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> Any:
            return None if value is None else bytes(value)

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: Any) -> Any:
            return None if value is None else bytes(value)

        return process


def _json_key(key: Any) -> str:
    """One step of a JSONPath: ``[3]`` for an array index, ``."name"`` for an object key."""
    if isinstance(key, int) and not isinstance(key, bool):
        return f"[{key}]"
    return '."%s"' % str(key).replace("\\", "\\\\").replace('"', '\\"')


class _JSONPathText:
    """JSON keys and paths as the JSONPath text DuckDB's ``->`` and ``->>`` take.

    A key is always sent as a path (``$."name"``), never bare: DuckDB reads a bare string that
    starts with ``$`` as a JSONPath and one that starts with ``/`` as a JSON Pointer, so the keys
    ``"$a"`` and ``"/p"`` would address something else.
    """

    string_bind_processor: Any
    string_literal_processor: Any

    @staticmethod
    def _format(value: Any) -> Any:
        raise NotImplementedError

    def bind_processor(self, dialect: Any) -> Any:
        string_processor = self.string_bind_processor(dialect)

        def process(value: Any) -> Any:
            value = self._format(value)
            return string_processor(value) if string_processor else value

        return process

    def literal_processor(self, dialect: Any) -> Any:
        string_processor = self.string_literal_processor(dialect)

        def process(value: Any) -> Any:
            value = self._format(value)
            return string_processor(value) if string_processor else value

        return process


def _one_key(value: Any) -> Any:
    return None if value is None else "$" + _json_key(key=value)


class _HardchargerJSONIndex(_JSONPathText, sqltypes.JSON.JSONIndexType):
    _format = staticmethod(_one_key)


class _HardchargerJSONIntIndex(_JSONPathText, sqltypes.JSON.JSONIntIndexType):
    _format = staticmethod(_one_key)


class _HardchargerJSONStrIndex(_JSONPathText, sqltypes.JSON.JSONStrIndexType):
    _format = staticmethod(_one_key)


class _HardchargerJSONPath(_JSONPathText, sqltypes.JSON.JSONPathType):
    """``column[("a", 0, "b")]`` -> ``$."a"[0]."b"``. A string is taken as JSONPath text."""

    @staticmethod
    def _format(value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return "$" + "".join(_json_key(key=part) for part in value)


class _HardchargerDateTime(sqltypes.DateTime):
    """Datetimes whose stored value doesn't depend on what else is in the batch.

    The driver builds one Arrow column per parameter, and Arrow gives a column one type: left
    alone, a time-zone-aware value in a batch of naive ones is shifted to UTC, a naive one among
    aware ones is taken as UTC, and a ``date`` first in a batch drops every later value's time.

    ``DateTime()`` (TIMESTAMP): a value's wall-clock time is stored; an offset is dropped, as
    PostgreSQL does. A ``date`` is midnight.

    ``DateTime(timezone=True)`` (TIMESTAMP WITH TIME ZONE): values are sent as ISO text and cast.
    An aware value is that instant; a naive one is read in the session's time zone.
    """

    @property
    def render_bind_cast(self) -> bool:  # type: ignore[override]
        return bool(self.timezone)

    def bind_processor(self, dialect: Any) -> Any:
        if self.timezone:

            def with_zone(value: Any) -> Any:
                if isinstance(value, datetime.datetime):
                    return value.isoformat(sep=" ")
                if isinstance(value, datetime.date):
                    return value.isoformat()
                return value

            return with_zone

        def without_zone(value: Any) -> Any:
            if isinstance(value, datetime.datetime):
                return value.replace(tzinfo=None) if value.tzinfo is not None else value
            if isinstance(value, datetime.date):
                return datetime.datetime(value.year, value.month, value.day)
            return value

        return without_zone


def _parse_time_with_zone(text: str) -> datetime.time:
    # DuckDB writes whole-hour offsets short ("12:00:00-05").
    if len(text) > 3 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    return datetime.time.fromisoformat(text)


class _HardchargerTime(sqltypes.Time):
    """``Time(timezone=True)`` (TIME WITH TIME ZONE) travels as text in both directions: as an
    Arrow time the driver would drop the offset on the way in and on the way out."""

    @property
    def render_bind_cast(self) -> bool:  # type: ignore[override]
        return bool(self.timezone)

    def bind_processor(self, dialect: Any) -> Any:
        if not self.timezone:
            return None

        def process(value: Any) -> Any:
            return value.isoformat() if isinstance(value, datetime.time) else value

        return process

    def column_expression(self, colexpr: Any) -> Any:
        if not self.timezone:
            return colexpr
        return type_coerce(sql_cast(colexpr, sqltypes.String()), self)

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        if not self.timezone:
            return None

        def process(value: Any) -> Any:
            return _parse_time_with_zone(text=value) if isinstance(value, str) else value

        return process


class _HardchargerUuid(sqltypes.Uuid):
    """The driver sends and returns a UUID as text; SQLAlchemy's ``Uuid`` works in
    ``uuid.UUID`` (or text, with ``as_uuid=False``). Parameters are cast to ``UUID``."""

    render_bind_cast = True

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> Any:
            return None if value is None else str(value)

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        if self.as_uuid:

            def process(value: Any) -> Any:
                if value is None or isinstance(value, _uuid.UUID):
                    return value
                return _uuid.UUID(value)

            return process

        def process_text(value: Any) -> Any:
            return None if value is None else str(value)

        return process_text

    def literal_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> str:
            return "'%s'" % str(value).replace("'", "''")

        return process


class _HardchargerPGUuid(_HardchargerUuid, postgresql.UUID):  # type: ignore[type-arg,misc]
    pass


class _HardchargerEnum(postgresql.ENUM):
    """An enum with a name is a type of its own (``CREATE TYPE ... AS ENUM``), created and
    dropped with the tables that use it, as on PostgreSQL. One without a name needs none of
    that: DuckDB takes its labels in the column definition, ``ENUM('a', 'b')``."""

    def create(self, bind: Any, checkfirst: bool = True, **kw: Any) -> None:
        if self.name:
            super().create(bind=bind, checkfirst=checkfirst, **kw)

    def drop(self, bind: Any, checkfirst: bool = True, **kw: Any) -> None:
        if self.name:
            super().drop(bind=bind, checkfirst=checkfirst, **kw)


def _interval_text(value: datetime.timedelta) -> str:
    return f"{value.days} days {value.seconds} seconds {value.microseconds} microseconds"


class _HardchargerInterval(postgresql.INTERVAL):
    """A ``timedelta`` is sent as interval text and cast; an interval comes back from the
    driver as (months, days, nanoseconds), and a month counts as 30 days here, as it does in
    DuckDB's own Python client and when DuckDB compares intervals."""

    render_bind_cast = True

    @classmethod
    def adapt_emulated_to_native(cls, interval: Any, **kw: Any) -> Any:  # type: ignore[override]
        return cls(precision=interval.second_precision)

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> Any:
            if isinstance(value, datetime.timedelta):
                return _interval_text(value=value)
            return value

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: Any) -> Any:
            if value is None or isinstance(value, datetime.timedelta):
                return value
            months, days, nanoseconds = value
            return datetime.timedelta(days=months * 30 + days, microseconds=nanoseconds // 1000)

        return process

    def literal_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> str:
            text = _interval_text(value=value) if isinstance(value, datetime.timedelta) else value
            return "INTERVAL '%s'" % str(text).replace("'", "''")

        return process


# ---------------------------------------------------------------------------------------------
# Reflection: the server's type text -> a SQLAlchemy type
# ---------------------------------------------------------------------------------------------

#: Type names as ``duckdb_columns().data_type`` spells them, without parameters.
ISCHEMA_NAMES: dict[str, Any] = {
    "BIGINT": sqltypes.BIGINT,
    "BIGNUM": BIGNUM,
    "BIT": BIT,
    "BLOB": sqltypes.BLOB,
    "BOOLEAN": sqltypes.BOOLEAN,
    "DATE": sqltypes.DATE,
    "DOUBLE": sqltypes.DOUBLE,
    "FLOAT": sqltypes.REAL,
    "GEOMETRY": GEOMETRY,
    "HUGEINT": HUGEINT,
    "INTEGER": sqltypes.INTEGER,
    "INTERVAL": postgresql.INTERVAL,
    "JSON": sqltypes.JSON,
    "SMALLINT": sqltypes.SMALLINT,
    "TIME": sqltypes.TIME,
    "TIME_NS": TIME_NS,
    "TIMESTAMP": sqltypes.TIMESTAMP,
    "TIMESTAMP_MS": TIMESTAMP_MS,
    "TIMESTAMP_NS": TIMESTAMP_NS,
    "TIMESTAMP_S": TIMESTAMP_S,
    "TINYINT": TINYINT,
    "UBIGINT": UBIGINT,
    "UHUGEINT": UHUGEINT,
    "UINTEGER": UINTEGER,
    "USMALLINT": USMALLINT,
    "UTINYINT": UTINYINT,
    "UUID": sqltypes.UUID,
    "VARCHAR": sqltypes.VARCHAR,
    "VARIANT": VARIANT,
}


class _TypeParser:
    """Recursive-descent parser for DuckDB's canonical type text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def fail(self, what: str) -> ValueError:
        return ValueError(f"can't parse the type {self.text!r}: {what} at position {self.pos}")

    def skip_spaces(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self) -> str:
        self.skip_spaces()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def take(self, char: str) -> bool:
        if self.peek() == char:
            self.pos += 1
            return True
        return False

    def expect(self, char: str) -> None:
        if not self.take(char=char):
            raise self.fail(what=f"expected {char!r}")

    def quoted(self, quote: str) -> str:
        """A quoted string or identifier; a doubled quote is one quote character."""
        self.expect(char=quote)
        out = []
        while True:
            if self.pos >= len(self.text):
                raise self.fail(what=f"unterminated {quote}")
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                if self.pos < len(self.text) and self.text[self.pos] == quote:
                    out.append(quote)
                    self.pos += 1
                    continue
                return "".join(out)
            out.append(char)

    def word(self) -> str:
        self.skip_spaces()
        start = self.pos
        while self.pos < len(self.text) and (
            self.text[self.pos].isalnum() or self.text[self.pos] == "_"
        ):
            self.pos += 1
        if start == self.pos:
            raise self.fail(what="expected a name")
        return self.text[start : self.pos]

    def identifier(self) -> str:
        return self.quoted(quote='"') if self.peek() == '"' else self.word()

    def number(self) -> int:
        self.skip_spaces()
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if start == self.pos:
            raise self.fail(what="expected a number")
        return int(self.text[start : self.pos])

    def named_fields(self) -> dict[str, TypeEngine[Any]]:
        self.expect(char="(")
        fields: dict[str, TypeEngine[Any]] = {}
        while True:
            name = self.identifier()
            fields[name] = self.type()
            if self.take(char=")"):
                return fields
            self.expect(char=",")

    def base(self) -> TypeEngine[Any]:
        name = self.word().upper()
        if name == "STRUCT":
            return Struct(fields=self.named_fields())
        if name == "UNION":
            return Union(members=self.named_fields())
        if name == "MAP":
            self.expect(char="(")
            key_type = self.type()
            self.expect(char=",")
            value_type = self.type()
            self.expect(char=")")
            return Map(key_type=key_type, value_type=value_type)
        if name == "ENUM":
            self.expect(char="(")
            labels = []
            while True:
                labels.append(self.quoted(quote="'"))
                if self.take(char=")"):
                    break
                self.expect(char=",")
            return sqltypes.Enum(*labels)
        if name == "DECIMAL":
            if not self.take(char="("):
                return sqltypes.DECIMAL()
            precision = self.number()
            self.expect(char=",")
            scale = self.number()
            self.expect(char=")")
            return sqltypes.DECIMAL(precision=precision, scale=scale)
        if name in ("TIMESTAMP", "TIME"):
            # "TIMESTAMP WITH TIME ZONE", "TIME WITH TIME ZONE"
            mark = self.pos
            self.skip_spaces()
            if self.text[self.pos : self.pos + 14].upper() == "WITH TIME ZONE":
                self.pos += 14
                cls = sqltypes.TIMESTAMP if name == "TIMESTAMP" else sqltypes.TIME
                return cls(timezone=True)
            self.pos = mark
        try:
            type_ = ISCHEMA_NAMES[name]
        except KeyError:
            raise self.fail(what=f"unknown type {name!r}") from None
        return to_instance(type_)

    def type(self) -> TypeEngine[Any]:
        type_ = self.base()
        dimensions = 0
        while self.take(char="["):
            if self.peek() != "]":
                self.number()  # a fixed-size ARRAY's length; SQLAlchemy's ARRAY has no place for it
            self.expect(char="]")
            dimensions += 1
        if dimensions:
            return postgresql.ARRAY(item_type=type_, dimensions=dimensions)
        return type_

    def parse(self) -> TypeEngine[Any]:
        type_ = self.type()
        if self.peek():
            raise self.fail(what="unexpected text")
        return type_


def parse_type(text: str) -> TypeEngine[Any] | None:
    """The SQLAlchemy type for a type as the server reports it (``duckdb_columns().data_type``),
    or ``None`` for one this dialect doesn't know."""
    try:
        return _TypeParser(text=text).parse()
    except ValueError:
        return None
