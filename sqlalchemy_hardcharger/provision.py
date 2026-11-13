"""Hooks SQLAlchemy's test suite uses to prepare a database (``sqlalchemy.testing.provision``)."""

from sqlalchemy import inspect, select
from sqlalchemy.testing.provision import (
    delete_from_all_tables,
    set_default_schema_on_connection,
    sort_tables_and_constraints,
    temp_table_keyword_args,
)


@temp_table_keyword_args.for_db("hardcharger")
def _temp_table_keyword_args(cfg, eng):  # noqa: ANN001, ANN202
    return {"prefixes": ["TEMPORARY"]}


@delete_from_all_tables.for_db("hardcharger")
def _delete_from_all_tables(connection, cfg, metadata):  # noqa: ANN001, ANN202
    """Empty every table, children first, committing after each one.

    DuckDB checks a foreign key against rows deleted earlier in the same transaction, so a
    parent's rows can be deleted only once the delete of its children is committed.
    """
    inspector = inspect(connection)
    tables = [
        table
        for table, _ in sort_tables_and_constraints(metadata.tables.values())
        if table is not None and table.name in inspector.get_table_names(schema=table.schema)
    ]
    # The suite calls this inside a transaction of its own, so the deletes go through a second
    # connection, in autocommit.
    with connection.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table in reversed(tables):
            for fk in table.foreign_keys:
                if fk.column.table is not table:
                    continue
                # Rows that point at rows of their own table: DuckDB refuses to delete (or
                # update) a row another row still points at, even in the same statement, so
                # delete the rows nothing points at until none is left.
                referenced = select(fk.parent).where(fk.parent.is_not(None))
                for _ in range(1000):
                    deleted = conn.execute(table.delete().where(fk.column.not_in(referenced)))
                    if deleted.rowcount <= 0:
                        break
            conn.execute(table.delete())


@set_default_schema_on_connection.for_db("hardcharger")
def _set_default_schema_on_connection(cfg, dbapi_connection, schema_name):  # noqa: ANN001, ANN202
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET schema = '%s'" % schema_name.replace("'", "''"))
    finally:
        cursor.close()
