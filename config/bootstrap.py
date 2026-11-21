"""Zero-configuration helpers used by the settings module.

Everything persistent lives in a single data directory (``/data`` in the
container). The secret key is generated there on first start so that a bare
``docker run`` works without any environment variable.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

SECRET_KEY_FILENAME = "secret_key"  # noqa: S105 - a file name, not a secret


def data_dir(default: Path) -> Path:
    path = Path(os.environ.get("PFM_DATA_DIR", default)).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_or_create_secret_key(directory: Path) -> str:
    """Return ``PFM_SECRET_KEY`` or the key stored in ``directory``, creating it if needed."""
    from_env = os.environ.get("PFM_SECRET_KEY", "").strip()
    if from_env:
        return from_env

    key_file = directory / SECRET_KEY_FILENAME
    if not key_file.exists():
        # Write to a private temp file, then hard-link it into place: link() is
        # atomic and fails if the target exists, so concurrent first starts
        # (web + worker) agree on a single key and never read a partial file.
        tmp = directory / f".{SECRET_KEY_FILENAME}.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(64))
        try:
            os.link(tmp, key_file)
        except FileExistsError:
            pass
        finally:
            tmp.unlink()
    key = key_file.read_text().strip()
    if not key:
        raise RuntimeError(f"{key_file} is empty; delete it to regenerate a key")
    return key
