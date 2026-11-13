"""SQL the dialect generates, run for real."""

import pytest
from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    exc,
    func,
    select,
    text,
    try_cast,
)

from sqlalchemy_hardcharger import insert


@pytest.fixture
def t(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("grp", String),
        Column("n", Integer),
        Column("doc", JSON),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {"id": 1, "grp": "a", "n": 10, "doc": {"k": {"deep": [1, 2, 3]}, "s": "x", "i": 7}},
                {"id": 2, "grp": "a", "n": 20, "doc": {"k": {"deep": [4]}, "s": "y", "i": 8}},
                {"id": 3, "grp": "b", "n": 5, "doc": None},
            ],
        )
    return table


def test_returning(engine, t):
    with engine.begin() as conn:
        inserted = conn.execute(
            t.insert().values(id=4, grp="c", n=1).returning(t.c.id, t.c.grp)
        ).all()
        assert inserted == [(4, "c")]
        updated = conn.execute(
            t.update().where(t.c.grp == "a").values(n=t.c.n + 1).returning(t.c.id, t.c.n)
        ).all()
        assert sorted(updated) == [(1, 11), (2, 21)]
        deleted = conn.execute(t.delete().where(t.c.id == 3).returning(t.c.id)).scalars().all()
        assert deleted == [3]


def test_rowcounts(engine, t):
    with engine.begin() as conn:
        assert conn.execute(t.update().where(t.c.grp == "a").values(n=0)).rowcount == 2
        assert conn.execute(t.update().where(t.c.grp == "a").values(n=0)).rowcount == 2  # matched
        assert conn.execute(t.delete().where(t.c.id == 999)).rowcount == 0
        many = conn.execute(
            t.update().where(t.c.id == func.cast(text(":key"), Integer)).values(n=1),
            [{"key": 1}, {"key": 2}, {"key": 999}],
        )
        assert many.rowcount == 2


def test_upsert_returns_the_stored_row(engine, t):
    statement = insert(t).values(id=1, grp="z", n=99)
    with engine.begin() as conn:
        row = conn.execute(
            statement.on_conflict_do_update(
                index_elements=[t.c.id], set_={"n": statement.excluded.n}
            ).returning(t.c.id, t.c.grp, t.c.n)
        ).one()
    assert row == (1, "a", 99)  # n replaced, grp kept


def test_json_keys_paths_and_typed_access(engine, t):
    with engine.connect() as conn:
        assert conn.execute(select(t.c.doc["s"].as_string()).where(t.c.id == 1)).scalar() == "x"
        assert conn.execute(select(t.c.doc["i"].as_integer()).where(t.c.id == 2)).scalar() == 8
        assert (
            conn.execute(select(t.c.doc[("k", "deep", 1)].as_integer()).where(t.c.id == 1)).scalar()
            == 2
        )
        assert conn.execute(select(t.c.doc[("k", "deep")]).where(t.c.id == 1)).scalar() == [1, 2, 3]
        found = conn.execute(select(t.c.id).where(t.c.doc["i"].as_integer() > 7)).scalars().all()
        assert found == [2]


def test_window_functions_with_a_range_frame(engine, t):
    total = func.sum(t.c.n).over(order_by=t.c.n, range_=(None, 0))
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.id, total).order_by(t.c.n)).all()
    assert rows == [(3, 5), (1, 15), (2, 35)]


def test_cte_union_and_distinct_on(engine, t):
    totals = select(t.c.grp, func.sum(t.c.n).label("total")).group_by(t.c.grp).cte("totals")
    with engine.connect() as conn:
        assert conn.execute(select(totals).order_by(totals.c.grp)).all() == [("a", 30), ("b", 5)]
        latest = select(t.c.grp, t.c.id).order_by(t.c.grp, t.c.n.desc())
        try:  # SQLAlchemy 2.1 spells DISTINCT ON as an extension, 2.0 as distinct(column)
            from sqlalchemy.dialects.postgresql import distinct_on

            latest = latest.ext(distinct_on(t.c.grp))
        except ImportError:
            latest = latest.distinct(t.c.grp)
        first_per_group = conn.execute(latest).all()
        assert first_per_group == [("a", 2), ("b", 3)]
        both = select(t.c.id).where(t.c.id == 1).union_all(select(t.c.id).where(t.c.id == 3))
        assert sorted(conn.execute(both).scalars()) == [1, 3]


def test_operators_that_differ_from_postgresql(engine, t):
    with engine.connect() as conn:
        row = conn.execute(
            select(
                t.c.n / 3,
                t.c.n // 3,
                t.c.n % 3,
                t.c.n.bitwise_xor(3),
                t.c.grp.regexp_match("^A", flags="i"),
                t.c.grp.regexp_replace("a", "o"),
                try_cast(t.c.grp, Integer),
            ).where(t.c.id == 1)
        ).one()
    # (integer / integer is a Numeric to SQLAlchemy, so the quotient arrives as a Decimal)
    assert float(row[0]) == pytest.approx(10 / 3)
    assert tuple(row[1:]) == (3, 1, 9, True, "o", None)


def test_select_for_update_runs_as_a_plain_select(engine, t):
    with engine.connect() as conn:
        assert conn.execute(select(t.c.id).where(t.c.id == 1).with_for_update()).scalar() == 1


def test_limit_offset_and_null_ordering(engine, t):
    with engine.connect() as conn:
        assert conn.execute(select(t.c.id).order_by(t.c.id).limit(1).offset(1)).scalars().all() == [
            2
        ]
        assert conn.execute(select(t.c.id).order_by(t.c.id).offset(2)).scalars().all() == [3]
        ordered = (
            conn.execute(select(t.c.id).order_by(t.c.doc["i"].as_integer().desc().nulls_first()))
            .scalars()
            .all()
        )
        assert ordered == [3, 2, 1]


def test_executemany_of_a_statement_without_parameters(engine, metadata, name):
    table = Table(f"{name}_d", metadata, Column("a", Integer, server_default=text("7")))
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        result = conn.execute(table.insert(), [{}, {}, {}])
        assert result.rowcount == 3
        assert conn.execute(select(table.c.a)).scalars().all() == [7, 7, 7]


def test_create_table_as_and_insert_from_select(engine, t, name):
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {name}_copy AS SELECT id, n FROM {name} WHERE grp = 'a'"))
        try:
            copy = Table(f"{name}_copy", MetaData(), autoload_with=conn)
            conn.execute(
                copy.insert().from_select(["id", "n"], select(t.c.id, t.c.n).where(t.c.grp == "b"))
            )
            assert sorted(conn.execute(select(copy.c.id)).scalars()) == [1, 2, 3]
        finally:
            conn.execute(text(f"DROP TABLE {name}_copy"))


# -- DuckDB limits the README states, pinned here so the README stays true ----------------------


def test_foreign_keys_cannot_cascade(engine, metadata, name):
    Table(f"{name}_p", metadata, Column("id", Integer, primary_key=True))
    Table(
        f"{name}_c",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("p_id", Integer, ForeignKey(f"{name}_p.id", ondelete="CASCADE")),
    )
    with pytest.raises(exc.DBAPIError, match="FOREIGN KEY constraints cannot use CASCADE"):
        metadata.create_all(bind=engine)


def test_a_row_another_row_points_at_keeps_its_key_columns(engine, metadata, name):
    """A table that references itself: a referenced row's key and reference columns can't be
    updated, and it can't be deleted in the same statement as the rows that point at it."""
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("parent_id", Integer, ForeignKey(f"{name}.id")),
        Column("v", String),
    )
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(id=1, parent_id=None, v="root"))
        conn.execute(table.insert().values(id=2, parent_id=1, v="leaf"))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(table.update().where(table.c.id == 1).values(v="other columns are fine"))
        with pytest.raises(exc.IntegrityError, match="still referenced by a foreign key"):
            conn.execute(table.update().where(table.c.id == 1).values(parent_id=None))
        with pytest.raises(exc.IntegrityError, match="still referenced by a foreign key"):
            conn.execute(table.delete())
        conn.execute(table.delete().where(table.c.id == 2))  # the leaf first ...
        conn.execute(table.delete())  # ... then the root
        assert conn.execute(select(func.count()).select_from(table)).scalar() == 0


def test_a_table_with_an_index_takes_few_alterations(engine, metadata, name):
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("a", String),
        Column("b", Integer),
    )
    metadata.create_all(bind=engine)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE INDEX {name}_ix ON {name} (a)"))
        conn.execute(text(f"ALTER TABLE {name} ADD COLUMN c INTEGER"))  # adding a column works
        for alteration in ("RENAME b TO b2", "ALTER COLUMN b TYPE BIGINT", "DROP COLUMN c"):
            with pytest.raises(exc.DBAPIError, match="because there are entries that depend on it"):
                conn.execute(text(f"ALTER TABLE {name} {alteration}"))
        conn.execute(text(f"DROP INDEX {name}_ix"))  # without the index they all go through
        conn.execute(text(f"ALTER TABLE {name} RENAME b TO b2"))
        conn.execute(text(f"ALTER TABLE {name} ALTER COLUMN b2 TYPE BIGINT"))
        conn.execute(text(f"ALTER TABLE {name} DROP COLUMN c"))
    del table


def test_constraints_cannot_be_added_to_an_existing_table(engine, metadata, name):
    Table(f"{name}_p", metadata, Column("id", Integer, primary_key=True))
    Table(f"{name}_c", metadata, Column("id", Integer, primary_key=True), Column("p_id", Integer))
    metadata.create_all(bind=engine)
    with engine.connect() as conn:
        with pytest.raises(exc.DBAPIError, match="No support for adding FOREIGN_KEY constraints"):
            conn.execute(
                text(
                    f"ALTER TABLE {name}_c ADD CONSTRAINT fk "
                    f"FOREIGN KEY (p_id) REFERENCES {name}_p (id)"
                )
            )


def test_serial_and_identity_do_not_exist_in_duckdb(engine, name):
    with engine.connect() as conn:
        with pytest.raises(exc.DBAPIError, match="Type with name SERIAL does not exist"):
            conn.execute(text(f"CREATE TABLE {name} (id SERIAL PRIMARY KEY)"))
        conn.rollback()
        with pytest.raises(exc.DBAPIError, match='syntax error at or near "IDENTITY"'):
            conn.execute(text(f"CREATE TABLE {name} (id INTEGER GENERATED ALWAYS AS IDENTITY)"))
