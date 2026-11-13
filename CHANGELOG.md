# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-11-13

### Added

- **Python 3.10** is supported (was 3.11 or later), with SQLAlchemy 2.0: SQLAlchemy 2.1 itself
  needs Python 3.11. The Hardcharger ADBC driver installs on Python 3.10 from 2.5.0. CI runs every
  suite on Python 3.10, and the lowest-versions job now runs there.

### Changed

- Requires `adbc-driver-hardcharger` 2.5.0 or newer (was 2.4.9). That driver applies a catalog and
  a schema given together at connect in that order; earlier ones usually applied the schema first,
  in the server's default catalog. A URL with both (`hardcharger://host/lake?schema=reports`) now
  passes both to the driver, where 1.0.0 connected with the catalog and set the schema afterwards.
  The connection starts in the same place as before.

## [1.0.0] - 2026-11-13

### Added

- The `hardcharger://` SQLAlchemy dialect for Hardcharger™, GizmoData's DuckDB 2.0 server, on the
  native Hardcharger ADBC driver (`adbc-driver-hardcharger>=2.4.9`) over Chargewire HTTP(S).
  SQLAlchemy 2.0 and 2.1, Python 3.11 to 3.15.
- URLs: `hardcharger://user:password@host[:port][/catalog][?option=value]`. TLS is on unless
  `?tls=false`. Options cover the schema, TLS trust and client certificates, token and browser
  (SSO) sign-in, session and query tags, `hardcharger.<setting>` session settings and a few
  driver options that tune statements. An unknown option is refused, and a URL can't name a
  program to run or a session to adopt: those driver options are for `connect_args` only. A
  browser sign-in is shared among one engine's connections, never between engines.
- SQLAlchemy Core and the ORM: DuckDB SQL generation, `RETURNING`, `ON CONFLICT` upserts
  (`sqlalchemy_hardcharger.insert`), sequences, JSON keys and JSONPath access, regular
  expressions, `TRY_CAST`, window functions, CTEs.
- Auto-incrementing integer primary keys: DuckDB has neither `SERIAL` nor identity columns, so
  the table's key gets a sequence, created with the table and dropped with it. A column declared
  with `Identity()` gets one too, with its start and increment.
- Transactions that begin at the first statement, not at checkout, so a pooled connection holds
  none open on the server and an unused one sends nothing. Isolation levels `SNAPSHOT` and
  `AUTOCOMMIT`. A session the server ended for good is a disconnect, told by the driver's
  status code: the pool replaces the connection. `BEGIN`, `COMMIT` and `ROLLBACK` sent as SQL
  text are followed, so a raw `BEGIN` never stays open in the pool.
- Reflection through DuckDB's catalog functions: schemas (including other attached catalogs, as
  `catalog.schema`), tables, views, temporary tables, columns with defaults, comments and
  generated columns, primary and foreign keys, unique and check constraints, indexes (including
  expression indexes), sequences and comments.
- Types: SQLAlchemy's generic types, plus `TINYINT`, the unsigned integers, `HUGEINT`,
  `UHUGEINT`, `BIGNUM`, `TIMESTAMP_S/_MS/_NS`, `TIME_NS`, `BIT`, `VARIANT`, `GEOMETRY`, `Struct`,
  `Map` and `Union`. `VARIANT` columns read and write Python values. Generic `Float` is a
  `DOUBLE`, so a Python `float` comes back as it was stored. Integer parameters are sent as
  `INTEGER` and integer aggregates come back as `int`. A stored datetime doesn't depend on the
  other values in its batch: `DateTime()` keeps the wall-clock time, `DateTime(timezone=True)`
  the instant, and `Time(timezone=True)` its offset. JSON keys are always taken literally.
- pandas `to_sql`/`read_sql` and Alembic migrations work with no extra setup. Alembic's
  autogenerate compares types as DuckDB stores them and constraints by their columns, and
  `op.drop_table()` drops the sequence that numbered the table's key.
- Tests against a real Hardcharger server: integration tests, SQLAlchemy's own dialect compliance
  suite, and every README example run as written.
