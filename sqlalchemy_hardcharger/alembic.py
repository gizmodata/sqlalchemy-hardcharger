"""Alembic support: tells Alembic how to run migrations on the ``hardcharger`` dialect.

Importing ``sqlalchemy_hardcharger`` registers this when Alembic is installed, so a migration
environment needs no extra line. DuckDB runs DDL inside transactions, so each migration is
applied atomically.
"""

from __future__ import annotations

import re
from typing import Any

from alembic.ddl.base import ColumnComment, format_column_name, format_table_name
from alembic.ddl.impl import DefaultImpl
from sqlalchemy import exc, schema, text
from sqlalchemy import types as sqltypes
from sqlalchemy.ext.compiler import compiles

from .dialect import SEQUENCE_COMMENT_PREFIX


class HardchargerImpl(DefaultImpl):
    __dialect__ = "hardcharger"
    transactional_ddl = True

    # -- autogenerate ---------------------------------------------------------------------------

    def _stored_type(self, type_: Any) -> str:
        """The type as DuckDB keeps it, as text: what a declared type turns into once a table is
        created (VARCHAR has no length, TEXT is VARCHAR, NUMERIC is DECIMAL, ...)."""
        rendered = self.dialect.type_compiler_instance.process(type_).upper()
        rendered = re.sub(r"\s+", "", rendered)
        rendered = re.sub(r"\b(?:VAR)?CHAR(?:\(\d+\))?", "VARCHAR", rendered)
        rendered = re.sub(r"\bTEXT\b", "VARCHAR", rendered)
        rendered = re.sub(r"\bNUMERIC\b", "DECIMAL", rendered)
        rendered = re.sub(r"\bDECIMAL(?!\()", "DECIMAL(18,3)", rendered)
        rendered = re.sub(r"\bFLOAT\b", "REAL", rendered)
        return rendered

    def compare_type(self, inspector_column: Any, metadata_column: Any) -> bool:
        """True when the column's type in the database differs from the declared one."""
        database, declared = inspector_column.type, metadata_column.type
        if isinstance(declared, sqltypes.Enum) and declared.native_enum:
            # A named enum reflects as its labels: the labels are what can differ.
            return not (
                isinstance(database, sqltypes.Enum) and list(database.enums) == list(declared.enums)
            )
        try:
            return self._stored_type(type_=database) != self._stored_type(type_=declared)
        except exc.CompileError:
            return False  # a type that can't be written as DDL can't be compared either

    def correct_for_autogen_constraints(
        self,
        conn_uniques: Any,
        conn_indexes: Any,
        metadata_unique_constraints: Any,
        metadata_indexes: Any,
    ) -> None:
        # DuckDB names constraints itself and doesn't keep a declared name, so a unique
        # constraint over the same columns is the same constraint, whatever its name.
        by_columns = {tuple(c.name for c in found.columns): found for found in conn_uniques}
        for declared in metadata_unique_constraints:
            found = by_columns.get(tuple(c.name for c in declared.columns))
            if found is not None:
                found.name = declared.name

    # -- operations -----------------------------------------------------------------------------

    def drop_table(self, table: Any, **kw: Any) -> None:
        # The table's key may be numbered by a sequence the dialect created with it. Alembic
        # describes a table to drop by name alone (and the table may have been renamed since),
        # so ask the database which sequences are this table's own before the table goes.
        sequences = [] if self.as_sql else self._own_sequences(table=table)
        super().drop_table(table, **kw)
        for sequence in sequences:
            self._exec(text(f"DROP SEQUENCE IF EXISTS {sequence}"))

    def _own_sequences(self, table: Any) -> list[str]:
        """Sequences the dialect made for this table's columns, that no other table uses."""
        connection, dialect = self.connection, self.dialect
        if connection is None:
            return []
        try:
            defaults = {
                column["default"]
                for column in dialect.get_columns(connection, table.name, schema=table.schema)
                if column["default"]
            }
        except exc.NoSuchTableError:
            return []
        where, params = dialect._scope(schema=table.schema)  # type: ignore[attr-defined]
        marked = connection.execute(
            text(f"SELECT sequence_name FROM duckdb_sequences() WHERE {where} AND comment LIKE :p"),
            {**params, "p": f"{SEQUENCE_COMMENT_PREFIX}%"},
        ).scalars()
        preparer = dialect.identifier_preparer
        found = []
        for name in marked:
            sequence = schema.Sequence(name, schema=table.schema)
            default = "nextval(%s)" % preparer.format_sequence_literal(sequence=sequence)  # type: ignore[attr-defined]
            if default not in defaults:
                continue
            others = connection.execute(
                text(
                    f"SELECT count(*) FROM duckdb_columns() WHERE {where} "
                    "AND column_default = :hc_default AND lower(table_name) <> lower(:hc_table)"
                ),
                {**params, "hc_default": default, "hc_table": table.name},
            ).scalar()
            if not others:
                found.append(preparer.format_sequence(sequence))
        return found


@compiles(ColumnComment, "hardcharger")
def _column_comment(element: Any, compiler: Any, **kw: Any) -> str:
    comment = (
        "NULL"
        if element.comment is None
        else compiler.sql_compiler.render_literal_value(element.comment, sqltypes.String())
    )
    return "COMMENT ON COLUMN %s.%s IS %s" % (
        format_table_name(compiler, element.table_name, element.schema),
        format_column_name(compiler, element.column_name),
        comment,
    )
