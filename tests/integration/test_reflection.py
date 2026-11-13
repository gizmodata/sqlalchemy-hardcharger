"""Reflection through DuckDB's catalog functions, against a real server."""

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    exc,
    func,
    inspect,
    text,
)

from sqlalchemy_hardcharger.dialect import RESERVED_WORDS


@pytest.fixture
def schema(engine, name):
    """parents/children tables with every kind of constraint, a view, an index, comments."""
    metadata = MetaData()
    parents = Table(
        f"{name}_parents",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(10), nullable=False, comment="short code"),
        Column("qty", Integer, server_default=text("5")),
        Column("doubled", Integer, Computed("qty * 2")),
        UniqueConstraint("code", name="declared_name_is_not_kept"),
        CheckConstraint("qty > 0 AND qty < 1000"),
        comment="the parents",
    )
    Table(
        f"{name}_children",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("parent_id", Integer, ForeignKey(f"{name}_parents.id")),
        Column("label", String),
        UniqueConstraint("parent_id", "label"),
    )
    Index(f"{name}_ix_code", parents.c.code)
    Index(f"{name}_ix_expr", func.lower(parents.c.code), parents.c.qty)
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE VIEW {name}_view AS SELECT id, code FROM {name}_parents"))
        conn.execute(text(f"COMMENT ON VIEW {name}_view IS 'a view'"))
    yield name
    with engine.begin() as conn:
        conn.execute(text(f"DROP VIEW IF EXISTS {name}_view"))
    metadata.drop_all(bind=engine)


def test_tables_views_and_has_table(engine, schema):
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert f"{schema}_parents" in tables and f"{schema}_children" in tables
    assert f"{schema}_view" not in tables
    assert f"{schema}_view" in inspector.get_view_names()
    assert inspector.has_table(table_name=f"{schema}_parents")
    assert inspector.has_table(table_name=f"{schema}_view")  # views count, as in SQLAlchemy 2
    assert inspector.has_table(table_name=f"{schema}_PARENTS")  # DuckDB names ignore case
    assert not inspector.has_table(table_name=f"{schema}_nope")
    assert inspector.get_materialized_view_names() == []


def test_columns(engine, schema):
    columns = {c["name"]: c for c in inspect(engine).get_columns(table_name=f"{schema}_parents")}
    assert list(columns) == ["id", "code", "qty", "doubled"]
    assert columns["id"]["autoincrement"] is True
    assert columns["id"]["default"] == f"nextval('{schema}_parents_id_seq')"
    assert columns["id"]["nullable"] is False
    assert columns["code"]["comment"] == "short code"
    assert columns["code"]["nullable"] is False
    assert columns["code"]["type"].length is None  # DuckDB's VARCHAR keeps no length
    assert columns["qty"]["default"] == "5"
    assert columns["qty"]["autoincrement"] is False
    assert columns["doubled"]["computed"] == {"sqltext": "qty * 2", "persisted": False}
    assert columns["doubled"]["default"] is None


def test_constraints_carry_the_names_duckdb_gives_them(engine, schema):
    inspector = inspect(engine)
    parents, children = f"{schema}_parents", f"{schema}_children"
    assert inspector.get_pk_constraint(table_name=parents) == {
        "constrained_columns": ["id"],
        "name": f"{parents}_id_pkey",
    }
    assert inspector.get_unique_constraints(table_name=parents) == [
        {"name": f"{parents}_code_key", "column_names": ["code"]}
    ]
    assert inspector.get_unique_constraints(table_name=children) == [
        {"name": f"{children}_parent_id_label_key", "column_names": ["parent_id", "label"]}
    ]
    assert inspector.get_check_constraints(table_name=parents) == [
        {"name": f"{parents}_qty_qty_check", "sqltext": "(qty > 0) AND (qty < 1000)"}
    ]
    assert inspector.get_foreign_keys(table_name=children) == [
        {
            "name": f"{children}_parent_id_id_fkey",
            "constrained_columns": ["parent_id"],
            "referred_schema": None,
            "referred_table": parents,
            "referred_columns": ["id"],
            "options": {},
        }
    ]
    assert inspector.get_foreign_keys(table_name=parents) == []
    assert inspector.get_check_constraints(table_name=children) == []


def test_indexes(engine, schema):
    indexes = {i["name"]: i for i in inspect(engine).get_indexes(table_name=f"{schema}_parents")}
    assert indexes[f"{schema}_ix_code"] == {
        "name": f"{schema}_ix_code",
        "unique": False,
        "column_names": ["code"],
        "dialect_options": {},
    }
    assert indexes[f"{schema}_ix_expr"] == {
        "name": f"{schema}_ix_expr",
        "unique": False,
        "column_names": [None, "qty"],
        "expressions": ["lower(code)", "qty"],
        "dialect_options": {},
    }


def test_comments_and_view_definition(engine, schema):
    inspector = inspect(engine)
    assert inspector.get_table_comment(table_name=f"{schema}_parents") == {"text": "the parents"}
    assert inspector.get_table_comment(table_name=f"{schema}_children") == {"text": None}
    assert inspector.get_table_comment(table_name=f"{schema}_view") == {"text": "a view"}
    definition = inspector.get_view_definition(view_name=f"{schema}_view")
    assert definition == f"CREATE VIEW {schema}_view AS SELECT id, code FROM {schema}_parents;"
    assert [c["name"] for c in inspector.get_columns(table_name=f"{schema}_view")] == ["id", "code"]


def test_a_missing_table_is_no_such_table_everywhere(engine):
    inspector = inspect(engine)
    for method in (
        inspector.get_columns,
        inspector.get_pk_constraint,
        inspector.get_foreign_keys,
        inspector.get_unique_constraints,
        inspector.get_check_constraints,
        inspector.get_indexes,
        inspector.get_table_comment,
    ):
        with pytest.raises(exc.NoSuchTableError, match="not_a_table"):
            method(table_name="not_a_table")
    with pytest.raises(exc.NoSuchTableError, match="not_a_view"):
        inspector.get_view_definition(view_name="not_a_view")


def test_reflect_whole_tables_and_create_them_elsewhere(engine, schema):
    reflected = MetaData()
    reflected.reflect(bind=engine, only=[f"{schema}_parents", f"{schema}_children"])
    children = reflected.tables[f"{schema}_children"]
    parents = reflected.tables[f"{schema}_parents"]
    assert [fk.column for fk in children.foreign_keys] == [parents.c.id]
    assert parents.c.id.primary_key and parents.c.id.autoincrement is True
    assert parents.comment == "the parents"


def test_other_schemas_and_attached_catalogs(engine, name):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE SCHEMA {name}_s"))
        conn.execute(text(f"CREATE TABLE {name}_s.t1 (id INTEGER PRIMARY KEY, v VARCHAR)"))
        conn.execute(text(f"CREATE SEQUENCE {name}_s.numbers"))
        conn.execute(text(f"ATTACH ':memory:' AS {name}_cat"))
        conn.execute(text(f"CREATE SCHEMA {name}_cat.sales"))
        conn.execute(text(f"CREATE TABLE {name}_cat.sales.orders (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(f"CREATE VIEW {name}_cat.sales.recent AS SELECT * FROM {name}_cat.sales.orders")
        )
    try:
        inspector = inspect(engine)
        schemas = inspector.get_schema_names()
        assert schemas[0:1] == ["main"] or "main" in schemas
        assert f"{name}_s" in schemas  # a schema of the current catalog: by its own name
        assert f"{name}_cat.sales" in schemas  # another catalog's: as catalog.schema
        assert f"{name}_cat.main" in schemas
        assert not any(s.startswith(("system.", "temp.")) for s in schemas)
        assert inspector.has_schema(schema_name=f"{name}_s")
        assert inspector.has_schema(schema_name=f"{name}_cat.sales")
        assert not inspector.has_schema(schema_name=f"{name}_cat.nope")

        assert inspector.get_table_names(schema=f"{name}_s") == ["t1"]
        assert inspector.get_sequence_names(schema=f"{name}_s") == ["numbers"]
        assert inspector.has_sequence(sequence_name="numbers", schema=f"{name}_s")
        assert inspector.get_table_names(schema=f"{name}_cat.sales") == ["orders"]
        assert inspector.get_view_names(schema=f"{name}_cat.sales") == ["recent"]
        assert inspector.has_table(table_name="orders", schema=f"{name}_cat.sales")
        assert not inspector.has_table(table_name="orders")  # not in the current schema
        assert [
            c["name"]
            for c in inspector.get_columns(table_name="orders", schema=f"{name}_cat.sales")
        ] == ["id"]
        assert inspector.get_pk_constraint(table_name="orders", schema=f"{name}_cat.sales")[
            "constrained_columns"
        ] == ["id"]

        # ... and a Table with schema="catalog.schema" reads and writes three-part names
        orders = Table("orders", MetaData(), autoload_with=engine, schema=f"{name}_cat.sales")
        with engine.begin() as conn:
            conn.execute(orders.insert().values(id=7))
            assert conn.execute(orders.select()).all() == [(7,)]
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DETACH {name}_cat"))
            conn.execute(text(f"DROP SCHEMA {name}_s CASCADE"))


def test_temporary_tables_and_views(engine, name):
    metadata = MetaData()
    temp = Table(
        f"{name}_tmp",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("v", String, unique=True),
        prefixes=["TEMPORARY"],
    )
    with engine.connect() as conn:
        temp.create(bind=conn)
        conn.execute(text(f"CREATE TEMPORARY VIEW {name}_tmp_view AS SELECT 1 AS one"))
        inserted = conn.execute(temp.insert().values(v="a"))
        assert inserted.inserted_primary_key == (1,)
        inspector = inspect(conn)
        assert f"{name}_tmp" in inspector.get_temp_table_names()
        assert f"{name}_tmp_view" in inspector.get_temp_view_names()
        assert f"{name}_tmp" not in inspector.get_table_names()
        assert inspector.has_table(table_name=f"{name}_tmp")
        assert [c["name"] for c in inspector.get_columns(table_name=f"{name}_tmp")] == ["id", "v"]
        assert inspector.get_unique_constraints(table_name=f"{name}_tmp") == [
            {"name": f"{name}_tmp_v_key", "column_names": ["v"]}
        ]
        # its sequence is temporary too: not among the schema's sequences
        assert f"{name}_tmp_id_seq" not in inspector.get_sequence_names()
        conn.rollback()


def test_an_unknown_type_warns_and_reflects_as_null_type(engine, name, monkeypatch):
    from sqlalchemy import types as sqltypes

    from sqlalchemy_hardcharger import types as hctypes

    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {name} (a INTEGER, b BIGINT)"))
    try:
        known = dict(hctypes.ISCHEMA_NAMES)
        del known["BIGINT"]
        monkeypatch.setattr(hctypes, "ISCHEMA_NAMES", known)
        with pytest.warns(exc.SAWarning, match="Did not recognize type 'BIGINT' of column 'b'"):
            columns = inspect(engine).get_columns(table_name=name)
        assert isinstance(columns[0]["type"], sqltypes.INTEGER)
        assert isinstance(columns[1]["type"], sqltypes.NullType)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {name}"))


def test_reserved_words_match_the_server(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT keyword_name FROM duckdb_keywords() "
                "WHERE keyword_category IN ('reserved', 'type_function')"
            )
        ).scalars()
        assert set(rows) == set(RESERVED_WORDS)


def test_every_keyword_works_as_a_column_and_a_table_name(engine, name):
    """Whatever DuckDB reserves, the dialect quotes: every keyword is usable as a name."""
    with engine.connect() as conn:
        keywords = (
            conn.execute(text("SELECT DISTINCT keyword_name FROM duckdb_keywords() ORDER BY 1"))
            .scalars()
            .all()
        )
    assert len(keywords) > 400
    metadata = MetaData()
    wide = Table(f"{name}_wide", metadata, *[Column(keyword, Integer) for keyword in keywords])
    as_tables = [
        Table(keyword, metadata, Column("a", Integer), schema=f"{name}_kw") for keyword in keywords
    ]
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA {name}_kw"))
        metadata.create_all(bind=conn)
        conn.execute(wide.insert().values({keyword: 1 for keyword in keywords}))
        row = conn.execute(wide.select().where(wide.c["select"] == 1).order_by(wide.c["at"])).one()
        assert set(row) == {1}
        for table in as_tables:
            conn.execute(table.insert().values(a=1))
            assert conn.execute(table.select()).scalar() == 1
        conn.rollback()
