<p align="center">
  <a href="https://github.com/gizmodata/hardcharger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gizmodata/sqlalchemy-hardcharger/main/docs/assets/hardcharger-banner-dark.png">
      <img src="https://raw.githubusercontent.com/gizmodata/sqlalchemy-hardcharger/main/docs/assets/hardcharger-banner-light.png" alt="Hardcharger™" width="400">
    </picture>
  </a>
</p>

<h1 align="center">Hardcharger™ SQLAlchemy Dialect</h1>

<p align="center">
  <a href="https://github.com/gizmodata/sqlalchemy-hardcharger/actions/workflows/ci.yml"><img src="https://github.com/gizmodata/sqlalchemy-hardcharger/actions/workflows/ci.yml/badge.svg" alt="CI: full suite on Python 3.11–3.15"></a>
  <a href="https://pypi.org/project/sqlalchemy-hardcharger/"><img src="https://img.shields.io/pypi/v/sqlalchemy-hardcharger?logo=pypi" alt="PyPI version"></a>
  <a href="https://pypi.org/project/sqlalchemy-hardcharger/"><img src="https://img.shields.io/badge/Python-3.10–3.15-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10–3.15"></a>
  <a href="https://pypi.org/project/sqlalchemy-hardcharger/"><img src="https://img.shields.io/pepy/dt/sqlalchemy-hardcharger.svg" alt="PyPI downloads"></a>
  <a href="https://www.sqlalchemy.org"><img src="https://img.shields.io/badge/SQLAlchemy-2.0%20%7C%202.1-D71F00.svg?logo=sqlalchemy&amp;logoColor=white" alt="SQLAlchemy 2.0 and 2.1"></a>
  <a href="https://github.com/gizmodata/hardcharger-adbc"><img src="https://img.shields.io/badge/driver-Hardcharger%20ADBC-f15a24.svg" alt="Hardcharger ADBC driver"></a>
  <a href="https://github.com/gizmodata/chargewire"><img src="https://img.shields.io/badge/protocol-Chargewire-31a8db.svg" alt="Chargewire protocol"></a>
  <a href="https://github.com/gizmodata/hardcharger"><img src="https://img.shields.io/badge/server-Hardcharger-f15a24.svg" alt="Hardcharger"></a>
  <a href="https://github.com/gizmodata/sqlalchemy-hardcharger/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-brightgreen.svg" alt="Apache 2.0 license"></a>
  <a href="https://gizmodata.com"><img src="https://img.shields.io/badge/by-GizmoData%E2%84%A2-f15a24.svg" alt="GizmoData™"></a>
</p>

<h3 align="center">SQLAlchemy Core, the ORM, pandas and Alembic on Hardcharger: remote DuckDB 2.0, over Chargewire.</h3>

---

`sqlalchemy-hardcharger` registers the `hardcharger://` URL scheme with
[SQLAlchemy](https://www.sqlalchemy.org), so anything that takes a SQLAlchemy engine works against a
[Hardcharger™](https://gizmodata.com/hardcharger) server, GizmoData's DuckDB 2.0 server. It runs on
the native [Hardcharger ADBC driver](https://github.com/gizmodata/hardcharger-adbc) over
**Chargewire HTTP(S)**, generates DuckDB SQL, and reflects schemas through DuckDB's own catalog.

## Install

```bash
pip install sqlalchemy-hardcharger
```

Python **3.10 or later**, SQLAlchemy **2.0 or 2.1** (SQLAlchemy 2.1 itself needs Python 3.11, so
Python 3.10 gets 2.0). Installs `adbc-driver-hardcharger>=2.5.0,<3`.

## Quick start

```python
from sqlalchemy import create_engine, text

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")

with engine.connect() as conn:
    print(conn.execute(text("SELECT 42 AS answer")).scalar())
```

```text
42
```

TLS is **on** unless the URL says `?tls=false` (a local development server). The port defaults to
31337, and to 443 for a [GizmoData Cloud](https://gizmodata.com/cloud) host.

## The ORM

A plain integer primary key numbers itself, as it does on PostgreSQL or SQLite:

```python
from sqlalchemy import ForeignKey, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    books: Mapped[list["Book"]] = relationship(back_populates="author")


class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    title: Mapped[str]
    author: Mapped[Author] = relationship(back_populates="books")


Base.metadata.create_all(bind=engine)

with Session(engine) as session:
    ada = Author(name="Ada", books=[Book(title="Notes"), Book(title="Sketch")])
    session.add(ada)
    session.commit()
    print(ada.id, [book.id for book in ada.books])

    counts = select(Author.name, func.count(Book.id)).join(Book).group_by(Author.name)
    print(session.execute(counts).all())
```

```text
1 [1, 2]
[('Ada', 2)]
```

DuckDB has neither `SERIAL` nor identity columns, so the dialect gives such a key a sequence:
`CREATE TABLE authors` is preceded by `CREATE SEQUENCE IF NOT EXISTS authors_id_seq`, the column
gets `DEFAULT nextval('authors_id_seq')`, and dropping the table drops the sequence. A column
declared with `Identity(start=…, increment=…)` gets one too, key or not, with those options. An
explicit `Sequence("name")` is used as given.

## Core: upserts and RETURNING

```python
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

from sqlalchemy_hardcharger import insert

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")
metadata = MetaData()
stock = Table(
    "stock",
    metadata,
    Column("sku", String, primary_key=True),
    Column("qty", Integer, nullable=False),
)
metadata.create_all(bind=engine)

with engine.begin() as conn:
    conn.execute(stock.insert(), [{"sku": "bolt", "qty": 10}, {"sku": "nut", "qty": 4}])

    upsert = insert(stock).values(sku="bolt", qty=25)
    upsert = upsert.on_conflict_do_update(
        index_elements=[stock.c.sku], set_={"qty": upsert.excluded.qty}
    )
    print(conn.execute(upsert.returning(stock.c.sku, stock.c.qty)).all())

    sold = stock.update().where(stock.c.sku == "nut").values(qty=stock.c.qty - 1)
    print(conn.execute(sold.returning(stock.c.qty)).scalar())
```

```text
[('bolt', 25)]
3
```

`sqlalchemy_hardcharger.insert` is SQLAlchemy's PostgreSQL `insert()`: `on_conflict_do_update()` and
`on_conflict_do_nothing()` compile to DuckDB's `ON CONFLICT`.

## pandas

```python
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")

frame = pd.DataFrame({"city": ["Oslo", "Lima", "Pune"], "temp_c": [4.5, 19.0, 31.5]})
frame.to_sql(name="weather", con=engine, index=False, if_exists="replace")

warm = pd.read_sql(
    sql="SELECT city, temp_c FROM weather WHERE temp_c > 10 ORDER BY city", con=engine
)
print(warm.to_dict(orient="records"))
```

```text
[{'city': 'Lima', 'temp_c': 19.0}, {'city': 'Pune', 'temp_c': 31.5}]
```

For loading millions of rows, use the driver's Arrow bulk ingest (see
[The driver underneath](#the-driver-underneath)): it sends Arrow batches in parallel instead of
`INSERT` statements.

## The URL

```
hardcharger://<user>:<password>@<host>[:<port>][/<catalog>][?option=value&...]
```

| Part or option | Default | Meaning |
| --- | --- | --- |
| `host`, `port` | port 31337 (443 on GizmoData Cloud) | The server |
| `user`, `password` | | Password sign-in |
| `/catalog` (or `catalog=`) | the server's default | Catalog to make current |
| `schema=` | `main` | Schema to make current |
| `tls=` | `true` | `false`: plain HTTP, for a local development server |
| `tls_skip_verify=` | `false` | `true`: don't verify the server's certificate (development only) |
| `tls_root_certs=` | system trust store | CA certificates to trust: a PEM file path |
| `tls_client_cert=`, `tls_client_key=` | | Client certificate and key for mutual TLS (PEM file paths) |
| `tls_server_name=` | the host | Name to check the certificate against, when connecting by IP or through a tunnel |
| `auth_type=` | `password` | `external`: sign in through a browser (SSO). `client_certificate`: sign in with the TLS client certificate |
| `access_token=` | | A Hardcharger access token, in place of a user and password (see the note on tokens below) |
| `subject_token=` | | An identity provider's token, exchanged for an access token |
| `oauth_timeout=` | `300` | Seconds to wait for the browser sign-in |
| `open_browser=` | `true` | `false`: print the sign-in URL instead of opening a browser |
| `share_sign_in=` | per engine, with `auth_type=external` | `false`: one browser sign-in per connection. A name: share the sign-in with other engines given the same name |
| `resume_timeout=` | `600` | Seconds to wait for a stopped GizmoData Cloud cluster to start |
| `session_tag=`, `query_tag=` | | JSON objects recorded with the session and its statements |
| `hardcharger.<name>=` | | A session setting (a name without a dot), e.g. `hardcharger.query_timeout=60` |
| `hardcharger.query.async`, `hardcharger.query.retain_result`, `hardcharger.query.timeout_seconds`, `hardcharger.query.result_partitions`, `hardcharger.query.result_order`, `hardcharger.ipc.compression`, `hardcharger.ingest.parts`, `hardcharger.retry.max_attempts`, `hardcharger.session.reconnect` | | Driver options that tune statements, by these exact names. Their names from before adbc-driver-hardcharger 2.7.0 (`adbc.chargewire.statement.result_partitions`, ...) still work, with a `DeprecationWarning` |

An option the dialect doesn't know is an error, never ignored. Everything `connect()` of the driver
takes can be passed as `create_engine(url, connect_args={...})`: see the
[driver's Python documentation](https://docs.hardcharger.gizmodata.com/clients/python/).

A URL is often configuration that someone other than the application's author supplies, so a URL
can only choose among the options above: it can't name a program to run (the driver's browser
command), adopt another session, or set a driver option outside that list, under any spelling
(`hardcharger.*`, `chargewire.*` or the older `adbc.*`). Those are for `connect_args`, in the
application's own code.

**Tokens.** SQLAlchemy hides a URL's password when it prints an engine or a URL, and nothing else:
an `access_token=` or `subject_token=` in the URL shows in `repr(engine)` and in logs that print
the URL. Pass a token in code instead:

<!-- not-run: needs a token from a running server -->
```python
from sqlalchemy import create_engine

engine = create_engine("hardcharger://db.example.com", connect_args={"access_token": token})
```

Sign in through your identity provider. The browser opens once per engine: the engine's pooled
connections share the sign-in, and another engine in the same process (another user of the same
application) gets its own.

<!-- not-run: needs a server with single sign-on and a browser -->
```python
from sqlalchemy import create_engine

engine = create_engine("hardcharger://db.example.com?auth_type=external")
```

## Catalogs, schemas and reflection

DuckDB names a table `catalog.schema.table`. Wherever SQLAlchemy takes a `schema`, give either a
schema of the current catalog (`"sales"`) or a catalog and schema (`"lake.sales"`):

```python
from sqlalchemy import MetaData, Table, create_engine, inspect, text

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")

with engine.begin() as conn:
    conn.execute(text("ATTACH ':memory:' AS lake"))
    conn.execute(text("CREATE SCHEMA lake.sales"))
    conn.execute(
        text("CREATE TABLE lake.sales.orders (id INTEGER PRIMARY KEY, total DECIMAL(10,2))")
    )

inspector = inspect(engine)
print([name for name in inspector.get_schema_names() if name.startswith("lake")])
print(inspector.get_table_names(schema="lake.sales"))

orders = Table("orders", MetaData(), autoload_with=engine, schema="lake.sales")
print([(column.name, str(column.type)) for column in orders.columns])
```

```text
['lake.main', 'lake.sales']
['orders']
[('id', 'INTEGER'), ('total', 'DECIMAL(10, 2)')]
```

`get_schema_names()` lists the current catalog's schemas by name and every other attached catalog's
as `catalog.schema`. Reflection covers tables, views, temporary tables, columns (with defaults,
comments and generated columns), primary and foreign keys, unique and check constraints, indexes,
sequences and comments, read from `duckdb_tables()`, `duckdb_columns()`, `duckdb_constraints()` and
friends.

## Types

SQLAlchemy's generic types map to DuckDB's:

| SQLAlchemy | Hardcharger | Notes |
| --- | --- | --- |
| `Integer`, `BigInteger`, `SmallInteger` | `INTEGER`, `BIGINT`, `SMALLINT` | |
| `Float`, `Double` | `DOUBLE` | A Python `float` is 8 bytes, so `Float` is a `DOUBLE`; `Float(precision=24)`, `FLOAT` or `REAL` ask for 4 bytes |
| `Numeric(p, s)` | `DECIMAL(p, s)` | `Decimal` in Python. `Numeric()` with no precision is DuckDB's `DECIMAL(18, 3)` |
| `String`, `String(n)`, `Text` | `VARCHAR` | DuckDB keeps no length |
| `Boolean`, `Date`, `Time`, `DateTime` | `BOOLEAN`, `DATE`, `TIME`, `TIMESTAMP` | A time-zone-aware value given to `DateTime()` keeps its wall-clock time and loses its offset, as on PostgreSQL |
| `DateTime(timezone=True)` | `TIMESTAMP WITH TIME ZONE` | An aware value is that instant; a naive one is read in the session's time zone. Returned in the session's time zone |
| `Time(timezone=True)` | `TIME WITH TIME ZONE` | Keeps its offset |
| `Interval` | `INTERVAL` | `timedelta`; a month reads as 30 days |
| `Uuid` | `UUID` | `uuid.UUID`, or text with `as_uuid=False` |
| `LargeBinary` | `BLOB` | |
| `JSON` | `JSON` | Keys, paths and typed access: `col["a"]`, `col[("a", 0)].as_integer()`. A key is always taken literally, also one that starts with `$` or `/` |
| `Enum("a", "b")` | `ENUM('a', 'b')` | With `name=`, a type of its own (`CREATE TYPE`) |
| `ARRAY(Integer)` | `INTEGER[]` | A Python `list`. A fixed-size `INTEGER[3]` reflects as a list |

And DuckDB's own, from `sqlalchemy_hardcharger`: `TINYINT`, `UTINYINT`, `USMALLINT`, `UINTEGER`,
`UBIGINT`, `HUGEINT`, `UHUGEINT`, `BIGNUM`, `TIMESTAMP_S`, `TIMESTAMP_MS`, `TIMESTAMP_NS`, `TIME_NS`,
`BIT`, `VARIANT`, `GEOMETRY`, `Struct`, `Map` and `Union`.

`VARIANT` holds a value of any type in each row, and reads and writes Python values:

```python
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select

from sqlalchemy_hardcharger import VARIANT

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")
metadata = MetaData()
events = Table(
    "events", metadata, Column("id", Integer, primary_key=True), Column("payload", VARIANT)
)
metadata.create_all(bind=engine)

with engine.begin() as conn:
    conn.execute(
        events.insert(),
        [{"payload": 42}, {"payload": "text"}, {"payload": {"user": "ada", "tags": ["a", "b"]}}],
    )
    print(conn.execute(select(events.c.payload).order_by(events.c.id)).scalars().all())
```

```text
[42, 'text', {'tags': ['a', 'b'], 'user': 'ada'}]
```

(A VARIANT object's keys come back in sorted order.)

`Struct`, `Map` and `Union` columns read as Python values (`dict`, a list of pairs, the member's
value) and are written with SQL expressions such as `func.struct_pack(...)`. A VARIANT integer
past 64 bits comes back as a float (it travels as JSON); `HUGEINT`, `UHUGEINT` and `BIGNUM` columns
hold such integers exactly.

Integer results are Python `int`s, including `sum()` over an integer column (a `HUGEINT` in
DuckDB). Integer parameters are sent as `INTEGER`, so `func.round(column, 2)` and
`date_column + 5` work as they do with a literal.

## Transactions and the connection pool

- DuckDB transactions run under snapshot isolation: `isolation_level` is `"SNAPSHOT"` (the default)
  or `"AUTOCOMMIT"`. DDL is transactional.
- A transaction begins at the first statement, not when a connection is checked out, and a
  connection returned to the pool holds none open. A connection that ran nothing sends nothing.
- Two transactions that write the same rows conflict: the second gets an error and retries after a
  rollback. There are no row locks, so `SELECT ... FOR UPDATE` runs as a plain `SELECT`.
- There are no savepoints: `begin_nested()` raises, with that reason.
- The server ends a session that sits idle. The driver opens a new one for the next statement,
  with the URL's catalog and schema current again, so an idle pool keeps working. A session whose
  state was changed with `SET` or `USE` in SQL is not replaced silently: its next statement fails
  as a disconnect, and the pool opens a fresh connection.
- A session the server ended for good (an administrator's `KILL SESSION`, a revoked token) is
  treated as a disconnect: the pool discards the connection and opens another.
- Use the connection's `commit()` and `rollback()`. `BEGIN`, `COMMIT` and `ROLLBACK` sent as SQL
  text are followed (a raw `BEGIN` on an `AUTOCOMMIT` connection is rolled back when the
  connection returns to the pool), but only as whole statements of their own.
- On a GizmoData Cloud cluster that stops when idle, leave `pool_pre_ping` off: a ping is a
  statement, and a statement starts the cluster.

## Alembic

Migrations work with no extra setup: importing the dialect registers it with
[Alembic](https://alembic.sqlalchemy.org). Each migration runs in a transaction. Autogenerate
compares types as DuckDB stores them (`String(50)` and `Text` are both `VARCHAR`) and matches
unique constraints by their columns, since DuckDB names constraints itself. `op.drop_table()`
also drops the sequence that numbered the table's key. Operations DuckDB has no statement for
fail as they would by hand: dropping or adding a constraint on an existing table, and altering a
table that has an index (see below).

## Good to know about DuckDB

These are properties of DuckDB 2.0 that differ from PostgreSQL. Each is pinned by a test in
`tests/integration`, so this list stays true.

- **Foreign keys** can't `CASCADE`, `SET NULL` or `SET DEFAULT`, and can't be added to an existing
  table (`ALTER TABLE ... ADD CONSTRAINT`), so tables that reference each other in a cycle can't be
  created.
- **Deleting a parent row and its children** takes two transactions: delete and commit the children,
  then delete the parent. DuckDB checks the foreign key against rows deleted earlier in the same
  transaction. In a table that references itself, a row another row points at can't be deleted in
  the same statement as that row, and its key columns can't be updated.
- **Constraint names** are DuckDB's own (`<table>_<columns>_pkey`, `_key`, `_fkey`, `_check`): the
  name a constraint was declared with isn't kept, so reflection returns DuckDB's.
- **A table with an index** takes few alterations: adding a column works; renaming, retyping or
  dropping one needs the index dropped first.
- **Generated columns** are virtual (`Computed(..., persisted=True)` is refused).
- **One transaction writes to one catalog.** Writing to a second attached catalog in the same
  transaction fails; commit between them.
- **Bulk ORM inserts** that need server-generated keys back run one `INSERT ... RETURNING` per row,
  because DuckDB doesn't promise the order `RETURNING` gives a multi-row insert's rows in. Give the
  model a client-side key (`default=uuid.uuid4`) or use Core `insert()` with a list of rows where
  that matters.

## The driver underneath

`connection.connection.driver_connection` is the Hardcharger ADBC connection, with its Arrow
interface and Hardcharger helpers:

```python
import pyarrow as pa
from sqlalchemy import create_engine, text

engine = create_engine("hardcharger://hardcharger:change-me@localhost:31337?tls=false")

with engine.connect() as conn:
    driver_connection = conn.connection.driver_connection
    table = pa.table({"id": list(range(100_000)), "squared": [i * i for i in range(100_000)]})
    with driver_connection.cursor() as cur:
        cur.adbc_ingest(table_name="squares", data=table, mode="create")
        cur.execute(operation="SELECT max(squared) AS biggest FROM squares")
        print(cur.fetch_arrow_table().to_pydict())
    print(conn.execute(text("SELECT count(*) FROM squares")).scalar())
```

```text
{'biggest': [9999800001]}
100000
```

## Development and testing

```bash
uv venv && uv pip install -e ".[dev]"
make lint            # ruff and mypy
make test            # unit tests, and integration tests against a real Hardcharger server
make suite           # SQLAlchemy's own dialect compliance suite, against a real server
make readme          # runs every example in this README and compares its output
```

The `hardcharger` development dependency downloads the released server binary on first use and
runs it on a free port; `HARDCHARGER_SERVER_BINARY` names another build to test. A server is
required: the integration tests, the compliance suite and the README examples fail, never skip,
when it can't start.

CI runs all of it on Python 3.10–3.15 and on both SQLAlchemy series, once more with the lowest
dependency versions `pyproject.toml` allows, and publishes from version tags with PyPI Trusted
Publishing only after every job has passed on the tagged commit. See
[release setup](docs/releasing.md).

## License

Apache-2.0. Built on [SQLAlchemy](https://www.sqlalchemy.org) (MIT), whose PostgreSQL compilers the
dialect extends. Hardcharger and GizmoData are trademarks of GizmoData.
