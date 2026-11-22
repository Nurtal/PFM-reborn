import datetime as dt

import factory

from apps.core.tests.factories import SpaceFactory, UserFactory
from apps.resources.models import ALL_WEEKDAYS, Area, Authorization, Resource, ResourceCategory


class AreaFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Area

    space = factory.SubFactory(SpaceFactory)
    name = factory.Sequence(lambda n: f"Room {n}")
    open_weekdays = ALL_WEEKDAYS
    day_start = dt.time(8)
    day_end = dt.time(19)
    slot_minutes = 30


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ResourceCategory

    space = factory.SubFactory(SpaceFactory)
    name = factory.Sequence(lambda n: f"Microscopes {n}")


class ResourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Resource
        skip_postgeneration_save = True

    space = factory.SubFactory(SpaceFactory)
    name = factory.Sequence(lambda n: f"Confocal {n}")
    category = factory.SubFactory(CategoryFactory, space=factory.SelfAttribute("..space"))
    area = factory.SubFactory(AreaFactory, space=factory.SelfAttribute("..space"))


class AuthorizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Authorization

    user = factory.SubFactory(UserFactory)
    category = factory.SubFactory(CategoryFactory)
    space = factory.SelfAttribute("category.space")
