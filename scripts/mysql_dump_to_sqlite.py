"""Convert a mysqldump-style SQL file into a SQLite database for `import_legacy`.

    python scripts/mysql_dump_to_sqlite.py dump.sql legacy.sqlite3
    python manage.py import_legacy --source sqlite:///legacy.sqlite3 --dry-run

Only what the importer needs is kept: table and column names (untyped
SQLite columns) and the rows of `INSERT INTO ... (cols) VALUES (...)`.
Values are parsed with a real tokenizer (MySQL backslash escapes, NULL,
numbers) and inserted with parameters, never by pasting SQL into SQLite.
Handy when no MySQL server is around; otherwise point the importer at
MySQL directly.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

CREATE = re.compile(r"^CREATE TABLE `(\w+)` \(\s*$")
COLUMN = re.compile(r"^\s+`(\w+)` ")
INSERT = re.compile(r"^INSERT INTO `(\w+)` \(([^)]*)\) VALUES\s*")
ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def parse_values(text: str, start: int) -> Iterator[list[object]]:
    """Yield the tuples of a VALUES clause beginning at ``text[start]``."""
    i, n = start, len(text)
    while i < n:
        while i < n and text[i] in " ,\n\r\t":
            i += 1
        if i >= n or text[i] == ";":
            return
        if text[i] != "(":
            raise ValueError(f"expected '(' at offset {i}")
        i += 1
        row: list[object] = []
        while True:
            while text[i] in " \n\r\t":
                i += 1
            if text[i] == "'":
                i += 1
                buf = []
                while True:
                    c = text[i]
                    if c == "\\":
                        nxt = text[i + 1]
                        buf.append(ESCAPES.get(nxt, nxt))
                        i += 2
                    elif c == "'":
                        if i + 1 < n and text[i + 1] == "'":  # '' inside a string
                            buf.append("'")
                            i += 2
                        else:
                            i += 1
                            break
                    else:
                        buf.append(c)
                        i += 1
                row.append("".join(buf))
            else:
                j = i
                while text[j] not in ",)":
                    j += 1
                token = text[i:j].strip()
                i = j
                if token.upper() == "NULL":
                    row.append(None)
                else:
                    try:
                        row.append(int(token))
                    except ValueError:
                        try:
                            row.append(float(token))
                        except ValueError:
                            row.append(token)
            while text[i] in " \n\r\t":
                i += 1
            if text[i] == ",":
                i += 1
                continue
            if text[i] == ")":
                i += 1
                break
            raise ValueError(f"unexpected {text[i]!r} at offset {i}")
        yield row


def convert(dump: Path, target: Path) -> dict[str, int]:
    text = dump.read_text(encoding="utf-8", errors="replace")
    target.unlink(missing_ok=True)
    db = sqlite3.connect(target)
    counts: dict[str, int] = {}
    table = None
    columns: list[str] = []
    for line in text.splitlines():
        if table is None:
            m = CREATE.match(line)
            if m:
                table, columns = m.group(1), []
            continue
        m = COLUMN.match(line)
        if m:
            columns.append(m.group(1))
        elif line.startswith(")"):
            cols = ", ".join(f'"{c}"' for c in columns)
            db.execute(f'CREATE TABLE "{table}" ({cols})')
            counts[table] = 0
            table = None
    for m in re.finditer(INSERT.pattern, text, re.M):
        name = m.group(1)
        insert_cols = [c.strip().strip("`") for c in m.group(2).split(",")]
        quoted = ", ".join(f'"{c}"' for c in insert_cols)
        placeholders = ", ".join("?" * len(insert_cols))
        # Table and column names matched \w+ in the dump: identifiers only.
        sql = f'INSERT INTO "{name}" ({quoted}) VALUES ({placeholders})'  # noqa: S608
        rows = list(parse_values(text, m.end()))
        db.executemany(sql, rows)
        counts[name] = counts.get(name, 0) + len(rows)
    db.commit()
    db.close()
    return counts


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    result = convert(Path(sys.argv[1]), Path(sys.argv[2]))
    filled = {k: v for k, v in result.items() if v}
    print(f"{len(result)} tables, {len(filled)} with rows, {sum(filled.values())} rows")
