"""Run the SQLite-based tests against a real PostgreSQL instead.

Many tests build an in-memory SQLite database with create_async_engine. SQLite is
quick but not PostgreSQL: it accepts queries PostgreSQL rejects and lacks
functions PostgreSQL has (to_char, extract(dow ...), DISTINCT ON), so some code
can only be checked for real on PostgreSQL.

    pip install pgserver            # a bundled PostgreSQL, no Docker needed
    SCROB_TEST_DB=postgres python -m pytest -p pg_test_plugin tests

With SCROB_TEST_DB=postgres a throwaway PostgreSQL server is started, the real
schema is built once by running the Alembic migrations, and every
create_async_engine("sqlite...") call in the tests gets its own copy of that
database, so the tests run unchanged. Without the variable this plugin does
nothing.
"""
import itertools
import os
import subprocess
import sys
import tempfile

if os.environ.get("SCROB_TEST_DB") == "postgres":
    import pgserver
    from sqlalchemy.ext import asyncio as sa_asyncio
    from sqlalchemy.sql.schema import Table

    _backend_dir = os.path.dirname(os.path.abspath(__file__))
    _server = pgserver.get_server(tempfile.mkdtemp(prefix="scrob-test-pg-"))
    _socket_dir = _server.get_uri().split("host=")[-1]
    _template = "scrob_template"
    _databases = itertools.count()


    def _url(database: str) -> str:
        return f"postgresql+asyncpg://postgres@/{database}?host={_socket_dir}"


    # The schema comes from the migrations, as in production: the models'
    # create_all is not valid on PostgreSQL (some server defaults are written for
    # SQLite). Built once, then copied for each test.
    _server.psql(f"CREATE DATABASE {_template};")
    os.environ["DATABASE_URL"] = _url(_template)
    os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes-long")
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=_backend_dir,
        check=True,
        capture_output=True,
    )

    _create_async_engine = sa_asyncio.create_async_engine


    def _create_engine(url, *args, **kwargs):
        if not str(url).startswith("sqlite"):
            return _create_async_engine(url, *args, **kwargs)
        name = f"test_{next(_databases)}"
        _server.psql(f"CREATE DATABASE {name} TEMPLATE {_template};")
        return _create_async_engine(_url(name))


    sa_asyncio.create_async_engine = _create_engine

    # The schema already exists, so the tests' own table creation is a no-op.
    Table.create = lambda self, bind=None, checkfirst=False: None
