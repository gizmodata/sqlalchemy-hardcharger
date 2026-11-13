"""Alembic support: tells Alembic how to run migrations on the ``hardcharger`` dialect.

Importing ``sqlalchemy_hardcharger`` registers this when Alembic is installed, so a migration
environment needs no extra line. DuckDB runs DDL inside transactions, so each migration is
applied atomically.
"""

from __future__ import annotations

from typing import Any

from alembic.ddl.impl import DefaultImpl
from sqlalchemy import exc, schema, text


class HardchargerImpl(DefaultImpl):
    __dialect__ = "hardcharger"
    transactional_ddl = True

    def drop_table(self, table: Any, **kw: Any) -> None:
        # op.drop_table("name") describes the table by name alone, so the DROP TABLE the
        # dialect writes can't tell which sequence numbered its key (see the dialect's
        # get_column_specification). Ask the database before the table goes.
        sequences = [] if self.as_sql or len(table.columns) else self._key_sequences(table=table)
        super().drop_table(table, **kw)
        for sequence in sequences:
            self._exec(text(f"DROP SEQUENCE IF EXISTS {sequence}"))

    def _key_sequences(self, table: Any) -> list[str]:
        preparer = self.dialect.identifier_preparer
        connection = self.connection
        if connection is None:
            return []
        try:
            columns = self.dialect.get_columns(connection, table.name, schema=table.schema)
        except exc.NoSuchTableError:
            return []
        found = []
        for column in columns:
            name = f"{table.name}_{column['name']}_seq"
            sequence = schema.Sequence(name, schema=table.schema)
            literal = preparer.format_sequence_literal(sequence=sequence)  # type: ignore[attr-defined]
            if column["default"] == f"nextval({literal})":
                found.append(preparer.format_sequence(sequence))
        return found
