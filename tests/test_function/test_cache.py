from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

import ukrdc_stats.utils.cache as cache_module
from ukrdc_stats.utils.cache import cachedsessionmaker


def _configure_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    monkeypatch.setattr(cache_module, "_cache_region", None)
    monkeypatch.setitem(cache_module.CONFIG, "backend", backend)
    monkeypatch.setitem(cache_module.CONFIG, "cache_dir", tmp_path / "query_cache")
    monkeypatch.setitem(cache_module.CONFIG, "expiration", 3600)


def _counting_engine() -> tuple[Engine, list[Any]]:
    engine = create_engine("sqlite:///:memory:")
    statements: list[Any] = []

    @event.listens_for(engine, "before_cursor_execute")
    def count_statement(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    return engine, statements


@pytest.mark.parametrize("backend", ["memory", "file"])
def test_execute_uses_cache_before_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    _configure_backend(monkeypatch, tmp_path, backend)
    engine, statements = _counting_engine()

    with engine.begin() as conn:
        conn.execute(text("create table example (id integer)"))
        conn.execute(text("insert into example values (1)"))
    statements.clear()

    sessionmaker = cachedsessionmaker(bind=engine)
    with sessionmaker() as session:
        first = session.execute(text("select id from example")).all()
        second = session.execute(text("select id from example")).all()

    assert first == [(1,)]
    assert second == [(1,)]
    assert len(statements) == 1


def test_file_backend_persists_between_cache_regions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_backend(monkeypatch, tmp_path, "file")
    engine = create_engine("sqlite:///:memory:")

    with engine.begin() as conn:
        conn.execute(text("create table example (id integer)"))
        conn.execute(text("insert into example values (1)"))

    sessionmaker = cachedsessionmaker(bind=engine)
    with sessionmaker() as session:
        assert session.execute(text("select id from example")).all() == [(1,)]

    # A fresh region over the same directory should reuse the serialized rows,
    # even when the new in-memory database does not contain the table.
    monkeypatch.setattr(cache_module, "_cache_region", None)
    empty_engine = create_engine("sqlite:///:memory:")
    empty_sessionmaker = cachedsessionmaker(bind=empty_engine)
    with empty_sessionmaker() as session:
        assert session.execute(text("select id from example")).all() == [(1,)]
