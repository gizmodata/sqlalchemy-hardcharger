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
