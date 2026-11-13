# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-13

### Added

- The `hardcharger://` SQLAlchemy dialect for Hardcharger™, GizmoData's DuckDB 2.0 server, on the
  native Hardcharger ADBC driver (`adbc-driver-hardcharger>=2.4.9`) over Chargewire HTTP(S).
  SQLAlchemy 2.0 and 2.1, Python 3.11 to 3.15.
- URLs: `hardcharger://user:password@host[:port][/catalog][?option=value]`. TLS is on unless
  `?tls=false`. Options cover the schema, TLS trust and client certificates, token and browser
  (SSO) sign-in, session and query tags, `hardcharger.<setting>` session settings and
  `adbc.<option>` driver options. An unknown option is refused.
- SQLAlchemy Core and the ORM: DuckDB SQL generation, `RETURNING`, `ON CONFLICT` upserts
  (`sqlalchemy_hardcharger.insert`), sequences, JSON keys and JSONPath access, regular
  expressions, `TRY_CAST`, window functions, CTEs.
- Auto-incrementing integer primary keys: DuckDB has neither `SERIAL` nor identity columns, so
  the table's key gets a sequence, created with the table and dropped with it. `Identity()`
  options set the sequence's start and increment.
- Transactions that begin at the first statement, not at checkout, so a pooled connection holds
  none open on the server and an unused one sends nothing. Isolation levels `SNAPSHOT` and
  `AUTOCOMMIT`. A session the server ended is a disconnect: the pool replaces the connection.
- Reflection through DuckDB's catalog functions: schemas (including other attached catalogs, as
  `catalog.schema`), tables, views, temporary tables, columns with defaults, comments and
  generated columns, primary and foreign keys, unique and check constraints, indexes (including
  expression indexes), sequences and comments.
- Types: SQLAlchemy's generic types, plus `TINYINT`, the unsigned integers, `HUGEINT`,
  `UHUGEINT`, `BIGNUM`, `TIMESTAMP_S/_MS/_NS`, `TIME_NS`, `BIT`, `VARIANT`, `GEOMETRY`, `Struct`,
  `Map` and `Union`. `VARIANT` columns read and write Python values. Generic `Float` is a
  `DOUBLE`, so a Python `float` comes back as it was stored.
- pandas `to_sql`/`read_sql` and Alembic migrations work with no extra setup.
- Tests against a real Hardcharger server: integration tests, SQLAlchemy's own dialect compliance
  suite, and every README example run as written.
