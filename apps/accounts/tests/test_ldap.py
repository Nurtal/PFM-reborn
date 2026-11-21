from types import SimpleNamespace

import pytest

from apps.accounts import ldap
from apps.accounts.models import User
from apps.core.models import InstanceSettings
from apps.core.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

DIRECTORY = {
    "uid=jdoe,ou=people,dc=univ,dc=fr": (
        "s3cret",
        {"mail": "JDoe@univ.fr", "givenName": "John", "sn": "Doe"},
    )
}


class FakeEntry:
    def __init__(self, dn, attrs):
        self.entry_dn = dn
        self._attrs = attrs

    def __getitem__(self, key):
        return SimpleNamespace(value=self._attrs[key])


class FakeConnection:
    instances: list = []

    def __init__(self, server, user, password, **kwargs):
        self.user, self.password, self.entries, self.searches = user, password, [], []
        FakeConnection.instances.append(self)

    def bind(self):
        if self.user == "cn=svc,dc=univ,dc=fr":
            return self.password == "svc-pass"
        known = DIRECTORY.get(self.user)
        return bool(known and known[0] == self.password)

    def search(self, base, query, attributes=None, search_scope=None, size_limit=0):
        self.searches.append(query)
        if base in DIRECTORY:
            self.entries = [FakeEntry(base, DIRECTORY[base][1])]
        elif query == "(uid=jdoe)":
            dn = "uid=jdoe,ou=people,dc=univ,dc=fr"
            self.entries = [FakeEntry(dn, DIRECTORY[dn][1])]
        else:
            self.entries = []

    def unbind(self):
        pass

    def open(self):
        pass

    def start_tls(self):
        pass


@pytest.fixture(autouse=True)
def fake_ldap(monkeypatch):
    import ldap3

    FakeConnection.instances = []
    monkeypatch.setattr(ldap3, "Connection", FakeConnection)
    monkeypatch.setattr(ldap3, "Server", lambda *a, **k: object())


def configure(**kw):
    conf = InstanceSettings.load()
    conf.ldap_url = "ldaps://ldap.univ.fr"
    for k, v in kw.items():
        setattr(conf, k, v)
    conf.save()
    return conf


def auth(username, password):
    return ldap.LDAPBackend().authenticate(None, username=username, password=password)


def test_disabled_without_configuration():
    assert auth("jdoe", "s3cret") is None


def test_direct_bind_creates_the_account():
    configure(ldap_user_dn_template="uid={username},ou=people,dc=univ,dc=fr")
    user = auth("jdoe", "s3cret")
    assert (user.username, user.email, user.first_name, user.last_name) == (
        "jdoe",
        "jdoe@univ.fr",
        "John",
        "Doe",
    )
    assert not user.has_usable_password()
    assert auth("jdoe", "wrong") is None


def test_search_then_bind_escapes_the_filter():
    conf = configure(
        ldap_bind_dn="cn=svc,dc=univ,dc=fr", ldap_search_base="ou=people,dc=univ,dc=fr"
    )
    conf.set_ldap_bind_password("svc-pass")
    conf.save()
    assert auth("jdoe", "s3cret").username == "jdoe"
    assert auth("*)(uid=*", "x") is None
    searches = [q for c in FakeConnection.instances for q in c.searches]
    assert r"(uid=\2a\29\28uid=\2a)" in searches  # injection characters escaped


def test_dn_template_escapes_username():
    configure(ldap_user_dn_template="uid={username},ou=people,dc=univ,dc=fr")
    auth("jdoe,ou=admins", "s3cret")
    assert any(
        c.user == r"uid=jdoe\,ou\=admins,ou=people,dc=univ,dc=fr" for c in FakeConnection.instances
    )


def test_never_logs_in_as_a_local_account():
    configure(ldap_user_dn_template="uid={username},ou=people,dc=univ,dc=fr")
    UserFactory(username="jdoe")  # local account with a password
    assert auth("jdoe", "s3cret") is None


def test_refuses_when_email_belongs_to_someone_else():
    configure(ldap_user_dn_template="uid={username},ou=people,dc=univ,dc=fr")
    UserFactory(username="other", email="jdoe@univ.fr")
    assert auth("jdoe", "s3cret") is None
    assert not User.objects.filter(username="jdoe").exists()


def test_bind_password_is_encrypted_at_rest():
    conf = configure()
    conf.set_ldap_bind_password("svc-pass")
    conf.save()
    assert "svc-pass" not in InstanceSettings.load().ldap_bind_password_encrypted
    assert InstanceSettings.load().get_ldap_bind_password() == "svc-pass"
