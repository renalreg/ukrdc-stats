"""
Transparent query-result caching for SQLAlchemy sessions.

Design, in plain terms:
  - CacheSession.execute() checks the cache BEFORE running the query.
    On a hit, the database is never touched.
  - On a miss, the query runs and results are fully materialized (`.all()`)
    right away, then cached. This avoids the SQLAlchemy Result object's
    "single use, cursor-like" behaviour and sidesteps the complexity of
    Result.freeze()/unfreeze().
  - The wrapped result (_ListResult) supports the handful of accessor
    methods people actually call: all(), first(), one(), one_or_none(),
    scalars(), scalar().

Known limitation (read before using with ORM entities):
  Cached rows containing ORM entities (e.g. `select(User)`) are detached
  from whatever Session originally loaded them. On a cache hit you get
  back entities NOT attached to your current session -- accessing a
  deferred column or relationship on them will raise DetachedInstanceError.
  If you need that, call `session.merge(obj)` on the returned entities
  before using them, or restrict caching to plain column/scalar queries
  (e.g. `select(User.id, User.name)`) where this doesn't apply.
"""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dogpile.cache import make_region
from dogpile.cache.api import NO_VALUE
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

import portalocker
from dotenv import dotenv_values


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

config_from_env = dotenv_values(".env")
CONFIG = {
    "expiration": int(config_from_env.get("CACHE_EXPIRATION", 3600)),
    "backend": config_from_env.get("CACHE_BACKEND", "file"),
    "cache_dir": Path(config_from_env.get("CACHE_DIR", ".do_not_commit"))
    / "query_cache",
    "redis_host": config_from_env.get("REDIS_HOST", "localhost"),
    "redis_port": int(config_from_env.get("REDIS_PORT", 6379)),
}


# ---------------------------------------------------------------------------
# File lock (unchanged - Windows-compatible dbm locking for dogpile)
# ---------------------------------------------------------------------------


class WindowsFileLock:
    """Cross-platform file lock using portalocker (Windows-compatible)."""

    def __init__(self, filename: str):
        self.filename = filename
        self._filedesc = None

    def _open_lockfile(self) -> int:
        import os

        return os.open(self.filename, os.O_CREAT | os.O_RDWR)

    def acquire_read_lock(self, wait: bool) -> bool:
        if self._filedesc is None:
            self._filedesc = self._open_lockfile()
        flag = portalocker.LOCK_SH | (0 if wait else portalocker.LOCK_NB)
        try:
            portalocker.lock(self._filedesc, flag)
            return True
        except portalocker.LockException:
            return False

    def acquire_write_lock(self, wait: bool) -> bool:
        if self._filedesc is None:
            self._filedesc = self._open_lockfile()
        flag = portalocker.LOCK_EX | (0 if wait else portalocker.LOCK_NB)
        try:
            portalocker.lock(self._filedesc, flag)
            return True
        except portalocker.LockException:
            return False

    def release_read_lock(self) -> None:
        if self._filedesc is not None:
            portalocker.unlock(self._filedesc)

    def release_write_lock(self) -> None:
        if self._filedesc is not None:
            portalocker.unlock(self._filedesc)

    def acquire(self, wait: bool = True) -> bool:
        return self.acquire_write_lock(wait)

    def release(self) -> None:
        self.release_write_lock()

    def read(self):
        @contextmanager
        def _read_ctx():
            self.acquire_read_lock(True)
            try:
                yield self
            finally:
                self.release_read_lock()

        return _read_ctx()

    def write(self):
        @contextmanager
        def _write_ctx():
            self.acquire_write_lock(True)
            try:
                yield self
            finally:
                self.release_write_lock()

        return _write_ctx()


# ---------------------------------------------------------------------------
# Cache region
# ---------------------------------------------------------------------------

_cache_region = None


def _get_cache_region(config: dict) -> Any:
    global _cache_region
    if _cache_region is not None:
        return _cache_region

    backend = config.get("backend", "memory")

    if backend == "memory":
        _cache_region = make_region().configure("dogpile.cache.memory")
    elif backend == "file":
        cache_dir = config["cache_dir"]
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_region = make_region().configure(
            "dogpile.cache.dbm",
            expiration_time=config.get("expiration", 3600),
            arguments={
                "filename": str(cache_dir / "query_cache.dbm"),
                "lock_factory": WindowsFileLock,
            },
        )
    elif backend == "redis":
        _cache_region = make_region().configure(
            "dogpile.cache.redis",
            expiration_time=config.get("expiration", 3600),
            arguments={
                "host": config.get("redis_host", "localhost"),
                "port": config.get("redis_port", 6379),
                "db": 0,
                "redis_expiration_time": config.get("expiration", 3600),
            },
        )
    else:
        raise ValueError(f"Unknown cache backend: {backend}")

    return _cache_region


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def _generate_cache_key(url: URL, statement: Any, params: dict | None) -> str:
    """Unique key per (database, compiled SQL incl. literal values, params).

    Bind values embedded in the statement itself (e.g. `User.id == 5`) are
    baked in via literal_binds so that two differently-parameterized ORM
    statements never collide on the same key.
    """
    try:
        compiled = statement.compile(compile_kwargs={"literal_binds": True})
        sql_text = str(compiled)
    except Exception:
        # Some constructs (custom types, certain functions) can't render
        # literal binds. Fall back to the raw statement text; this is only
        # safe because `params` is included separately below.
        sql_text = str(statement)

    parameters = params or {}
    key_parts = {
        "db": f"{url.host}:{url.port}/{url.database}",
        "sql": sql_text,
        "params": {k: str(v) for k, v in parameters.items()},
    }
    blob = json.dumps(key_parts, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Minimal result wrapper
# ---------------------------------------------------------------------------


class _ListResult:
    """A tiny stand-in for SQLAlchemy's Result, backed by a plain list.

    Supports the common accessor methods. Reusable (unlike a real Result,
    which is single-use) since it just wraps a list.
    """

    def __init__(self, rows: list):
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> Any:
        if len(self._rows) > 1:
            raise ValueError("one_or_none() found more than one row")
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        if len(self._rows) != 1:
            raise ValueError(f"one() expected exactly one row, got {len(self._rows)}")
        return self._rows[0]

    def scalars(self) -> "_ListResult":
        """Reduce each row to its first column."""
        return _ListResult([row[0] for row in self._rows])

    def scalar(self) -> Any:
        row = self.first()
        return row[0] if row is not None else None

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)


# ---------------------------------------------------------------------------
# Caching session
# ---------------------------------------------------------------------------


class CacheSession:
    """Wraps a SQLAlchemy Session; execute() checks the cache before
    hitting the database. Everything else passes through untouched.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._url = session.bind.url

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def execute(
        self, statement: Any, params: dict | None = None, **kwargs: Any
    ) -> _ListResult:
        key = _generate_cache_key(self._url, statement, params)
        region = _get_cache_region(CONFIG)

        cached_rows = region.get(key)
        if cached_rows is not NO_VALUE:
            return _ListResult(cached_rows)

        rows = self._session.execute(statement, params, **kwargs).all()
        region.set(key, rows)
        return _ListResult(rows)

    def __enter__(self) -> "CacheSession":
        self._session.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._session.close()


class CachedSessionMaker(sessionmaker):
    """Drop-in replacement for sessionmaker that returns CacheSession instances."""

    def __call__(self, **local_kw: Any) -> CacheSession:
        session = super().__call__(**local_kw)
        return CacheSession(session)


def cachedsessionmaker(**local_kw: Any):
    return CachedSessionMaker(**local_kw)
