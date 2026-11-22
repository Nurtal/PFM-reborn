"""Upload validation shared by every module (README §7.8).

Files are accepted on an allow-list of extensions whose content must match
the expected signature, are size-limited, get a random name, live under
MEDIA_ROOT (never served directly) and are downloaded through views that
check permissions.
"""

from __future__ import annotations

import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext as _

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class FileKind:
    content_type: str
    signatures: tuple[bytes, ...] = ()
    zip_member: str | None = None  # Office Open XML / OpenDocument containers
    text: bool = False


KINDS: dict[str, FileKind] = {
    "pdf": FileKind("application/pdf", (b"%PDF-",)),
    "png": FileKind("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": FileKind("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": FileKind("image/jpeg", (b"\xff\xd8\xff",)),
    "txt": FileKind("text/plain", text=True),
    "csv": FileKind("text/csv", text=True),
    "md": FileKind("text/markdown", text=True),
    "docx": FileKind(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        zip_member="[Content_Types].xml",
    ),
    "xlsx": FileKind(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        zip_member="[Content_Types].xml",
    ),
    "pptx": FileKind(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        zip_member="[Content_Types].xml",
    ),
    "odt": FileKind("application/vnd.oasis.opendocument.text", zip_member="mimetype"),
    "ods": FileKind("application/vnd.oasis.opendocument.spreadsheet", zip_member="mimetype"),
}


@dataclass(frozen=True)
class CheckedUpload:
    extension: str
    content_type: str
    size: int
    original_name: str

    def storage_name(self) -> str:
        return f"{uuid.uuid4().hex}.{self.extension}"


def check_upload(upload: UploadedFile, allowed: set[str] | None = None) -> CheckedUpload:
    """Validate an uploaded file or raise ``ValidationError``."""
    name = PurePath(upload.name or "").name[:200]
    extension = PurePath(name).suffix.lower().lstrip(".")
    kinds = {k: v for k, v in KINDS.items() if allowed is None or k in allowed}
    if extension not in kinds:
        raise ValidationError(
            _("File type not allowed. Accepted: %(types)s.") % {"types": ", ".join(sorted(kinds))}
        )
    if upload.size is None or upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(_("The file is larger than 20 MB."))
    if upload.size == 0:
        raise ValidationError(_("The file is empty."))
    kind = kinds[extension]
    upload.seek(0)
    head = upload.read(8192)
    upload.seek(0)
    if kind.signatures and not any(head.startswith(sig) for sig in kind.signatures):
        raise ValidationError(_("The file content does not match its extension."))
    if kind.text:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            # A multi-byte character may be cut at the boundary: retry without it.
            try:
                head[:-3].decode("utf-8")
            except UnicodeDecodeError:
                raise ValidationError(_("Text files must be UTF-8.")) from None
        if b"\x00" in head:
            raise ValidationError(_("The file content does not match its extension."))
    if kind.zip_member:
        try:
            with zipfile.ZipFile(upload) as archive:
                if kind.zip_member not in archive.namelist():
                    raise ValidationError(_("The file content does not match its extension."))
        except zipfile.BadZipFile:
            raise ValidationError(_("The file content does not match its extension.")) from None
        finally:
            upload.seek(0)
    return CheckedUpload(extension, kind.content_type, upload.size, name)
