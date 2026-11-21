"""Import from the old Platform-Manager database (README §12).

Order: spaces → users → memberships → pricing grids → clients → areas,
categories, resources → authorizations → prices and packages → bookings →
invoices (frozen, with their original numbers).

Idempotent: every imported row is recorded in ``LegacyIdMap``; a re-run
updates what changed and never duplicates. Legacy passwords are not
imported (users reset theirs). Anything odd goes into the report rather
than stopping the import.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import models, transaction
from django.utils.text import slugify

from apps.accounts.models import User
from apps.booking.models import Booking
from apps.booking.models import Status as BookingStatus
from apps.clients.models import Client, Package, PackagePrice, PricingGrid, ResourcePrice
from apps.core.models import Role, Space, SpaceMembership
from apps.invoices.models import Invoice, InvoiceLine, Kind, Status
from apps.legacy.models import LegacyIdMap
from apps.legacy.sources import Source
from apps.resources.models import ALL_WEEKDAYS, Area, Authorization, Resource, ResourceCategory
from apps.services.models import (
    Order,
    OrderLine,
    OrderStatus,
    Project,
    ProjectStatus,
    ServiceItem,
    ServicePrice,
)

LEGACY_ROLES = {1: Role.VISITOR, 2: Role.USER, 3: Role.MANAGER, 4: Role.ADMIN}
WEEKDAY_COLUMNS = (
    "is_monday",
    "is_tuesday",
    "is_wednesday",
    "is_thursday",
    "is_friday",
    "is_saturday",
    "is_sunday",
)


@dataclass
class Report:
    created: Counter[str] = field(default_factory=Counter)
    updated: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    anomalies: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.anomalies.append(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": dict(self.created),
            "updated": dict(self.updated),
            "skipped": dict(self.skipped),
            "anomalies": self.anomalies,
        }


def _live(row: dict[str, Any]) -> bool:
    return not row.get("deleted")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not value or str(value).startswith("0000"):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class Importer:
    def __init__(self, source: Source, space_ids: set[int] | None = None) -> None:
        self.source = source
        self.space_ids = space_ids  # None: every space
        self.report = Report()
        self._map: dict[tuple[str, int], int] = {
            (m.legacy_table, m.legacy_id): m.new_id for m in LegacyIdMap.objects.all()
        }

    # --- helpers -----------------------------------------------------------------

    def rows(self, table: str) -> list[dict[str, Any]]:
        if not self.source.has_table(table):
            self.report.warn(f"Table {table} not found: skipped.")
            return []
        rows = [r for r in self.source.rows(table) if _live(r)]
        if self.space_ids is not None and table != "core_users":
            rows = [r for r in rows if "id_space" not in r or _int(r["id_space"]) in self.space_ids]
        return rows

    def new_id(self, table: str, legacy_id: Any) -> int | None:
        return self._map.get((table, _int(legacy_id)))

    def get(self, model: Any, table: str, legacy_id: Any) -> Any:
        pk = self.new_id(table, legacy_id)
        return model.objects.filter(pk=pk).first() if pk else None

    def upsert(self, table: str, legacy_id: Any, model: Any, values: dict[str, Any]) -> Any:
        legacy_id = _int(legacy_id)
        pk = self._map.get((table, legacy_id))
        obj = model.objects.filter(pk=pk).first() if pk else None
        if obj is None:
            obj = model.objects.create(**values)
            self.report.created[table] += 1
        else:
            for key, value in values.items():
                setattr(obj, key, value)
            obj.save()
            self.report.updated[table] += 1
        self._remember(table, legacy_id, obj)
        return obj

    def _remember(self, table: str, legacy_id: int, obj: models.Model) -> None:
        LegacyIdMap.objects.update_or_create(
            legacy_table=table,
            legacy_id=legacy_id,
            defaults={"model": obj._meta.label, "new_id": obj.pk},
        )
        self._map[(table, legacy_id)] = obj.pk

    # --- steps -------------------------------------------------------------------------

    @transaction.atomic
    def run(self) -> Report:
        self.spaces()
        self.users()
        self.memberships()
        self.grids()
        self.clients()
        self.resources()
        self.authorizations()
        self.prices()
        self.bookings()
        self.services()
        self.invoices()
        return self.report

    def spaces(self) -> None:
        taken = set(Space.objects.values_list("slug", flat=True))
        for row in self.rows("core_spaces"):
            if self.space_ids is not None and _int(row["id"]) not in self.space_ids:
                continue
            existing = self.get(Space, "core_spaces", row["id"])
            slug = existing.slug if existing else self._unique_slug(row, taken)
            color = row.get("color") or "#4f46e5"
            self.upsert(
                "core_spaces",
                row["id"],
                Space,
                {
                    "name": (row.get("name") or slug)[:120],
                    "slug": slug,
                    "description": row.get("description") or "",
                    "accent_color": color if len(color) == 7 else "#4f46e5",
                },
            )

    def _unique_slug(self, row: dict[str, Any], taken: set[str]) -> str:
        base = slugify(row.get("shortname") or row.get("name") or f"space-{row['id']}")[:40]
        base = base or f"space-{row['id']}"
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = f"{base}-{n}"
        taken.add(slug)
        return slug

    def users(self) -> None:
        emails = {e.lower() for e in User.objects.values_list("email", flat=True)}
        usernames = set(User.objects.values_list("username", flat=True))
        wanted = None
        if self.space_ids is not None:
            wanted = {_int(r["id_user"]) for r in self.rows("core_j_spaces_user")}
        for row in self.rows("core_users"):
            legacy_id = _int(row["id"])
            if wanted is not None and legacy_id not in wanted:
                continue
            existing = self.get(User, "core_users", legacy_id)
            login = (row.get("login") or f"user{legacy_id}")[:150]
            email = (row.get("email") or "").strip().lower()
            if existing is None:
                if login in usernames:
                    self.report.warn(f"User {login}: login already taken, renamed {login}-legacy.")
                    login = f"{login}-legacy"
                if not email or email in emails:
                    self.report.warn(
                        f"User {login}: missing or duplicate e-mail, placeholder used."
                    )
                    email = f"{slugify(login) or legacy_id}.{legacy_id}@legacy.invalid"
            else:
                # Once imported, the account belongs to PFM-NG: a re-run never
                # overwrites it nor reactivates one that was deleted (GDPR).
                self.report.skipped["core_users"] += 1
                continue
            usernames.add(login)
            emails.add(email)
            user = self.upsert(
                "core_users",
                legacy_id,
                User,
                {
                    "username": login,
                    "email": email,
                    "first_name": (row.get("firstname") or "")[:150],
                    "last_name": (row.get("name") or "")[:150],
                    "is_active": bool(_int(row.get("is_active"), 1)),
                },
            )
            user.set_unusable_password()  # legacy hashes are not trusted (md5)
            user.save(update_fields=["password"])

    def memberships(self) -> None:
        for row in self.rows("core_j_spaces_user"):
            user = self.get(User, "core_users", row["id_user"])
            space = self.get(Space, "core_spaces", row["id_space"])
            role = LEGACY_ROLES.get(_int(row.get("status")))
            if not (user and space) or not user.is_active:
                self.report.skipped["core_j_spaces_user"] += 1
                continue
            if role is None:  # inactive or pending in the legacy system
                self.report.skipped["core_j_spaces_user"] += 1
                continue
            _obj, created = SpaceMembership.objects.update_or_create(
                user=user, space=space, defaults={"role": role}
            )
            (self.report.created if created else self.report.updated)["core_j_spaces_user"] += 1

    def grids(self) -> None:
        nightwe = {_int(r.get("id_belonging")): r for r in self.rows("bk_nightwe")}
        for row in self.rows("cl_pricings"):
            space = self.get(Space, "core_spaces", row["id_space"])
            if space is None:
                continue
            rules = nightwe.get(_int(row["id"]), {})
            unique = bool(_int(rules.get("tarif_unique"), 1))
            self.upsert(
                "cl_pricings",
                row["id"],
                PricingGrid,
                {
                    "space": space,
                    "name": (row.get("name") or f"Grid {row['id']}")[:120],
                    "order": _int(row.get("display_order")),
                    "night_rate_enabled": not unique and bool(_int(rules.get("tarif_night"))),
                    "night_start": dt.time(_int(rules.get("night_start"), 19) % 24),
                    "night_end": dt.time(_int(rules.get("night_end"), 8) % 24),
                    "weekend_rate_enabled": not unique and bool(_int(rules.get("tarif_we"))),
                    "weekend_days": self._weekend(rules.get("choice_we")),
                },
            )

    def _weekend(self, raw: Any) -> int:
        # Legacy stores seven 0/1 flags, Monday first: "0,0,0,0,0,1,1".
        flags = [p.strip() for p in str(raw or "").split(",")]
        if len(flags) != 7:
            return 0b1100000
        return sum(1 << i for i, flag in enumerate(flags) if flag == "1")

    def clients(self) -> None:
        addresses = {_int(r["id"]): r for r in self.rows("cl_addresses")}
        for row in self.rows("cl_clients"):
            space = self.get(Space, "core_spaces", row["id_space"])
            grid = self.get(PricingGrid, "cl_pricings", row.get("pricing"))
            if space is None:
                continue
            if grid is None:
                grid = PricingGrid.objects.filter(space=space).first()
                if grid is None:
                    grid = PricingGrid.objects.create(space=space, name="Default")
                self.report.warn(f"Client {row.get('name')}: no pricing grid, used {grid}.")
            address = addresses.get(_int(row.get("address_invoice")), {})
            lines = [
                address.get("institution"),
                address.get("building_floor"),
                address.get("service"),
                address.get("address"),
                " ".join(p for p in (address.get("zip_code"), address.get("city")) if p),
                address.get("country"),
            ]
            name = (row.get("name") or f"Client {row['id']}")[:200]
            if (
                not self.new_id("cl_clients", row["id"])
                and Client.objects.filter(space=space, name=name).exists()
            ):
                name = f"{name} ({row['id']})"[:200]
            self.upsert(
                "cl_clients",
                row["id"],
                Client,
                {
                    "space": space,
                    "name": name,
                    "pricing_grid": grid,
                    "contact_name": (row.get("contact_name") or "")[:200],
                    "email": (row.get("email") or "")[:254],
                    "phone": (row.get("phone") or "")[:40],
                    "invoice_address": "\n".join(str(p) for p in lines if p),
                },
            )
        for row in self.rows("cl_j_client_user"):
            client = self.get(Client, "cl_clients", row["id_client"])
            user = self.get(User, "core_users", row["id_user"])
            if client and user and user.is_active:
                client.users.add(user)

    def resources(self) -> None:
        schedules = {_int(r.get("id_rearea")): r for r in self.rows("bk_schedulings")}
        for row in self.rows("re_area"):
            space = self.get(Space, "core_spaces", row["id_space"])
            if space is None:
                continue
            sched = schedules.get(_int(row["id"]))
            values: dict[str, Any] = {
                "space": space,
                "name": (row.get("name") or f"Area {row['id']}")[:120],
                "is_restricted": bool(_int(row.get("restricted"))),
            }
            if sched:
                begin, end = _int(sched.get("day_begin"), 8), _int(sched.get("day_end"), 18)
                values.update(
                    open_weekdays=sum(
                        1 << i for i, col in enumerate(WEEKDAY_COLUMNS) if _int(sched.get(col), 1)
                    )
                    or ALL_WEEKDAYS,
                    day_start=dt.time(begin % 24),
                    day_end=dt.time(0) if end >= 24 else dt.time(end),
                    slot_minutes=max(5, _int(sched.get("size_bloc_resa"), 3600) // 60),
                )
            self.upsert("re_area", row["id"], Area, values)
        for row in self.rows("re_category"):
            space = self.get(Space, "core_spaces", row["id_space"])
            if space:
                self.upsert(
                    "re_category",
                    row["id"],
                    ResourceCategory,
                    {"space": space, "name": (row.get("name") or f"Category {row['id']}")[:120]},
                )
        for row in self.rows("re_info"):
            space = self.get(Space, "core_spaces", row["id_space"])
            area = self.get(Area, "re_area", row.get("id_area"))
            category = self.get(ResourceCategory, "re_category", row.get("id_category"))
            if not (space and area and category):
                self.report.warn(f"Resource {row.get('name')}: missing area or category, skipped.")
                self.report.skipped["re_info"] += 1
                continue
            description = "\n\n".join(
                p for p in (row.get("description"), row.get("long_description")) if p
            )
            self.upsert(
                "re_info",
                row["id"],
                Resource,
                {
                    "space": space,
                    "name": (row.get("name") or f"Resource {row['id']}")[:120],
                    "brand": (row.get("brand") or "")[:120],
                    "model": (row.get("type") or "")[:120],
                    "description": description,
                    "area": area,
                    "category": category,
                    "order": _int(row.get("display_order")),
                },
            )

    def authorizations(self) -> None:
        latest: dict[tuple[int, int], dict[str, Any]] = {}
        for row in self.rows("bk_authorization"):
            key = (_int(row.get("user_id")), _int(row.get("resource_id")))  # resource_id = category
            if key not in latest or (_date(row.get("date")) or dt.date.min) >= (
                _date(latest[key].get("date")) or dt.date.min
            ):
                latest[key] = row
        for row in latest.values():
            user = self.get(User, "core_users", row.get("user_id"))
            category = self.get(ResourceCategory, "re_category", row.get("resource_id"))
            if not (user and category):
                self.report.skipped["bk_authorization"] += 1
                continue
            ended = _date(row.get("date_desactivation"))
            auth, created = Authorization.objects.update_or_create(
                user=user,
                category=category,
                defaults={
                    "space": category.space,
                    "granted_on": _date(row.get("date")) or dt.date(2000, 1, 1),
                    "expires_on": ended,
                    "is_active": bool(_int(row.get("is_active"), 1)) and ended is None,
                },
            )
            self._remember("bk_authorization", _int(row["id"]), auth)
            (self.report.created if created else self.report.updated)["bk_authorization"] += 1

    def prices(self) -> None:
        rates: dict[tuple[int, int], dict[str, Decimal]] = defaultdict(dict)
        for row in self.rows("bk_prices"):
            if _int(row.get("id_package")):
                continue  # package prices live in bk_j_packages_prices
            price = _decimal(row.get("price"))
            period = str(row.get("day_night_we") or "day")
            if price is not None and period in {"day", "night", "we"}:
                rates[(_int(row.get("id_resource")), _int(row.get("id_belonging")))][period] = price
        for (resource_id, grid_id), values in rates.items():
            resource = self.get(Resource, "re_info", resource_id)
            grid = self.get(PricingGrid, "cl_pricings", grid_id)
            if not (resource and grid) or "day" not in values:
                self.report.skipped["bk_prices"] += 1
                continue
            _obj, created = ResourcePrice.objects.update_or_create(
                resource=resource,
                grid=grid,
                defaults={
                    "space": resource.space,
                    "day_rate": values["day"],
                    "night_rate": values.get("night"),
                    "weekend_rate": values.get("we"),
                },
            )
            (self.report.created if created else self.report.updated)["bk_prices"] += 1
        for row in self.rows("bk_packages"):
            resource = self.get(Resource, "re_info", row.get("id_resource"))
            if resource is None:
                continue
            minutes = int((_decimal(row.get("duration")) or Decimal(1)) * 60)
            self.upsert(
                "bk_packages",
                row["id"],
                Package,
                {
                    "space": resource.space,
                    "resource": resource,
                    "name": (row.get("name") or "Package")[:120],
                    "duration_minutes": max(minutes, 1),
                },
            )
        for row in self.rows("bk_j_packages_prices"):
            package = self.get(Package, "bk_packages", row.get("id_package"))
            grid = self.get(PricingGrid, "cl_pricings", row.get("id_pricing"))
            price = _decimal(row.get("price"))
            if package and grid and price is not None:
                PackagePrice.objects.update_or_create(
                    package=package, grid=grid, defaults={"space": package.space, "price": price}
                )

    def bookings(self) -> None:
        busy: dict[int, list[tuple[dt.datetime, dt.datetime]]] = defaultdict(list)
        for row in sorted(self.rows("bk_calendar_entry"), key=lambda r: _int(r["id"])):
            resource = self.get(Resource, "re_info", row.get("resource_id"))
            user = self.get(User, "core_users", row.get("recipient_id")) or self.get(
                User, "core_users", row.get("booked_by_id")
            )
            if not (resource and user):
                self.report.skipped["bk_calendar_entry"] += 1
                continue
            start = dt.datetime.fromtimestamp(_int(row.get("start_time")), tz=dt.UTC)
            end = dt.datetime.fromtimestamp(_int(row.get("end_time")), tz=dt.UTC)
            if end <= start:
                self.report.warn(f"Booking {row['id']}: end before start, skipped.")
                self.report.skipped["bk_calendar_entry"] += 1
                continue
            status = BookingStatus.CONFIRMED
            if any(s < end and start < e for s, e in busy[resource.pk]):
                status = BookingStatus.CANCELLED
                self.report.warn(
                    f"Booking {row['id']} on {resource}: overlaps an earlier one, "
                    "imported as cancelled."
                )
            else:
                busy[resource.pk].append((start, end))
            self.upsert(
                "bk_calendar_entry",
                row["id"],
                Booking,
                {
                    "space": resource.space,
                    "resource": resource,
                    "user": user,
                    "created_by": self.get(User, "core_users", row.get("booked_by_id")) or user,
                    "start": start,
                    "end": end,
                    "title": (row.get("short_description") or "")[:200],
                    "note": row.get("full_description") or "",
                    "status": status,
                    "client": self.get(Client, "cl_clients", row.get("responsible_id")),
                    "package": self.get(Package, "bk_packages", row.get("package_id")),
                    # Invoiced by the legacy system: never bill it again.
                    "billed_externally": _int(row.get("invoice_id")) > 0,
                },
            )

    def services(self) -> None:
        for row in self.rows("se_services"):
            space = self.get(Space, "core_spaces", row["id_space"])
            if space is None:
                continue
            name = (row.get("name") or f"Service {row['id']}")[:200]
            if (
                not self.new_id("se_services", row["id"])
                and ServiceItem.objects.filter(space=space, name=name).exists()
            ):
                name = f"{name} ({row['id']})"[:200]
            self.upsert(
                "se_services",
                row["id"],
                ServiceItem,
                {
                    "space": space,
                    "name": name,
                    "description": row.get("description") or "",
                    "order": _int(row.get("display_order")),
                    "is_active": bool(_int(row.get("is_active"), 1)),
                },
            )
        for row in self.rows("se_prices"):
            item = self.get(ServiceItem, "se_services", row.get("id_service"))
            grid = self.get(PricingGrid, "cl_pricings", row.get("id_belonging"))
            price = _decimal(row.get("price"))
            if item and grid and price is not None:
                ServicePrice.objects.update_or_create(
                    item=item, grid=grid, defaults={"space": item.space, "price": price}
                )
                self.report.created["se_prices"] += 1
        self._orders()
        self._projects()

    def _order_lines(
        self,
        order: Order,
        rows: list[dict[str, Any]],
        table: str,
        default_day: dt.date,
        billed: bool,
    ) -> None:
        for row in rows:
            item = self.get(ServiceItem, "se_services", row.get("id_service"))
            quantity = _decimal(row.get("quantity"))
            if item is None or quantity is None or quantity <= 0:
                self.report.skipped[table] += 1
                continue
            self.upsert(
                table,
                row["id"],
                OrderLine,
                {
                    "order": order,
                    "item": item,
                    "quantity": quantity,
                    "performed_on": _date(row.get("date")) or default_day,
                    "comment": (row.get("comment") or "")[:200],
                    "billed_externally": billed or _int(row.get("id_invoice")) > 0,
                },
            )

    def _orders(self) -> None:
        lines: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in self.rows("se_order_service"):
            lines[_int(row.get("id_order"))].append(row)
        for row in self.rows("se_order"):
            space = self.get(Space, "core_spaces", row["id_space"])
            client = self.get(Client, "cl_clients", row.get("id_resp"))
            if not (space and client):
                self.report.skipped["se_order"] += 1
                continue
            opened = _date(row.get("date_open")) or dt.date(2000, 1, 1)
            closed = _date(row.get("date_close"))
            order = self.upsert(
                "se_order",
                row["id"],
                Order,
                {
                    "space": space,
                    "client": client,
                    "requester": self.get(User, "core_users", row.get("id_user")),
                    "title": (row.get("no_identification") or f"Order {row['id']}")[:200],
                    "status": OrderStatus.DONE if closed else OrderStatus.OPEN,
                    "closed_at": dt.datetime.combine(closed, dt.time(12), tzinfo=dt.UTC)
                    if closed
                    else None,
                },
            )
            self._order_lines(
                order,
                lines.get(_int(row["id"]), []),
                "se_order_service",
                opened,
                billed=_int(row.get("id_invoice")) > 0,
            )

    def _projects(self) -> None:
        lines: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in self.rows("se_project_service"):
            lines[_int(row.get("id_project"))].append(row)
        for row in self.rows("se_project"):
            space = self.get(Space, "core_spaces", row["id_space"])
            client = self.get(Client, "cl_clients", row.get("id_resp"))
            if not (space and client):
                self.report.skipped["se_project"] += 1
                continue
            start = _date(row.get("date_open")) or dt.date(2000, 1, 1)
            closed = _date(row.get("date_close"))
            end = max(closed or _date(row.get("time_limit")) or start, start)
            name = (row.get("name") or f"Project {row['id']}")[:200]
            project = self.upsert(
                "se_project",
                row["id"],
                Project,
                {
                    "space": space,
                    "name": name,
                    "client": client,
                    "lead": self.get(User, "core_users", row.get("in_charge")),
                    "start_date": start,
                    "end_date": end,
                    "status": ProjectStatus.CLOSED if closed else ProjectStatus.ACTIVE,
                    "description": row.get("samplescomment") or "",
                },
            )
            # Legacy projects hold service lines directly; here they live in an order.
            order = self.upsert(
                "se_project_order",
                row["id"],
                Order,
                {
                    "space": space,
                    "client": client,
                    "project": project,
                    "title": name,
                    "status": OrderStatus.DONE if closed else OrderStatus.OPEN,
                },
            )
            self._order_lines(
                order, lines.get(_int(row["id"]), []), "se_project_service", start, False
            )

    def invoices(self) -> None:
        items: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in self.rows("in_invoice_item"):
            items[_int(row.get("id_invoice"))].append(row)
        importer = User.objects.filter(is_superuser=True).order_by("pk").first()
        for row in self.rows("in_invoice"):
            if self.new_id("in_invoice", row["id"]):
                self.report.skipped["in_invoice"] += 1  # frozen: never re-imported
                continue
            space = self.get(Space, "core_spaces", row["id_space"])
            client = self.get(Client, "cl_clients", row.get("id_responsible"))
            if not (space and client and importer):
                self.report.warn(f"Invoice {row.get('number')}: missing space or client, skipped.")
                self.report.skipped["in_invoice"] += 1
                continue
            number = str(row.get("number") or f"L{row['id']}")[:30]
            if Invoice.objects.filter(space=space, number=number).exists():
                number = f"L-{number}"[:30]
            total = _decimal(row.get("total_ht")) or Decimal("0.00")
            issued = _date(row.get("date_generated")) or _date(row.get("period_end"))
            invoice = Invoice.objects.create(
                space=space,
                kind=Kind.INVOICE,
                client=client,
                number=number,
                period_start=_date(row.get("period_begin")) or issued or dt.date(2000, 1, 1),
                period_end=_date(row.get("period_end")) or issued or dt.date(2000, 1, 1),
                issue_date=issued,
                due_date=issued,
                client_name=client.name,
                client_address=client.invoice_address,
                currency=space.currency,
                vat_rate=Decimal("0"),
                total_excl_tax=total,
                total_tax=Decimal("0.00"),
                total_incl_tax=total,
                paid_at=_date(row.get("date_paid")) if _int(row.get("is_paid")) else None,
                note=(row.get("title") or "")
                + "\nImported from the previous system: amounts excluding tax.",
                created_by=importer,
                issued_by=importer,
            )
            lines = [
                InvoiceLine(
                    invoice=invoice,
                    position=i,
                    description=(item.get("details") or item.get("module") or "—")[:300],
                    quantity=Decimal(1),
                    unit_price=_decimal(item.get("total_ht")) or Decimal("0.00"),
                    amount=_decimal(item.get("total_ht")) or Decimal("0.00"),
                )
                for i, item in enumerate(items.get(_int(row["id"]), []))
            ]
            gap = total - sum((line.amount for line in lines), Decimal("0.00"))
            if gap:
                lines.append(
                    InvoiceLine(
                        invoice=invoice,
                        position=len(lines),
                        description="Adjustment (discount or rounding in the previous system)",
                        quantity=Decimal(1),
                        unit_price=gap,
                        amount=gap,
                    )
                )
            InvoiceLine.objects.bulk_create(lines)
            # Freeze last: issued invoices refuse further changes.
            Invoice.objects.filter(pk=invoice.pk).update(status=Status.ISSUED)
            self._remember("in_invoice", _int(row["id"]), invoice)
            self.report.created["in_invoice"] += 1
