"""An Alembic migration run for real: upgrade, autogenerate comparison, downgrade."""

import textwrap

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Column, Integer, MetaData, String, Table, inspect

REVISION = '''
    """create accounts"""
    import sqlalchemy as sa
    from alembic import op

    revision = "0001"
    down_revision = None

    def upgrade():
        op.create_table(
            "{prefix}_accounts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("email", sa.String(120), nullable=False),
        )
        active = sa.Column("active", sa.Boolean, server_default=sa.true())
        op.add_column("{prefix}_accounts", active)
        op.execute("INSERT INTO {prefix}_accounts (email) VALUES ('a@example.com')")
        # (DuckDB alters a table only while no index depends on it: rename first, index after)
        op.alter_column("{prefix}_accounts", "email", new_column_name="mail")
        op.create_index("{prefix}_ix_email", "{prefix}_accounts", ["mail"], unique=True)

    def downgrade():
        op.drop_index("{prefix}_ix_email", table_name="{prefix}_accounts")
        op.drop_table("{prefix}_accounts")
'''

ENV = """
    from alembic import context
    from sqlalchemy import create_engine

    engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table=context.config.get_main_option("version_table"),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
"""


def test_upgrade_and_downgrade(engine, url, name, tmp_path):
    script_dir = tmp_path / "migrations"
    (script_dir / "versions").mkdir(parents=True)
    (script_dir / "env.py").write_text(textwrap.dedent(ENV))
    (script_dir / "script.py.mako").write_text("")
    (script_dir / "versions" / "0001_create_accounts.py").write_text(
        textwrap.dedent(REVISION).format(prefix=name)
    )
    config = Config()
    config.set_main_option("script_location", str(script_dir))
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    config.set_main_option("version_table", f"{name}_alembic_version")

    command.upgrade(config=config, revision="head")
    inspector = inspect(engine)
    assert inspector.has_table(table_name=f"{name}_accounts")
    columns = [c["name"] for c in inspector.get_columns(table_name=f"{name}_accounts")]
    assert columns == ["id", "mail", "active"]
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(f"SELECT id, mail, active FROM {name}_accounts").all()
        version = conn.exec_driver_sql(f"SELECT version_num FROM {name}_alembic_version").scalar()
    assert rows == [(1, "a@example.com", True)]  # the key came from the table's sequence
    assert version == "0001"

    # autogenerate sees a column the database doesn't have yet
    target = MetaData()
    Table(
        f"{name}_accounts",
        target,
        Column("id", Integer, primary_key=True),
        Column("mail", String(120), nullable=False),
        Column("active", Integer),
        Column("nickname", String),
    )
    with engine.connect() as conn:
        context = MigrationContext.configure(
            connection=conn,
            opts={
                "include_name": lambda n, type_, parent: type_ != "table" or n == f"{name}_accounts"
            },
        )
        differences = compare_metadata(context=context, metadata=target)
    added = [d for d in differences if d[0] == "add_column"]
    assert [d[3].name for d in added] == ["nickname"]

    command.downgrade(config=config, revision="base")
    inspector = inspect(engine)
    assert not inspector.has_table(table_name=f"{name}_accounts")
    assert f"{name}_accounts_id_seq" not in inspector.get_sequence_names()
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name}_alembic_version")


def _compare(engine, target, table_name):
    with engine.connect() as conn:
        context = MigrationContext.configure(
            connection=conn,
            opts={
                "compare_type": True,
                "include_name": lambda n, type_, parent: type_ != "table" or n == table_name,
            },
        )
        return compare_metadata(context=context, metadata=target)


def test_autogenerate_finds_nothing_to_do_right_after_create_all(engine, name):
    """Types and constraints read back from DuckDB in another spelling (TEXT is VARCHAR,
    Float(24) is REAL, a named enum is its labels, constraint names are DuckDB's own) must not
    be reported as changes."""
    import sqlalchemy as sa

    target = MetaData()
    Table(
        name,
        target,
        Column("id", Integer, primary_key=True),
        Column("title", String(120), nullable=False),
        Column("body", sa.Text),
        Column("ratio", sa.Float(24)),
        Column("score", sa.Float),
        Column("price", sa.Numeric(10, 2)),
        Column("amount", sa.Numeric),
        Column("mood", sa.Enum("sad", "ok", "happy", name=f"{name}_mood")),
        Column("code", String, unique=True),
        sa.UniqueConstraint("title", "body", name="declared_name"),
    )
    target.create_all(bind=engine)
    try:
        assert _compare(engine=engine, target=target, table_name=name) == []

        # ... and a real change is still seen
        changed = MetaData()
        Table(
            name,
            changed,
            Column("id", Integer, primary_key=True),
            Column("title", String(120), nullable=False),
            Column("body", sa.Text),
            Column("ratio", sa.Float(24)),
            Column("score", sa.Float),
            Column("price", sa.Numeric(12, 4)),
            Column("amount", sa.Numeric),
            Column("mood", sa.Enum("sad", "ok", "happy", "ecstatic", name=f"{name}_mood")),
            Column("code", String, unique=True),
            sa.UniqueConstraint("title", "body", name="declared_name"),
        )
        differences = _compare(engine=engine, target=changed, table_name=name)
        modified = sorted(d[0][3] for d in differences if d[0][0] == "modify_type")
        assert modified == ["mood", "price"]
    finally:
        target.drop_all(bind=engine)


def test_column_comments_rename_and_the_keys_sequence(engine, url, name, tmp_path):
    revision = '''
        """accounts"""
        import sqlalchemy as sa
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade():
            op.create_table(
                "{prefix}_acct",
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("email", sa.String),
            )
            op.execute("INSERT INTO {prefix}_acct (email) VALUES ('a'), ('b'), ('c')")
            op.alter_column(
                "{prefix}_acct", "email", comment="it's the login", existing_type=sa.String
            )
            op.rename_table("{prefix}_acct", "{prefix}_accounts")
            op.execute("INSERT INTO {prefix}_accounts (email) VALUES ('d')")
            op.drop_table("{prefix}_accounts")
            # a new table of the first name starts numbering afresh
            op.create_table(
                "{prefix}_acct",
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("v", sa.String),
            )
            op.execute("INSERT INTO {prefix}_acct (v) VALUES ('x')")

        def downgrade():
            op.drop_table("{prefix}_acct")
    '''
    script_dir = tmp_path / "migrations"
    (script_dir / "versions").mkdir(parents=True)
    (script_dir / "env.py").write_text(textwrap.dedent(ENV))
    (script_dir / "script.py.mako").write_text("")
    (script_dir / "versions" / "0001_accounts.py").write_text(
        textwrap.dedent(revision).format(prefix=name)
    )
    config = Config()
    config.set_main_option("script_location", str(script_dir))
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    config.set_main_option("version_table", f"{name}_alembic_version")

    command.upgrade(config=config, revision="head")
    inspector = inspect(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql(f"SELECT id, v FROM {name}_acct").all() == [(1, "x")]
    assert inspector.get_sequence_names().count(f"{name}_acct_id_seq") == 1
    command.downgrade(config=config, revision="base")
    assert f"{name}_acct_id_seq" not in inspect(engine).get_sequence_names()
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name}_alembic_version")


def test_dropping_a_table_leaves_a_sequence_another_table_uses(engine, name):
    from alembic.operations import Operations

    with engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE SEQUENCE {name}_shared")
        conn.exec_driver_sql(
            f"COMMENT ON SEQUENCE {name}_shared IS 'sqlalchemy-hardcharger: numbers a.id'"
        )
        conn.exec_driver_sql(f"CREATE TABLE {name}_a (id INTEGER DEFAULT nextval('{name}_shared'))")
        conn.exec_driver_sql(f"CREATE TABLE {name}_b (id INTEGER DEFAULT nextval('{name}_shared'))")
    try:
        with engine.begin() as conn:
            Operations(MigrationContext.configure(connection=conn)).drop_table(f"{name}_a")
        assert f"{name}_shared" in inspect(engine).get_sequence_names()  # {name}_b still needs it
        with engine.begin() as conn:
            Operations(MigrationContext.configure(connection=conn)).drop_table(f"{name}_b")
        assert f"{name}_shared" not in inspect(engine).get_sequence_names()
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name}_a")
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name}_b")
            conn.exec_driver_sql(f"DROP SEQUENCE IF EXISTS {name}_shared")
