"""Development data: a platform with resources, users and bookings this week.

Refuses to run with DEBUG off so it never touches a production database.
"""

import datetime as dt
import zoneinfo
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.booking import services as booking
from apps.clients.models import Client, Package, PackagePrice, PricingGrid, ResourcePrice
from apps.core.models import InstanceSettings, Role, Space, SpaceMembership
from apps.resources.models import (
    ALL_WEEKDAYS,
    Access,
    Area,
    Authorization,
    Resource,
    ResourceCategory,
)

PASSWORD = "demo-password-123"  # noqa: S105 - development data only


class Command(BaseCommand):
    help = "Load demo data (DEBUG only). Users: admin, manager, alice, bob / " + PASSWORD

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        if not settings.DEBUG:
            raise CommandError("Demo data is only for development (DEBUG=True).")
        if Space.objects.filter(slug="imaging").exists():
            self.stdout.write("Demo data already loaded.")
            return

        def user(username: str, first: str, last: str, **kw: object) -> User:
            u = User.objects.create_user(
                username,
                f"{username}@example.org",
                PASSWORD,
                first_name=first,
                last_name=last,
                **kw,
            )
            return u

        admin = user("admin", "Ada", "Admin", is_staff=True, is_superuser=True)
        manager = user("manager", "Marc", "Manager")
        alice = user("alice", "Alice", "Martin")
        bob = user("bob", "Bob", "Durand")

        space = Space.objects.create(
            name="Imagerie", slug="imaging", description="Plateforme de microscopie."
        )
        for u, role in [
            (admin, Role.ADMIN),
            (manager, Role.MANAGER),
            (alice, Role.USER),
            (bob, Role.USER),
        ]:
            SpaceMembership.objects.create(user=u, space=space, role=role)
        cyto = Space.objects.create(name="Cytométrie", slug="cytometry", accent_color="#0d9488")
        SpaceMembership.objects.create(user=admin, space=cyto, role=Role.ADMIN)

        lab = Area.objects.create(space=space, name="Salle 101")
        night = Area.objects.create(
            space=space,
            name="Salle 24/7",
            open_weekdays=ALL_WEEKDAYS,
            day_start=dt.time(0),
            day_end=dt.time(0),
            slot_minutes=60,
        )
        confocal = ResourceCategory.objects.create(space=space, name="Confocaux")
        wide = ResourceCategory.objects.create(space=space, name="Champ large")
        r1 = Resource.objects.create(
            space=space,
            name="Zeiss LSM 980",
            brand="Zeiss",
            model="LSM 980",
            category=confocal,
            area=lab,
            color="#4f46e5",
            access=Access.AUTHORIZED,
            max_duration_minutes=240,
            edit_deadline_hours=24,
        )
        r2 = Resource.objects.create(
            space=space,
            name="Nikon Ti2",
            brand="Nikon",
            model="Ti2-E",
            category=wide,
            area=lab,
            color="#0891b2",
        )
        r3 = Resource.objects.create(
            space=space,
            name="Lightsheet",
            category=wide,
            area=night,
            color="#c026d3",
        )
        r1.managers.add(manager)
        Authorization.objects.create(space=space, user=alice, category=confocal, granted_by=manager)

        academic = PricingGrid.objects.create(
            space=space, name="Académique", night_rate_enabled=True, weekend_rate_enabled=True
        )
        industry = PricingGrid.objects.create(space=space, name="Industriel", order=1)
        martin = Client.objects.create(
            space=space,
            name="Équipe Martin (UMR 1234)",
            organization="Université",
            pricing_grid=academic,
        )
        martin.users.add(alice, bob)
        startup = Client.objects.create(space=space, name="BioStart SAS", pricing_grid=industry)
        startup.users.add(bob)
        for res, day_rate, night_rate, weekend_rate, industry_rate in [
            (r1, "45.00", "60.00", "70.00", "180.00"),
            (r2, "20.00", "25.00", "30.00", "90.00"),
            (r3, "35.00", "35.00", "40.00", "150.00"),
        ]:
            ResourcePrice.objects.create(
                space=space,
                resource=res,
                grid=academic,
                day_rate=Decimal(day_rate),
                night_rate=Decimal(night_rate),
                weekend_rate=Decimal(weekend_rate),
            )
            ResourcePrice.objects.create(
                space=space, resource=res, grid=industry, day_rate=Decimal(industry_rate)
            )
        half_day = Package.objects.create(
            space=space, resource=r1, name="Demi-journée", duration_minutes=240
        )
        PackagePrice.objects.create(
            space=space, package=half_day, grid=academic, price=Decimal("150.00")
        )
        PackagePrice.objects.create(
            space=space, package=half_day, grid=industry, price=Decimal("600.00")
        )

        tz = zoneinfo.ZoneInfo(space.timezone)
        today = timezone.localdate(timezone=tz)
        # This week, or next week from Friday on, so the demo bookings lie ahead.
        monday = today - dt.timedelta(days=today.weekday())
        if today.weekday() >= 4:
            monday += dt.timedelta(days=7)

        def slot(day: int, h: int, length: int) -> tuple[dt.datetime, dt.datetime]:
            start = dt.datetime.combine(monday + dt.timedelta(days=day), dt.time(h), tzinfo=tz)
            return start, start + dt.timedelta(hours=length)

        for actor, res, (d, h, n), title in [
            (alice, r1, (1, 9, 3), "Imagerie cellules HeLa"),
            (bob, r2, (1, 14, 2), ""),
            (alice, r2, (3, 10, 2), "Time-lapse"),
            (manager, r1, (4, 9, 4), "Maintenance préventive"),
            (bob, r3, (2, 20, 10), "Acquisition nuit"),
        ]:
            start, end = slot(d, h, n)
            booking.reserve(
                manager,
                res,
                start,
                end,
                for_user=actor,
                client=martin if actor != manager else None,
                title=title,
                now=start - dt.timedelta(days=30),
            )

        from apps.services import services as svc
        from apps.services.models import Cabinet, Order, Project, ServiceItem, ServicePrice, Shelf

        prep = ServiceItem.objects.create(
            space=space, name="Préparation d'échantillon", unit="échantillon"
        )
        ServicePrice.objects.create(space=space, item=prep, grid=academic, price=Decimal("12.00"))
        ServicePrice.objects.create(space=space, item=prep, grid=industry, price=Decimal("40.00"))
        projects = []
        for name, client, start_m, length, status in [
            ("Atlas cérébral", martin, -2, 5, "active"),
            ("Criblage BioStart", startup, 0, 3, "active"),
            ("Formation imagerie 2027", martin, 2, 2, "planned"),
        ]:
            m = today.month - 1 + start_m
            start_d = dt.date(today.year + m // 12, m % 12 + 1, 1)
            e = m + length
            end_d = dt.date(today.year + e // 12, e % 12 + 1, 1) - dt.timedelta(days=1)
            projects.append(
                Project.objects.create(
                    space=space,
                    name=name,
                    client=client,
                    lead=manager,
                    start_date=start_d,
                    end_date=end_d,
                    status=status,
                )
            )
        order = Order.objects.create(
            space=space, client=martin, project=projects[0], title="Coupes série A", requester=alice
        )
        svc.add_line(order, prep, Decimal("6"), today)
        freezer = Cabinet.objects.create(
            space=space, name="Congélateur -80 °C", location="Salle 101"
        )
        rack = Shelf.objects.create(cabinet=freezer, name="Étagère 1", capacity=20)
        Shelf.objects.create(cabinet=freezer, name="Étagère 2", capacity=20)
        svc.store(rack, martin, "Cerveaux de souris fixés", 4)

        instance = InstanceSettings.load()
        instance.setup_completed_at = timezone.now()
        instance.save()
        self.stdout.write(
            self.style.SUCCESS(f"Demo loaded. Log in as admin / alice / bob, password {PASSWORD}")
        )
