"""List translatable strings missing from the French catalogue.

Stopgap while gettext (makemessages) is not installed everywhere; prefer
``manage.py makemessages -l fr`` when it is.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
po = (ROOT / "locale/fr/LC_MESSAGES/django.po").read_text()
have = set(re.findall(r'^msgid "((?:[^"\\]|\\.)*)"', po, re.M))
found: list[object] = []


def add(item: object) -> None:
    key = item[1] if isinstance(item, tuple) else item
    if key not in have and item not in found:
        found.append(item)


for p in [*ROOT.joinpath("apps").rglob("*.py"), *ROOT.joinpath("config").rglob("*.py")]:
    if {"migrations", "tests", "management"} & set(p.parts):
        continue
    t = p.read_text()
    for m in re.finditer(r'(?<![\w.])_\(\s*"((?:[^"\\]|\\.)*)"', t):
        add(m.group(1))
    for m in re.finditer(r'ngettext\(\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"', t):
        add(("PLURAL", m.group(1), m.group(2)))
for p in [
    *ROOT.joinpath("templates").rglob("*.html"),
    *ROOT.joinpath("apps").rglob("*.html"),
    *ROOT.joinpath("apps").rglob("*.txt"),
]:
    t = p.read_text()
    for pat in (
        r'{%\s*translate\s+"((?:[^"\\]|\\.)*)"',
        r"{%\s*translate\s+'((?:[^'\\]|\\.)*)'",
        r'_\("((?:[^"\\]|\\.)*)"\)',
    ):
        for m in re.finditer(pat, t):
            add(m.group(1))
    for m in re.finditer(r"{%\s*blocktranslate[^%]*%}(.*?){%\s*endblocktranslate", t, re.S):
        add(re.sub(r"{{\s*(\w+)\s*}}", r"%(\1)s", m.group(1)))
for item in found:
    print(repr(item))
