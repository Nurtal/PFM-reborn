import pytest

from apps.core import services
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_last_admin_cannot_be_demoted():
    m = MembershipFactory(role=Role.ADMIN)
    with pytest.raises(services.LastAdminError):
        services.change_role(m, Role.USER)


def test_last_admin_cannot_be_removed():
    m = MembershipFactory(role=Role.ADMIN)
    with pytest.raises(services.LastAdminError):
        services.remove_member(m)


def test_admin_can_be_demoted_when_another_exists():
    m = MembershipFactory(role=Role.ADMIN)
    MembershipFactory(space=m.space, role=Role.ADMIN)
    services.change_role(m, Role.USER)
    m.refresh_from_db()
    assert m.role == Role.USER


def test_add_member_updates_existing_role():
    m = MembershipFactory(role=Role.USER)
    services.add_member(m.space, m.user, Role.MANAGER)
    m.refresh_from_db()
    assert m.role == Role.MANAGER


def test_spaces_for_lists_only_memberships():
    m = MembershipFactory()
    SpaceFactory()
    assert services.spaces_for(m.user) == [m.space]


def test_superuser_sees_all_active_spaces():
    root = UserFactory(is_superuser=True)
    SpaceFactory()
    SpaceFactory(is_active=False)
    assert len(services.spaces_for(root)) == 1
