import factory
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import InstanceSettings, Role, Space, SpaceMembership


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.org")
    password = factory.django.Password("correct horse battery")


class SpaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Space

    name = factory.Sequence(lambda n: f"Platform {n}")
    slug = factory.Sequence(lambda n: f"platform-{n}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SpaceMembership

    user = factory.SubFactory(UserFactory)
    space = factory.SubFactory(SpaceFactory)
    role = Role.USER


def complete_setup() -> InstanceSettings:
    instance = InstanceSettings.load()
    instance.setup_completed_at = timezone.now()
    instance.save()
    return instance
