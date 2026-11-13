"""The SQLAlchemy dialect for Hardcharger, GizmoData's DuckDB 2.0 server.

Statements are compiled as DuckDB SQL (on SQLAlchemy's PostgreSQL compilers, which DuckDB's
syntax follows, with the differences overridden here), run through the native Hardcharger ADBC
driver over Chargewire HTTP(S), and reflection reads DuckDB's own catalog functions
(``duckdb_tables()``, ``duckdb_columns()``, ``duckdb_constraints()`` ...).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import exc, pool, schema, text, util
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql.base import (
    PGCompiler,
    PGDDLCompiler,
    PGDialect,
    PGIdentifierPreparer,
    PGTypeCompiler,
)
from sqlalchemy.engine import reflection
from sqlalchemy.engine.default import DefaultDialect, DefaultExecutionContext
from sqlalchemy.engine.interfaces import BindTyping
from sqlalchemy.engine.url import URL
from sqlalchemy.sql import cast as sql_cast
from sqlalchemy.sql import compiler
from sqlalchemy.sql.compiler import InsertmanyvaluesSentinelOpts

from . import types as hctypes
from ._version import __version__

#: Hardcharger's Chargewire port, used when the URL names none (the driver uses 443 for a
#: GizmoData Cloud host).
DEFAULT_PORT = 31337

#: Isolation levels: DuckDB transactions run under snapshot isolation; ``AUTOCOMMIT`` sends no
#: ``BEGIN``, so every statement commits on its own.
ISOLATION_LEVELS = ("SNAPSHOT", "AUTOCOMMIT")

# The DuckDB 2.0 keywords that can't be a table or column name unquoted: its "reserved" and
# "type_function" categories (duckdb_keywords()). A test compares this set with the running
# server's, and tries each word as a column and a table name.
RESERVED_WORDS = frozenset(
    """
    all analyse analyze and any array as asc asymmetric both case cast check collate column
    constraint create default deferrable desc describe distinct do else end except false fetch
    for foreign from group having in initially intersect into lambda lateral leading limit not
    null offset on only or order pivot pivot_longer pivot_wider placing primary qualify
    references returning select show some summarize symmetric table then to trailing true union
    unique unpivot using variadic when where window with

    anti asof at authorization binary by collation columns concurrently cross freeze full
    generated glob ilike inner is isnull join left like map natural notnull outer overlaps
    positional right semi similar struct tablesample try_cast tuple unpack verbose
    """.split()
)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

# Error codes after which the connection's session is gone for good: the pool then discards the
# connection and opens another instead of handing the dead one out again.
_DISCONNECT_MARKERS = (
    "(session_ended",
    "(session_not_found",
    "(invalid_token",
    "(server_draining",
    "connection refused",
    "connection reset",
    "broken pipe",
    "no such host",
    "EOF",
)


def _as_bool(name: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise exc.ArgumentError(f"URL option {name}={value!r} is not a boolean: use true or false")


def _as_bool_or_text(name: str, value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return value


def _as_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise exc.ArgumentError(f"URL option {name}={value!r} is not a number") from None


def _as_text(name: str, value: str) -> str:
    return value


# URL query option -> (connect() keyword, converter)
_URL_OPTIONS: dict[str, tuple[str, Any]] = {
    "profile": ("profile", _as_text),
    "auth_type": ("auth_type", _as_text),
    "access_token": ("access_token", _as_text),
    "subject_token": ("subject_token", _as_text),
    "oauth_timeout": ("oauth_timeout", _as_float),
    "open_browser": ("open_browser", _as_bool_or_text),
    "share_sign_in": ("share_sign_in", _as_bool_or_text),
    "resume_timeout": ("resume_timeout", _as_float),
    "session_tag": ("session_tag", _as_text),
    "query_tag": ("query_tag", _as_text),
    "tls_skip_verify": ("tls_skip_verify", _as_bool),
    "tls_root_certs": ("tls_root_certs", _as_text),
    "tls_client_cert": ("tls_client_cert", _as_text),
    "tls_client_key": ("tls_client_key", _as_text),
    "tls_server_name": ("tls_server_name", _as_text),
    "schema": ("db_schema", _as_text),
}


# ---------------------------------------------------------------------------------------------
# The DB-API connection SQLAlchemy sees
# ---------------------------------------------------------------------------------------------


class HardchargerConnection:
    """The driver's connection, with transactions begun only when SQLAlchemy begins one.

    The driver connection stays in autocommit. ``BEGIN`` goes out when SQLAlchemy starts a
    transaction (at the first statement, not at checkout), and ``COMMIT``/``ROLLBACK`` only when
    a ``BEGIN`` did. So a connection resting in the pool holds no transaction open on the
    server, and returning one that ran nothing costs no request (which matters for a GizmoData
    Cloud cluster that is stopped: nothing wakes it until a statement runs).

    Every other attribute is the driver connection's: ``adbc_get_objects()``,
    ``set_query_tag()``, ``session_id`` and the rest.
    """

    def __init__(self, driver_connection: Any) -> None:
        self.driver_connection = driver_connection
        self.autocommit = False
        self.in_transaction = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver_connection, name)

    def cursor(self) -> Any:
        return self.driver_connection.cursor()

    def _run(self, statement: str) -> None:
        cursor = self.driver_connection.cursor()
        try:
            cursor.execute(statement)
        finally:
            cursor.close()

    def begin(self) -> None:
        if self.autocommit or self.in_transaction:
            return
        self._run(statement="BEGIN")
        self.in_transaction = True

    def commit(self) -> None:
        if not self.in_transaction:
            return
        # A COMMIT that fails leaves in_transaction set, so the ROLLBACK SQLAlchemy sends next
        # reaches the server and clears an aborted transaction.
        self._run(statement="COMMIT")
        self.in_transaction = False

    def rollback(self) -> None:
        if not self.in_transaction:
            return
        try:
            self._run(statement="ROLLBACK")
        except Exception as error:
            # The server already ended the transaction (a failed COMMIT rolls back).
            if "no transaction is active" not in str(error):
                raise
        finally:
            self.in_transaction = False

    def close(self) -> None:
        self.in_transaction = False
        self.driver_connection.close()


# ---------------------------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------------------------


def _serial_sequence(column: Any, schema_name: str | None) -> schema.Sequence | None:
    """The sequence that numbers an auto-incrementing integer primary key.

    DuckDB has neither ``SERIAL`` nor identity columns, so a column SQLAlchemy treats as
    auto-incrementing (an integer primary key with no default of its own, or one declared with
    ``Identity()``) gets ``DEFAULT nextval('<table>_<column>_seq')``, and CREATE TABLE creates
    that sequence first. Returns ``None`` for every other column.
    """
    table = column.table
    if table is None or not column.primary_key or column is not table._autoincrement_column:
        return None
    if column.type._type_affinity is not sqltypes.Integer:
        return None
    identity = column.identity
    if column.default is not None:
        if not (isinstance(column.default, schema.Sequence) and column.default.optional):
            return None
    if column.server_default is not None and identity is None:
        return None
    options: dict[str, Any] = {}
    if identity is not None:
        for name in ("start", "increment", "minvalue", "maxvalue", "cycle"):
            value = getattr(identity, name, None)
            if value is not None:
                options[name] = value
    return schema.Sequence(f"{table.name}_{column.name}_seq", schema=schema_name, **options)


class HardchargerIdentifierPreparer(PGIdentifierPreparer):
    reserved_words = RESERVED_WORDS

    def _separate(self, name: str) -> tuple[str | None, str]:
        """Split a schema name into (catalog, schema).

        A SQLAlchemy "schema" may be ``schema`` or ``catalog.schema``, DuckDB's three-part
        names being ``catalog.schema.table``. A name that itself contains a dot is written
        quoted: ``'"my.catalog".main'``.
        """
        parts = [
            quoted.replace('""', '"') if quoted or not plain else plain
            for quoted, plain in re.findall(r'"((?:[^"]|"")*)"|([^."]+)', name)
        ]
        if len(parts) == 1:
            return None, parts[0]
        if len(parts) == 2:
            return parts[0], parts[1]
        raise exc.ArgumentError(
            f"schema {name!r} must be 'schema' or 'catalog.schema' (quote a name that contains "
            "a dot)"
        )

    def format_schema(self, name: str) -> str:
        catalog, schema_name = self._separate(name=name)
        if catalog is None:
            return self.quote(schema_name)
        return f"{self.quote(catalog)}.{self.quote(schema_name)}"

    def quote_schema(self, schema: str, force: Any = None) -> str:  # noqa: A002
        return self.format_schema(name=schema)

    def format_sequence_literal(self, sequence: Any) -> str:
        """A sequence's name as the string literal nextval() takes."""
        return "'%s'" % self.format_sequence(sequence, use_schema=True).replace("'", "''")


# ---------------------------------------------------------------------------------------------
# Compilers
# ---------------------------------------------------------------------------------------------


class HardchargerCompiler(PGCompiler):
    preparer: HardchargerIdentifierPreparer

    def for_update_clause(self, select: Any, **kw: Any) -> str:
        # DuckDB has no row locks: its transactions are optimistic (a conflicting write fails
        # at the write or at COMMIT), so SELECT ... FOR UPDATE has nothing to take.
        return ""

    def visit_sequence(self, seq: Any, **kw: Any) -> str:
        return "nextval(%s)" % self.preparer.format_sequence_literal(sequence=seq)

    def visit_floordiv_binary(self, binary: Any, operator: Any, **kw: Any) -> str:
        left = self.process(binary.left, **kw)
        right = self.process(binary.right, **kw)
        if (
            binary.left.type._type_affinity is sqltypes.Integer
            and binary.right.type._type_affinity is sqltypes.Integer
        ):
            return f"{left} // {right}"
        return f"FLOOR({left} / {right})"

    def visit_bitwise_xor_op_binary(self, binary: Any, operator: Any, **kw: Any) -> str:
        return "xor(%s, %s)" % (self.process(binary.left, **kw), self.process(binary.right, **kw))

    def _regexp_args(self, binary: Any, kw: Any) -> str:
        args = [self.process(binary.left, **kw), self.process(binary.right, **kw)]
        flags = binary.modifiers["flags"]
        if flags is not None:
            args.append(self.render_literal_value(flags, sqltypes.STRINGTYPE))
        return ", ".join(args)

    def visit_regexp_match_op_binary(self, binary: Any, operator: Any, **kw: Any) -> str:
        return f"regexp_matches({self._regexp_args(binary=binary, kw=kw)})"

    def visit_not_regexp_match_op_binary(self, binary: Any, operator: Any, **kw: Any) -> str:
        return f"NOT regexp_matches({self._regexp_args(binary=binary, kw=kw)})"

    def visit_regexp_replace_op_binary(self, binary: Any, operator: Any, **kw: Any) -> str:
        args = [
            self.process(binary.left, **kw),
            self.process(binary.right, **kw),
        ]
        flags = binary.modifiers["flags"]
        if flags is not None:
            args.append(self.render_literal_value(flags, sqltypes.STRINGTYPE))
        return f"regexp_replace({', '.join(args)})"

    def visit_try_cast(self, element: Any, **kw: Any) -> str:
        return "TRY_CAST(%s AS %s)" % (
            self.process(element.clause, **kw),
            self.process(element.typeclause, **kw),
        )

    def visit_json_path_getitem_op_binary(
        self, binary: Any, operator: Any, _cast_applied: bool = False, **kw: Any
    ) -> str:
        # DuckDB has no #> / #>> operators: -> and ->> take a JSONPath ('$."a"[0]') instead.
        if not _cast_applied and binary.type._type_affinity is not sqltypes.JSON:
            kw["_cast_applied"] = True
            return self.process(sql_cast(binary, binary.type), **kw)
        kw["eager_grouping"] = True
        return self._generate_generic_binary(binary, " -> " if not _cast_applied else " ->> ", **kw)

    def visit_match_op_binary(self, binary: Any, operator: Any, **kw: Any) -> str:
        raise exc.CompileError(
            "Hardcharger has no MATCH operator: use the full-text search extension's functions "
            "(fts_main_<table>.match_bm25) through func"
        )


class HardchargerDDLCompiler(PGDDLCompiler):
    preparer: HardchargerIdentifierPreparer

    def _serial_sequence(self, column: Any) -> schema.Sequence | None:
        table = column.table
        return _serial_sequence(column=column, schema_name=None if table is None else table.schema)

    def get_column_specification(self, column: Any, **kwargs: Any) -> str:
        colspec = self.preparer.format_column(column)
        colspec += " " + self.dialect.type_compiler_instance.process(
            column.type, type_expression=column, identifier_preparer=self.preparer
        )
        sequence = self._serial_sequence(column=column)
        if sequence is not None:
            colspec += " DEFAULT nextval(%s)" % self.preparer.format_sequence_literal(
                sequence=sequence
            )
        else:
            default = self.get_column_default_string(column)
            if default is not None:
                colspec += " DEFAULT " + default
        if column.computed is not None:
            colspec += " " + self.process(column.computed)
        if not column.nullable:
            colspec += " NOT NULL"
        return colspec

    def _serial_sequences(self, table: Any) -> list[schema.Sequence]:
        column = table._autoincrement_column
        if column is None:
            return []
        sequence = self._serial_sequence(column=column)
        return [] if sequence is None else [sequence]

    def visit_create_table(self, create: Any, **kw: Any) -> str:
        statement = super().visit_create_table(create, **kw)
        prefix = ""
        table = create.element
        # A temporary table's sequence is temporary too, so it goes when the session does.
        temporary = any(p.upper() in ("TEMPORARY", "TEMP") for p in table._prefixes)
        for sequence in self._serial_sequences(table=table):
            options = self.get_identity_options(sequence)
            prefix += "CREATE %sSEQUENCE IF NOT EXISTS %s%s;\n" % (
                "TEMPORARY " if temporary else "",
                self.preparer.format_sequence(sequence),
                f" {options}" if options else "",
            )
        return prefix + statement

    def visit_drop_table(self, drop: Any, **kw: Any) -> str:
        statement = super().visit_drop_table(drop, **kw)
        for sequence in self._serial_sequences(table=drop.element):
            statement += ";\nDROP SEQUENCE IF EXISTS %s" % self.preparer.format_sequence(sequence)
        return statement

    def visit_computed_column(self, generated: Any, **kw: Any) -> str:
        if generated.persisted is True:
            raise exc.CompileError(
                "Hardcharger (DuckDB) generated columns are virtual: drop persisted=True"
            )
        return "GENERATED ALWAYS AS (%s) VIRTUAL" % self.sql_compiler.process(
            generated.sqltext, include_table=False, literal_binds=True
        )

    def visit_create_sequence(self, create: Any, **kw: Any) -> str:
        # DuckDB's CREATE SEQUENCE takes no "AS <type>" (PostgreSQL's does).
        return compiler.DDLCompiler.visit_create_sequence(self, create, **kw)

    def visit_create_index(self, create: Any, **kw: Any) -> str:
        # Plain CREATE [UNIQUE] INDEX: none of PostgreSQL's CONCURRENTLY/USING/INCLUDE/WHERE.
        return compiler.DDLCompiler.visit_create_index(self, create, **kw)

    def visit_drop_index(self, drop: Any, **kw: Any) -> str:
        return compiler.DDLCompiler.visit_drop_index(self, drop, **kw)

    def visit_create_enum_type(self, create: Any, **kw: Any) -> str:
        type_ = create.element
        return "CREATE TYPE %s AS ENUM (%s)" % (
            self.preparer.format_type(type_),
            ", ".join(
                self.sql_compiler.render_literal_value(label, sqltypes.STRINGTYPE)
                for label in type_.enums
            ),
        )


class HardchargerTypeCompiler(PGTypeCompiler):
    def _named(name: str) -> Any:  # type: ignore[misc]  # noqa: N805
        def visit(self: Any, type_: Any, **kw: Any) -> str:
            return name

        return visit

    visit_TINYINT = _named("TINYINT")
    visit_UTINYINT = _named("UTINYINT")
    visit_USMALLINT = _named("USMALLINT")
    visit_UINTEGER = _named("UINTEGER")
    visit_UBIGINT = _named("UBIGINT")
    visit_HUGEINT = _named("HUGEINT")
    visit_UHUGEINT = _named("UHUGEINT")
    visit_BIGNUM = _named("BIGNUM")
    visit_TIMESTAMP_S = _named("TIMESTAMP_S")
    visit_TIMESTAMP_MS = _named("TIMESTAMP_MS")
    visit_TIMESTAMP_NS = _named("TIMESTAMP_NS")
    visit_TIME_NS = _named("TIME_NS")
    visit_BIT = _named("BIT")
    visit_GEOMETRY = _named("GEOMETRY")
    visit_VARIANT = _named("VARIANT")
    visit_UUID = _named("UUID")
    visit_uuid = _named("UUID")
    visit_INTERVAL = _named("INTERVAL")
    visit_JSON = _named("JSON")
    visit_JSONB = _named("JSON")
    visit_BYTEA = _named("BLOB")
    visit_BLOB = _named("BLOB")
    visit_BINARY = _named("BLOB")
    visit_VARBINARY = _named("BLOB")
    visit_large_binary = _named("BLOB")
    visit_DOUBLE = _named("DOUBLE")
    visit_double = _named("DOUBLE")
    visit_DOUBLE_PRECISION = _named("DOUBLE")
    visit_REAL = _named("REAL")

    del _named

    def visit_FLOAT(self, type_: Any, **kw: Any) -> str:
        # FLOAT is 4 bytes in DuckDB; a precision past 24 bits means a DOUBLE, as in the standard.
        if type_.precision is not None and type_.precision > 24:
            return "DOUBLE"
        return "FLOAT"

    def visit_float(self, type_: Any, **kw: Any) -> str:
        # SQLAlchemy's generic Float holds a Python float, which is 8 bytes: a 4-byte FLOAT
        # would hand back a different number than was stored. So Float() is a DOUBLE, and a
        # FLOAT is what Float(precision=24 or less), FLOAT() or REAL() ask for.
        if type_.precision is not None and type_.precision <= 24:
            return "FLOAT"
        return "DOUBLE"

    def visit_TIMESTAMP(self, type_: Any, **kw: Any) -> str:
        return "TIMESTAMP WITH TIME ZONE" if type_.timezone else "TIMESTAMP"

    def visit_datetime(self, type_: Any, **kw: Any) -> str:
        return self.visit_TIMESTAMP(type_, **kw)

    def visit_DATETIME(self, type_: Any, **kw: Any) -> str:
        return self.visit_TIMESTAMP(type_, **kw)

    def visit_TIME(self, type_: Any, **kw: Any) -> str:
        return "TIME WITH TIME ZONE" if type_.timezone else "TIME"

    def visit_time(self, type_: Any, **kw: Any) -> str:
        return self.visit_TIME(type_, **kw)

    def _enum(self, type_: Any, identifier_preparer: Any) -> str:
        if type_.name:
            preparer: Any = identifier_preparer or self.dialect.identifier_preparer
            return preparer.format_type(type_)
        # DuckDB takes an enum's labels in place: ENUM('a', 'b') needs no CREATE TYPE.
        return "ENUM(%s)" % ", ".join("'%s'" % label.replace("'", "''") for label in type_.enums)

    def visit_ENUM(self, type_: Any, identifier_preparer: Any = None, **kw: Any) -> str:
        return self._enum(type_=type_, identifier_preparer=identifier_preparer)

    def visit_enum(self, type_: Any, identifier_preparer: Any = None, **kw: Any) -> str:
        if not type_.native_enum:
            return compiler.GenericTypeCompiler.visit_enum(self, type_, **kw)
        return self._enum(type_=type_, identifier_preparer=identifier_preparer)

    def _fields(self, fields: dict[str, Any], kw: Any) -> str:
        quote = self.dialect.identifier_preparer.quote
        return ", ".join(
            f"{quote(name)} {self.process(type_, **kw)}" for name, type_ in fields.items()
        )

    def visit_HC_STRUCT(self, type_: Any, **kw: Any) -> str:
        return f"STRUCT({self._fields(fields=type_.fields, kw=kw)})"

    def visit_HC_UNION(self, type_: Any, **kw: Any) -> str:
        return f"UNION({self._fields(fields=type_.members, kw=kw)})"

    def visit_HC_MAP(self, type_: Any, **kw: Any) -> str:
        return "MAP(%s, %s)" % (
            self.process(type_.key_type, **kw),
            self.process(type_.value_type, **kw),
        )


# ---------------------------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------------------------


class HardchargerExecutionContext(DefaultExecutionContext):
    identifier_preparer: HardchargerIdentifierPreparer

    def fire_sequence(self, seq: Any, type_: Any) -> Any:
        return self._execute_scalar(
            "SELECT nextval(%s)" % self.identifier_preparer.format_sequence_literal(sequence=seq),
            type_,
        )

    def get_insert_default(self, column: Any) -> Any:
        if column.primary_key and column is column.table._autoincrement_column:
            sequence = _serial_sequence(
                column=column, schema_name=self.connection.schema_for_object(column.table)
            )
            if sequence is not None:
                return self.fire_sequence(seq=sequence, type_=column.type)
        return super().get_insert_default(column)


# ---------------------------------------------------------------------------------------------
# The dialect
# ---------------------------------------------------------------------------------------------


class HardchargerDialect(PGDialect):
    name = "hardcharger"
    driver = "adbc"

    supports_statement_cache = True
    default_paramstyle = "qmark"
    bind_typing = BindTyping.RENDER_CASTS

    statement_compiler = HardchargerCompiler
    ddl_compiler = HardchargerDDLCompiler
    type_compiler_cls = HardchargerTypeCompiler
    preparer = HardchargerIdentifierPreparer
    identifier_preparer: HardchargerIdentifierPreparer
    execution_ctx_cls = HardchargerExecutionContext
    inspector = reflection.Inspector

    # DuckDB identifiers have no length limit to speak of.
    max_identifier_length = 1024

    supports_alter = False  # no ALTER TABLE ... ADD/DROP CONSTRAINT for foreign keys
    supports_comments = True
    supports_constraint_comments = False
    supports_identity_columns = False
    supports_smallserial = False
    supports_virtual_generated_columns = True
    supports_sequences = True
    sequences_optional = True
    preexecute_autoincrement_sequences = True
    supports_native_boolean = True
    supports_native_decimal = True
    supports_native_enum = True
    supports_native_uuid = True
    supports_sane_rowcount = True
    supports_sane_multi_rowcount = True
    supports_server_side_cursors = False
    supports_default_values = True
    supports_empty_insert = False
    supports_multivalues_insert = True
    div_is_floordiv = False
    postfetch_lastrowid = False
    insert_returning = True
    update_returning = True
    delete_returning = True
    update_returning_multifrom = True
    delete_returning_multifrom = True
    use_insertmanyvalues = True
    # DuckDB doesn't promise the order RETURNING gives a multi-row INSERT's rows in, so the ORM
    # matches them to its objects by a client-side key, never by position.
    insertmanyvalues_implicit_sentinel = InsertmanyvaluesSentinelOpts.NOT_SUPPORTED
    returns_native_bytes = True

    _backslash_escapes = False
    _supports_create_index_concurrently = False
    _supports_drop_index_concurrently = False
    _supports_jsonb_subscripting = False

    construct_arguments: list[Any] = []
    reflection_options = ()

    ischema_names = hctypes.ISCHEMA_NAMES
    colspecs = util.update_copy(
        PGDialect.colspecs,
        {
            sqltypes.Numeric: hctypes._HardchargerNumeric,
            sqltypes.Float: hctypes._HardchargerFloat,
            sqltypes.LargeBinary: hctypes._HardchargerLargeBinary,
            sqltypes.JSON.JSONPathType: hctypes._HardchargerJSONPath,
            sqltypes.Enum: hctypes._HardchargerEnum,
            postgresql.ENUM: hctypes._HardchargerEnum,
            sqltypes.Uuid: hctypes._HardchargerUuid,
            postgresql.UUID: hctypes._HardchargerPGUuid,
            sqltypes.Interval: hctypes._HardchargerInterval,
            postgresql.INTERVAL: hctypes._HardchargerInterval,
        },
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    # -- connecting ---------------------------------------------------------------------------

    @classmethod
    def import_dbapi(cls) -> Any:
        from adbc_driver_hardcharger import dbapi

        return dbapi

    @classmethod
    def get_pool_class(cls, url: URL) -> type[pool.Pool]:
        return pool.QueuePool

    def create_connect_args(self, url: URL) -> Any:
        """``hardcharger://user:password@host[:port][/catalog][?option=value...]``.

        TLS is on unless ``?tls=false``. The path names the catalog to make current, and
        ``?schema=`` the schema. The other options are ``connect()`` keywords of the Hardcharger
        ADBC driver (``auth_type``, ``access_token``, ``tls_skip_verify``, ``tls_root_certs``,
        ...); ``hardcharger.<name>`` is a session setting and ``adbc.<...>`` a driver option.
        Anything else is refused, never dropped.
        """
        kwargs: dict[str, Any] = {}
        query: dict[str, str] = {}
        for name, value in url.query.items():
            if not isinstance(value, str):
                raise exc.ArgumentError(f"URL option {name!r} is given more than once")
            query[name] = value

        tls = _as_bool(name="tls", value=query.pop("tls")) if "tls" in query else True
        profile = query.get("profile")
        if url.host:
            host = f"[{url.host}]" if ":" in url.host else url.host
            uri = f"hardcharger://{host}"
            if url.port is not None:
                uri += f":{url.port}"
            if not tls:
                uri += "?tls=false"
            kwargs["uri"] = uri
        elif not profile:
            raise exc.ArgumentError(
                "a hardcharger:// URL needs a host (hardcharger://user:password@host:31337) or "
                "?profile=<ADBC connection profile>"
            )
        if url.username is not None:
            kwargs["username"] = url.username
        if url.password is not None:
            kwargs["password"] = url.password
        if url.database:
            kwargs["catalog"] = url.database

        settings: dict[str, str] = {}
        db_kwargs: dict[str, str] = {}
        for name, value in query.items():
            if name == "catalog":
                if kwargs.get("catalog", value) != value:
                    raise exc.ArgumentError(
                        "the URL names two catalogs: give it in the path or as ?catalog=, not both"
                    )
                kwargs["catalog"] = value
            elif name in _URL_OPTIONS:
                keyword, convert = _URL_OPTIONS[name]
                kwargs[keyword] = convert(name, value)
            elif name.startswith("hardcharger."):
                settings[name[len("hardcharger.") :]] = value
            elif name.startswith("adbc."):
                db_kwargs[name] = value
            else:
                raise exc.ArgumentError(
                    f"unknown hardcharger:// URL option {name!r}. Options: tls, catalog, "
                    f"{', '.join(sorted(_URL_OPTIONS))}, hardcharger.<setting> and adbc.<option>"
                )
        if settings:
            kwargs["settings"] = settings
        if db_kwargs:
            kwargs["db_kwargs"] = db_kwargs
        return [], kwargs

    def connect(self, *cargs: Any, **cparams: Any) -> Any:
        if cparams.get("auth_type") == "external":
            # One browser sign-in for the whole pool, not one window per pooled connection.
            cparams.setdefault("share_sign_in", True)
        # The driver applies a schema before the catalog (adbc-driver-hardcharger 2.4.9), so a
        # schema of a catalog that isn't the server's default is "not found". When both are
        # given, the driver sets the catalog and the schema is chosen here, once connected.
        schema_name = cparams.pop("db_schema", None) if cparams.get("catalog") else None
        driver_connection = self.loaded_dbapi.connect(*cargs, **cparams)
        if schema_name is not None:
            quote = self.identifier_preparer.quote_identifier
            try:
                cursor = driver_connection.cursor()
                try:
                    cursor.execute(f"USE {quote(cparams['catalog'])}.{quote(schema_name)}")
                finally:
                    cursor.close()
            except Exception:
                driver_connection.close()
                raise
        return HardchargerConnection(driver_connection=driver_connection)

    def get_driver_connection(self, connection: Any) -> Any:
        return getattr(connection, "driver_connection", connection)

    def initialize(self, connection: Any) -> None:
        DefaultDialect.initialize(self, connection)

    def _get_server_version_info(self, connection: Any) -> tuple[int, ...]:
        version = connection.exec_driver_sql("SELECT HARDCHARGER_VERSION()").scalar()
        return tuple(int(part) for part in re.findall(r"\d+", version.split("-")[0])[:3])

    def _get_default_schema_name(self, connection: Any) -> str:
        return connection.exec_driver_sql("SELECT current_schema()").scalar()

    def is_disconnect(self, e: Any, connection: Any, cursor: Any) -> bool:
        message = str(e)
        return any(marker in message for marker in _DISCONNECT_MARKERS)

    def do_executemany(
        self, cursor: Any, statement: Any, parameters: Any, context: Any = None
    ) -> None:
        if parameters and not any(parameters):
            # A statement without parameters, run once per (empty) parameter set: the driver's
            # executemany() has nothing to bind and would run it zero times.
            rowcount = 0
            for _ in parameters:
                cursor.execute(statement)
                rowcount = -1 if cursor.rowcount < 0 or rowcount < 0 else rowcount + cursor.rowcount
            if context is not None:
                context._rowcount = rowcount
            return
        cursor.executemany(statement, parameters)

    # -- transactions -------------------------------------------------------------------------

    def do_begin(self, dbapi_connection: Any) -> None:
        dbapi_connection.begin()

    def do_commit(self, dbapi_connection: Any) -> None:
        dbapi_connection.commit()

    def do_rollback(self, dbapi_connection: Any) -> None:
        dbapi_connection.rollback()

    def do_savepoint(self, connection: Any, name: str) -> None:
        raise exc.InvalidRequestError(
            "Hardcharger (DuckDB) has no savepoints, so nested transactions "
            "(Connection.begin_nested(), Session.begin_nested()) aren't available"
        )

    def get_isolation_level_values(self, dbapi_connection: Any) -> tuple[str, ...]:
        return ISOLATION_LEVELS

    def get_default_isolation_level(self, dbapi_conn: Any) -> str:
        return "SNAPSHOT"

    def get_isolation_level(self, dbapi_connection: Any) -> str:
        return "AUTOCOMMIT" if dbapi_connection.autocommit else "SNAPSHOT"

    def set_isolation_level(self, dbapi_connection: Any, level: str) -> None:
        dbapi_connection.rollback()
        dbapi_connection.autocommit = level == "AUTOCOMMIT"

    def detect_autocommit_setting(self, dbapi_conn: Any) -> bool:
        return bool(dbapi_conn.autocommit)

    # -- reflection ---------------------------------------------------------------------------

    def _scope(self, schema: str | None, temporary: str = "exclude") -> tuple[str, dict[str, Any]]:
        """A WHERE fragment (and its parameters) choosing the catalog and schema to look in.

        ``schema`` is ``None`` (the session's current catalog and schema), ``'schema'`` (in the
        current catalog) or ``'catalog.schema'``. ``temporary`` says what to do about the
        session's temporary objects, which live in the ``temp`` catalog: ``exclude`` them,
        ``include`` them next to the current schema's (only when ``schema`` is None), or look at
        ``only`` them.
        """
        if temporary == "only":
            return "database_name = 'temp'", {}
        if schema is None:
            where = "(database_name = current_catalog() AND schema_name = current_schema())"
            if temporary == "include":
                where = f"({where} OR database_name = 'temp')"
            return where, {}
        catalog, schema_name = self.identifier_preparer._separate(name=schema)
        if catalog is None:
            return (
                "(database_name = current_catalog() AND lower(schema_name) = lower(:hc_schema))",
                {"hc_schema": schema_name},
            )
        return (
            "(lower(database_name) = lower(:hc_catalog)"
            " AND lower(schema_name) = lower(:hc_schema))",
            {"hc_catalog": catalog, "hc_schema": schema_name},
        )

    def _names(
        self,
        connection: Any,
        function: str,
        column: str,
        schema: str | None,
        temporary: str,
        extra: str = "",
    ) -> list[str]:
        where, params = self._scope(schema=schema, temporary=temporary)
        rows = connection.execute(
            text(f"SELECT {column} FROM {function}() WHERE {where}{extra} ORDER BY 1"), params
        )
        return [row[0] for row in rows]

    @reflection.cache
    def get_schema_names(self, connection: Any, **kw: Any) -> list[str]:
        """The current catalog's schemas by name, then every other attached catalog's as
        ``catalog.schema`` (a name any ``schema=`` argument takes)."""
        rows = connection.execute(
            text(
                """
                SELECT database_name = current_catalog() AS current, database_name, schema_name
                FROM duckdb_schemas()
                WHERE database_name NOT IN (
                    SELECT database_name FROM duckdb_databases() WHERE internal
                )
                ORDER BY 1 DESC, 2, 3
                """
            )
        )
        quote = self.identifier_preparer.quote
        names = []
        for current, catalog, schema_name in rows:
            plain = "." not in schema_name and '"' not in schema_name
            if current:
                names.append(schema_name if plain else quote(schema_name))
            else:
                names.append(f"{quote(catalog)}.{quote(schema_name)}")
        return names

    @reflection.cache
    def has_schema(self, connection: Any, schema_name: str, **kw: Any) -> bool:
        where, params = self._scope(schema=schema_name)
        return (
            connection.execute(
                text(f"SELECT 1 FROM duckdb_schemas() WHERE {where} LIMIT 1"), params
            ).first()
            is not None
        )

    @reflection.cache
    def get_table_names(self, connection: Any, schema: str | None = None, **kw: Any) -> list[str]:
        return self._names(
            connection=connection,
            function="duckdb_tables",
            column="table_name",
            schema=schema,
            temporary="exclude",
            extra=" AND NOT internal AND NOT temporary",
        )

    @reflection.cache
    def get_temp_table_names(
        self, connection: Any, schema: str | None = None, **kw: Any
    ) -> list[str]:
        return self._names(
            connection=connection,
            function="duckdb_tables",
            column="table_name",
            schema=None,
            temporary="only",
        )

    @reflection.cache
    def get_view_names(self, connection: Any, schema: str | None = None, **kw: Any) -> list[str]:
        return self._names(
            connection=connection,
            function="duckdb_views",
            column="view_name",
            schema=schema,
            temporary="exclude",
            extra=" AND NOT internal AND NOT temporary",
        )

    @reflection.cache
    def get_temp_view_names(
        self, connection: Any, schema: str | None = None, **kw: Any
    ) -> list[str]:
        return self._names(
            connection=connection,
            function="duckdb_views",
            column="view_name",
            schema=None,
            temporary="only",
            extra=" AND NOT internal",
        )

    @reflection.cache
    def get_materialized_view_names(
        self, connection: Any, schema: str | None = None, **kw: Any
    ) -> list[str]:
        return []

    @reflection.cache
    def get_sequence_names(
        self, connection: Any, schema: str | None = None, **kw: Any
    ) -> list[str]:
        return self._names(
            connection=connection,
            function="duckdb_sequences",
            column="sequence_name",
            schema=schema,
            temporary="exclude",
        )

    def _has(
        self,
        connection: Any,
        function: str,
        column: str,
        name: str,
        schema: str | None,
        extra: str = "",
    ) -> bool:
        where, params = self._scope(schema=schema, temporary="include")
        params["hc_name"] = name
        return (
            connection.execute(
                text(
                    f"SELECT 1 FROM {function}() "
                    f"WHERE {where} AND lower({column}) = lower(:hc_name){extra} LIMIT 1"
                ),
                params,
            ).first()
            is not None
        )

    @reflection.cache
    def has_table(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> bool:
        self._ensure_has_table_connection(connection)
        return self._has(
            connection=connection,
            function="duckdb_tables",
            column="table_name",
            name=table_name,
            schema=schema,
        ) or self._has(
            connection=connection,
            function="duckdb_views",
            column="view_name",
            name=table_name,
            schema=schema,
            extra=" AND NOT internal",
        )

    @reflection.cache
    def has_sequence(
        self, connection: Any, sequence_name: str, schema: str | None = None, **kw: Any
    ) -> bool:
        return self._has(
            connection=connection,
            function="duckdb_sequences",
            column="sequence_name",
            name=sequence_name,
            schema=schema,
        )

    @reflection.cache
    def has_type(
        self, connection: Any, type_name: str, schema: str | None = None, **kw: Any
    ) -> bool:
        return self._has(
            connection=connection,
            function="duckdb_types",
            column="type_name",
            name=type_name,
            schema=None if schema == "*" else schema,
            extra=" AND NOT internal",
        )

    def _table_rows(
        self,
        connection: Any,
        function: str,
        columns: str,
        table_name: str,
        schema: str | None,
        extra: str = "",
        name_column: str = "table_name",
    ) -> list[Any]:
        where, params = self._scope(schema=schema, temporary="include")
        params["hc_table"] = table_name
        return list(
            connection.execute(
                text(
                    f"SELECT {columns} FROM {function}() "
                    f"WHERE {where} AND lower({name_column}) = lower(:hc_table){extra}"
                ),
                params,
            )
        )

    def _require_table(self, connection: Any, table_name: str, schema: str | None) -> None:
        if not self.has_table(connection, table_name, schema=schema):
            raise exc.NoSuchTableError(f"{schema}.{table_name}" if schema else table_name)

    @reflection.cache
    def get_columns(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> list[Any]:
        rows = self._table_rows(
            connection=connection,
            function="duckdb_columns",
            columns=(
                "column_name, data_type, is_nullable, column_default, comment, is_generated, "
                "generation_expression"
            ),
            table_name=table_name,
            schema=schema,
            extra=" ORDER BY column_index",
        )
        if not rows:
            raise exc.NoSuchTableError(f"{schema}.{table_name}" if schema else table_name)
        columns = []
        for name, data_type, nullable, default, comment, generated, expression in rows:
            type_ = hctypes.parse_type(text=data_type)
            if type_ is None:
                util.warn(f"Did not recognize type '{data_type}' of column '{name}'")
                type_ = sqltypes.NULLTYPE
            column: dict[str, Any] = {
                "name": name,
                "type": type_,
                "nullable": bool(nullable),
                "default": None if generated else default,
                "autoincrement": bool(default) and not generated and "nextval(" in default,
                "comment": comment,
            }
            if generated:
                column["computed"] = {
                    "sqltext": _generated_expression(expression=expression, data_type=data_type),
                    "persisted": False,
                }
            columns.append(column)
        return columns

    def _constraints(
        self, connection: Any, table_name: str, schema: str | None, kind: str
    ) -> list[Any]:
        self._require_table(connection=connection, table_name=table_name, schema=schema)
        where, params = self._scope(schema=schema, temporary="include")
        params.update({"hc_table": table_name, "hc_kind": kind})
        return list(
            connection.execute(
                text(
                    "SELECT constraint_name, constraint_column_names, expression, "
                    "referenced_table, referenced_column_names "
                    f"FROM duckdb_constraints() WHERE {where} "
                    "AND lower(table_name) = lower(:hc_table) AND constraint_type = :hc_kind "
                    "ORDER BY constraint_index"
                ),
                params,
            )
        )

    @reflection.cache
    def get_pk_constraint(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> Any:
        rows = self._constraints(
            connection=connection, table_name=table_name, schema=schema, kind="PRIMARY KEY"
        )
        if not rows:
            return {"constrained_columns": [], "name": None}
        return {"constrained_columns": list(rows[0][1]), "name": rows[0][0]}

    @reflection.cache
    def get_foreign_keys(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> list[Any]:
        keys: dict[tuple[Any, ...], dict[str, Any]] = {}
        for name, columns, _, referred_table, referred_columns in self._constraints(
            connection=connection, table_name=table_name, schema=schema, kind="FOREIGN KEY"
        ):
            key = (tuple(columns), referred_table, tuple(referred_columns))
            # DuckDB names a foreign key after its columns, so two declared over the same
            # columns report the same name: they are one key to SQLAlchemy.
            keys.setdefault(
                key,
                {
                    "name": name,
                    "constrained_columns": list(columns),
                    # A foreign key can't leave its table's schema in DuckDB.
                    "referred_schema": schema,
                    "referred_table": referred_table,
                    "referred_columns": list(referred_columns),
                    "options": {},
                },
            )
        return list(keys.values())

    @reflection.cache
    def get_unique_constraints(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> list[Any]:
        return [
            {"name": name, "column_names": list(columns)}
            for name, columns, _, _, _ in self._constraints(
                connection=connection, table_name=table_name, schema=schema, kind="UNIQUE"
            )
        ]

    @reflection.cache
    def get_check_constraints(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> list[Any]:
        return [
            {"name": name, "sqltext": _unwrap(expression=expression)}
            for name, _, expression, _, _ in self._constraints(
                connection=connection, table_name=table_name, schema=schema, kind="CHECK"
            )
        ]

    @reflection.cache
    def get_indexes(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> list[Any]:
        self._require_table(connection=connection, table_name=table_name, schema=schema)
        rows = self._table_rows(
            connection=connection,
            function="duckdb_indexes",
            columns="index_name, is_unique, expressions",
            table_name=table_name,
            schema=schema,
            extra=" ORDER BY index_name",
        )
        indexes = []
        for name, unique, expressions in rows:
            index: dict[str, Any] = {
                "name": name,
                "unique": bool(unique),
                "column_names": [],
                "dialect_options": {},
            }
            parts = _index_expressions(text=expressions)
            if any(not plain for _, plain in parts):
                index["expressions"] = [value for value, _ in parts]
            index["column_names"] = [value if plain else None for value, plain in parts]
            indexes.append(index)
        return indexes

    @reflection.cache
    def get_table_comment(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> Any:
        rows = self._table_rows(
            connection=connection,
            function="duckdb_tables",
            columns="comment",
            table_name=table_name,
            schema=schema,
        ) or self._table_rows(
            connection=connection,
            function="duckdb_views",
            columns="comment",
            table_name=table_name,
            schema=schema,
            name_column="view_name",
        )
        if not rows:
            raise exc.NoSuchTableError(f"{schema}.{table_name}" if schema else table_name)
        return {"text": rows[0][0]}

    @reflection.cache
    def get_view_definition(
        self, connection: Any, view_name: str, schema: str | None = None, **kw: Any
    ) -> Any:
        rows = self._table_rows(
            connection=connection,
            function="duckdb_views",
            columns="sql",
            table_name=view_name,
            schema=schema,
            name_column="view_name",
        )
        if not rows:
            raise exc.NoSuchTableError(f"{schema}.{view_name}" if schema else view_name)
        return rows[0][0]

    def get_table_oid(
        self, connection: Any, table_name: str, schema: str | None = None, **kw: Any
    ) -> Any:
        raise NotImplementedError()

    # The multi-table reflection entry points go through the single-table ones above, not
    # through PostgreSQL's pg_catalog queries.
    get_multi_columns = DefaultDialect.get_multi_columns
    get_multi_pk_constraint = DefaultDialect.get_multi_pk_constraint
    get_multi_foreign_keys = DefaultDialect.get_multi_foreign_keys
    get_multi_indexes = DefaultDialect.get_multi_indexes
    get_multi_unique_constraints = DefaultDialect.get_multi_unique_constraints
    get_multi_check_constraints = DefaultDialect.get_multi_check_constraints
    get_multi_table_comment = DefaultDialect.get_multi_table_comment
    get_multi_table_options = DefaultDialect.get_multi_table_options
    # DuckDB tables have no storage options to reflect.
    get_table_options = DefaultDialect.get_table_options
    if hasattr(DefaultDialect, "has_multi_table"):
        has_multi_table = DefaultDialect.has_multi_table


def _generated_expression(expression: str, data_type: str) -> str:
    """A generated column's expression without the ``CAST(... AS <column type>)`` DuckDB wraps
    it in, so that reflecting a table and creating it again doesn't nest one cast per trip."""
    prefix, suffix = "CAST(", f" AS {data_type})"
    if expression.startswith(prefix) and expression.endswith(suffix):
        inner = expression[len(prefix) : -len(suffix)]
        if inner.startswith("(") and inner.endswith(")") and _balanced(text=inner[1:-1]):
            inner = inner[1:-1]
        if _balanced(text=inner):
            return inner
    return expression


def _unwrap(expression: str) -> str:
    """An expression without the one pair of parentheses DuckDB stores around it."""
    if expression.startswith("(") and expression.endswith(")") and _balanced(text=expression[1:-1]):
        return expression[1:-1]
    return expression


def _balanced(text: str) -> bool:  # noqa: A002
    depth = 0
    for char in text:
        depth += char == "("
        depth -= char == ")"
        if depth < 0:
            return False
    return depth == 0


def _index_expressions(text: str) -> list[tuple[str, bool]]:  # noqa: A002
    """Split ``duckdb_indexes().expressions`` (``[a, '"b c"', (x + 1)]``) into
    ``(text, is_plain_column)`` pairs."""
    body = text.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    parts: list[tuple[str, bool]] = []
    depth = 0
    current: list[str] = []
    quote: str | None = None
    previous = ""
    for char in body + ",":
        if quote:
            current.append(char)
            if char == quote and previous != "\\":
                quote = None
            previous = char
            continue
        previous = char
        if char in "'\"":
            quote = char
            current.append(char)
        elif char in "([":
            depth += 1
            current.append(char)
        elif char in ")]":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            item = "".join(current).strip()
            current = []
            if not item:
                continue
            if len(item) >= 2 and item[0] == "'" and item[-1] == "'":
                item = item[1:-1].replace("\\'", "'")
            if len(item) >= 2 and item[0] == '"' and item[-1] == '"' and '"' not in item[1:-1]:
                parts.append((item[1:-1], True))
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item):
                parts.append((item, True))
            else:
                # DuckDB wraps an expression in one pair of parentheses of its own.
                if item.startswith("(") and item.endswith(")") and _balanced(text=item[1:-1]):
                    item = item[1:-1]
                parts.append((item, False))
        else:
            current.append(char)
    return parts


__all__ = [
    "DEFAULT_PORT",
    "ISOLATION_LEVELS",
    "HardchargerConnection",
    "HardchargerDialect",
    "__version__",
]
