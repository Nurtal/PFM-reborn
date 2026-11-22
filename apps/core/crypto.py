"""Encryption of secrets stored in the database (SMTP, LDAP, IMAP passwords).

The Fernet key is derived from ``SECRET_KEY`` (itself stored in ``/data``), so
a database dump alone does not reveal those secrets.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings


def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"pfm-db-secrets-v1").derive(
        settings.SECRET_KEY.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted (was the secret key changed?)") from exc
