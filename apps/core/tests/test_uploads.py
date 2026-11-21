import io
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core import uploads


def f(name, content):
    return SimpleUploadedFile(name, content)


def docx_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("report.pdf", b"%PDF-1.7 ..."),
        ("photo.PNG", b"\x89PNG\r\n\x1a\nrest"),
        ("notes.txt", "Résumé ✓".encode()),
        ("table.docx", docx_bytes()),
    ],
)
def test_accepted(name, content):
    checked = uploads.check_upload(f(name, content))
    assert checked.extension == name.rsplit(".", 1)[1].lower()
    assert checked.storage_name().endswith("." + checked.extension)
    assert name not in checked.storage_name()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("page.html", b"<script>alert(1)</script>"),  # not allowed at all
        ("image.svg", b"<svg onload=alert(1)>"),
        ("fake.pdf", b"<html>not a pdf"),  # content does not match
        ("fake.png", b"%PDF-1.7"),
        ("fake.docx", b"PK\x03\x04garbage"),
        ("other.docx", b"%PDF"),
        ("binary.txt", b"\x00\x01\x02\xff\xfe"),
        ("empty.pdf", b""),
        ("noext", b"%PDF-"),
    ],
)
def test_rejected(name, content):
    with pytest.raises(ValidationError):
        uploads.check_upload(f(name, content))


def test_size_limit(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValidationError):
        uploads.check_upload(f("big.txt", b"a" * 11))


def test_path_in_name_is_dropped():
    checked = uploads.check_upload(f("../../etc/passwd.txt", b"hello"))
    assert checked.original_name == "passwd.txt"
