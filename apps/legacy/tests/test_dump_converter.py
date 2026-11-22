import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("converter", ROOT / "scripts/mysql_dump_to_sqlite.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)

DUMP = r"""
CREATE TABLE `core_users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `login` varchar(100) NOT NULL,
  `note` text,
  `score` float,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE `empty_one` (
  `id` int(11) NOT NULL
) ENGINE=InnoDB;
INSERT INTO `core_users` (`id`,`login`,`note`,`score`) VALUES
(1,'o\'brien','line1\nline2 \\ back',1.5),(2,'it''s',NULL,-3),
(3,'paren ) and , comma','tab\there',0);
"""


def test_converts_escapes_nulls_numbers(tmp_path):
    dump = tmp_path / "d.sql"
    dump.write_text(DUMP)
    target = tmp_path / "out.sqlite3"
    counts = converter.convert(dump, target)
    assert counts == {"core_users": 3, "empty_one": 0}
    rows = (
        sqlite3.connect(target)
        .execute("select id, login, note, score from core_users order by id")
        .fetchall()
    )
    assert rows == [
        (1, "o'brien", "line1\nline2 \\ back", 1.5),
        (2, "it's", None, -3),
        (3, "paren ) and , comma", "tab\there", 0),
    ]
