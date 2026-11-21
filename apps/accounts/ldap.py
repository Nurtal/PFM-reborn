"""LDAP authentication with ldap3 (pure Python, no system library).

Two modes, set in the instance settings:
* direct bind: the user's DN is built from a template, then bound with
  their password;
* search then bind: a service account finds the user's DN with a filter.

User input is escaped both in DNs (RFC 4514) and filters (RFC 4515), so a
username cannot inject LDAP syntax. A successful login creates or updates
the local account (names, e-mail); the password is never stored locally.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest

from apps.accounts.models import User

logger = logging.getLogger(__name__)


def _conf() -> Any:
    from apps.core.models import InstanceSettings

    conf = InstanceSettings.load()
    return conf if conf.ldap_url else None


def _server(conf: Any) -> Any:
    import ldap3

    return ldap3.Server(conf.ldap_url, get_info=ldap3.NONE, connect_timeout=5)


def _connect(conf: Any, dn: str, password: str) -> Any:
    import ldap3

    conn = ldap3.Connection(
        _server(conf), user=dn, password=password, receive_timeout=10, raise_exceptions=False
    )
    if conf.ldap_start_tls:
        conn.open()
        conn.start_tls()
    return conn if conn.bind() else None


def _attr(entry: Any, name: str) -> str:
    try:
        value = entry[name].value
    except (KeyError, IndexError, TypeError):  # missing attribute
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def find_user_dn(conf: Any, username: str) -> tuple[str, Any] | None:
    from ldap3.utils.conv import escape_filter_chars

    service = _connect(conf, conf.ldap_bind_dn, conf.get_ldap_bind_password())
    if service is None:
        logger.error("LDAP: service account bind failed")
        return None
    query = conf.ldap_search_filter.replace("{username}", escape_filter_chars(username))
    attrs = [conf.ldap_attr_email, conf.ldap_attr_first_name, conf.ldap_attr_last_name]
    service.search(conf.ldap_search_base, query, attributes=attrs, size_limit=2)
    entries = list(service.entries)
    service.unbind()
    if len(entries) != 1:  # none, or ambiguous: refuse
        return None
    return entries[0].entry_dn, entries[0]


class LDAPBackend(BaseBackend):
    def authenticate(  # type: ignore[override]
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> User | None:
        if not username or not password:
            return None
        conf = _conf()
        if conf is None:
            return None
        from ldap3.utils.dn import escape_rdn

        username = username.strip()
        try:
            entry = None
            if conf.ldap_bind_dn and conf.ldap_search_base:
                found = find_user_dn(conf, username)
                if found is None:
                    return None
                dn, entry = found
            elif conf.ldap_user_dn_template:
                dn = conf.ldap_user_dn_template.replace("{username}", escape_rdn(username))
            else:
                return None
            conn = _connect(conf, dn, password)
            if conn is None:
                return None
            if entry is None:
                attrs = [conf.ldap_attr_email, conf.ldap_attr_first_name, conf.ldap_attr_last_name]
                conn.search(dn, "(objectClass=*)", search_scope="BASE", attributes=attrs)
                entry = conn.entries[0] if conn.entries else None
            conn.unbind()
        except Exception:
            logger.exception("LDAP authentication error")
            return None
        return self._sync_user(conf, username, entry)

    def _sync_user(self, conf: Any, username: str, entry: Any) -> User | None:
        email = _attr(entry, conf.ldap_attr_email).lower() if entry is not None else ""
        user = User.objects.filter(username=username).first()
        if user is None:
            if email and User.objects.filter(email__iexact=email).exists():
                logger.warning("LDAP: e-mail %s already used by another account", email)
                return None
            user = User(username=username, email=email or f"{username}@ldap.invalid")
            user.set_unusable_password()
        elif not user.is_active:
            return None
        elif user.is_superuser or user.has_usable_password():
            # A local account with the same name: never let the directory log in as it.
            logger.warning("LDAP: %s is a local account, directory login refused", username)
            return None
        if entry is not None:
            user.first_name = _attr(entry, conf.ldap_attr_first_name)[:150] or user.first_name
            user.last_name = _attr(entry, conf.ldap_attr_last_name)[:150] or user.last_name
            taken = User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists()
            if email and email != user.email and not taken:
                user.email = email
        user.save()
        return user

    def get_user(self, user_id: int) -> User | None:
        return User.objects.filter(pk=user_id, is_active=True).first()
