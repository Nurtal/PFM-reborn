from decimal import Decimal

import factory

from apps.clients.models import Client, Package, PricingGrid, ResourcePrice
from apps.core.tests.factories import SpaceFactory


class GridFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PricingGrid

    space = factory.SubFactory(SpaceFactory)
    name = factory.Sequence(lambda n: f"Grid {n}")


class ClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Client
        skip_postgeneration_save = True

    space = factory.SubFactory(SpaceFactory)
    name = factory.Sequence(lambda n: f"Lab {n}")
    pricing_grid = factory.SubFactory(GridFactory, space=factory.SelfAttribute("..space"))


class PriceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ResourcePrice

    space = factory.SelfAttribute("resource.space")
    day_rate = Decimal("30.00")


class PackageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Package

    space = factory.SelfAttribute("resource.space")
    name = "Half day"
    duration_minutes = 240
