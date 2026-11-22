"""Read-only access to the legacy MySQL database (or a SQLite copy for tests).

This is the one place with raw SQL (README §7.1): the legacy schema has no
Django models. Table names come from a fixed allow-list, never from input,
and no value is interpolated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

TABLES = frozenset(
    {
        "core_spaces",
        "core_users",
        "core_j_spaces_user",
        "cl_pricings",
        "cl_clients",
        "cl_addresses",
        "cl_j_client_user",
        "bk_nightwe",
        "re_area",
        "re_category",
        "re_info",
        "bk_schedulings",
        "bk_authorization",
        "bk_prices",
        "bk_packages",
        "bk_j_packages_prices",
        "bk_calendar_entry",
        "in_invoice",
        "in_invoice_item",
        "se_services",
        "se_prices",
        "se_order",
        "se_order_service",
        "se_project",
        "se_project_service",
    }
)


class Source(Protocol):
    def has_table(self, table: str) -> bool: ...

    def rows(self, table: str) -> Iterator[dict[str, Any]]: ...


def _check(table: str) -> str:
    if table not in TABLES:
        raise ValueError(f"Table {table!r} is not part of the legacy import")
    return table


class SQLiteSource:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def has_table(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_check(table),)
        ).fetchone()
        return row is not None

    def rows(self, table: str) -> Iterator[dict[str, Any]]:
        name = _check(table)
        columns = {r[1] for r in self.conn.execute(f"PRAGMA table_info({name})")}
        order = " ORDER BY id" if "id" in columns else ""  # some join tables have no id
        for row in self.conn.execute(f"SELECT * FROM {name}{order}"):  # noqa: S608 - allow-listed name
            yield dict(row)


class MySQLSource:
    """``mysql://user:password@host:3306/database``; needs the ``legacy`` extra (PyMySQL)."""

    def __init__(self, url: str) -> None:
        try:
            import pymysql
            import pymysql.cursors
        except ImportError as exc:  # pragma: no cover - depends on the image variant
            raise RuntimeError(
                "PyMySQL is missing: use the :legacy-import image or `uv sync --extra legacy`."
            ) from exc
        parts = urlsplit(url)
        self.conn = pymysql.connect(
            host=parts.hostname or "localhost",
            port=parts.port or 3306,
            user=unquote(parts.username or ""),
            password=unquote(parts.password or ""),
            database=parts.path.lstrip("/"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.SSDictCursor,  # stream big tables
            read_timeout=600,
        )

    def has_table(self, table: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE %s", (_check(table),))
            return cur.fetchone() is not None

    def rows(self, table: str) -> Iterator[dict[str, Any]]:
        name = _check(table)
        with self.conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `" + name + "` LIKE 'id'")  # allow-listed name
            has_id = cur.fetchone() is not None
        order = " ORDER BY id" if has_id else ""  # some join tables have no id
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{name}`{order}")  # noqa: S608 - allow-listed name
            yield from cur


def open_source(url: str) -> Source:
    if url.startswith("sqlite:///"):
        return SQLiteSource(url.removeprefix("sqlite:///"))
    if url.startswith("mysql://"):
        return MySQLSource(url)
    raise ValueError("Use mysql://user:pass@host/db or sqlite:///path/to/copy.db")
