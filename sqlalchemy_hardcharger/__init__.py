"""SQLAlchemy dialect for Hardcharger, GizmoData's DuckDB 2.0 server.

    from sqlalchemy import create_engine, text

    engine = create_engine("hardcharger://user:password@localhost:31337?tls=false")
    with engine.connect() as conn:
        print(conn.execute(text("SELECT 42 AS answer")).scalar())

Installing the package registers the ``hardcharger://`` URL scheme with SQLAlchemy.
"""

from sqlalchemy.dialects.postgresql import insert

from ._version import __version__
from .dialect import HardchargerConnection, HardchargerDialect
from .types import (
    BIGNUM,
    BIT,
    GEOMETRY,
    HUGEINT,
    TIME_NS,
    TIMESTAMP_MS,
    TIMESTAMP_NS,
    TIMESTAMP_S,
    TINYINT,
    UBIGINT,
    UHUGEINT,
    UINTEGER,
    USMALLINT,
    UTINYINT,
    VARIANT,
    Map,
    Struct,
    Union,
)

try:  # Alembic is optional: register the migration support only where it is installed.
    from . import alembic as _alembic  # noqa: F401
except ImportError:  # pragma: no cover
    pass

__all__ = [
    "BIGNUM",
    "BIT",
    "GEOMETRY",
    "HUGEINT",
    "TIMESTAMP_MS",
    "TIMESTAMP_NS",
    "TIMESTAMP_S",
    "TIME_NS",
    "TINYINT",
    "UBIGINT",
    "UHUGEINT",
    "UINTEGER",
    "USMALLINT",
    "UTINYINT",
    "VARIANT",
    "HardchargerConnection",
    "HardchargerDialect",
    "Map",
    "Struct",
    "Union",
    "__version__",
    "insert",
]
