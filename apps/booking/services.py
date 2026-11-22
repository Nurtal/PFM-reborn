"""Booking business rules.

Two layers:

* ``check_slot`` is a pure function: given a resource's rules and a slot, it
  lists the rule violations. Exhaustively unit-tested (midnight, DST, ...).
* ``reserve`` / ``modify`` / ``cancel`` are the only way to write bookings.
  They check permissions and rules, then check overlaps and write in the same
  transaction. On SQLite the transaction is ``BEGIN IMMEDIATE`` (see settings)
  so concurrent writers are serialised; on PostgreSQL the resource row is
  locked and an exclusion constraint backs it up.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from collections.abc import Iterator
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.accounts.models import User
from apps.booking.models import Booking, Closure, Status
from apps.clients.models import Client, Package
from apps.core.models import Role, user_role
from apps.resources.models import Access, Area, Resource
from apps.resources.models import Authorization as ResourceAuthorization

MIDNIGHT = dt.time(0)


class BookingError(Exception):
    """A booking request was refused. ``messages`` are user-facing reasons."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


# --- Pure rules -------------------------------------------------------------------


@dataclass(frozen=True)
class SlotRules:
    tz: dt.tzinfo
    open_weekdays: int
    day_start: dt.time
    day_end: dt.time  # 00:00 means midnight at the end of the day
    slot_minutes: int
    min_duration: dt.timedelta = dt.timedelta(0)
    max_duration: dt.timedelta | None = None
    max_advance: dt.timedelta | None = None

    @classmethod
    def for_resource(cls, resource: Resource) -> SlotRules:
        area: Area = resource.area
        tz = zoneinfo.ZoneInfo(resource.space.timezone)
        return cls(
            tz=tz,
            open_weekdays=area.open_weekdays,
            day_start=area.day_start,
            day_end=area.day_end,
            slot_minutes=area.slot_minutes,
            min_duration=dt.timedelta(minutes=resource.min_duration_minutes),
            max_duration=(
                dt.timedelta(minutes=resource.max_duration_minutes)
                if resource.max_duration_minutes is not None
                else None
            ),
            max_advance=(
                dt.timedelta(days=resource.max_advance_days)
                if resource.max_advance_days is not None
                else None
            ),
        )

    def is_open_on(self, day: dt.date) -> bool:
        return bool(self.open_weekdays & (1 << day.weekday()))

    def opening_window(self, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
        """Aware [open, close) datetimes of ``day`` in the space time zone."""
        opens = dt.datetime.combine(day, self.day_start, tzinfo=self.tz)
        if self.day_end == MIDNIGHT:
            closes = dt.datetime.combine(day + dt.timedelta(days=1), MIDNIGHT, tzinfo=self.tz)
        else:
            closes = dt.datetime.combine(day, self.day_end, tzinfo=self.tz)
        return opens, closes


def _local_days(start: dt.datetime, end: dt.datetime, tz: dt.tzinfo) -> Iterator[dt.date]:
    day = start.astimezone(tz).date()
    last = (end - dt.timedelta(microseconds=1)).astimezone(tz).date()
    while day <= last:
        yield day
        day += dt.timedelta(days=1)


def _on_grid(moment: dt.datetime, rules: SlotRules) -> bool:
    local = moment.astimezone(rules.tz)
    if local.second or local.microsecond:
        return False
    minutes = (local.hour * 60 + local.minute) - (
        rules.day_start.hour * 60 + rules.day_start.minute
    )
    return minutes % rules.slot_minutes == 0


def check_slot(
    rules: SlotRules, start: dt.datetime, end: dt.datetime, now: dt.datetime
) -> list[str]:
    """Return why ``[start, end)`` breaks the rules (empty list: it is fine).

    Durations are real elapsed time, so a booking across a DST change is
    measured in actual hours. Opening hours are wall-clock times of the space.
    """
    if timezone.is_naive(start) or timezone.is_naive(end):
        raise ValueError("start and end must be timezone-aware")
    # Python subtracts/compares datetimes sharing a tzinfo by wall clock, which
    # is wrong across DST changes. Work in UTC; local times only for display rules.
    start, end, now = (v.astimezone(dt.UTC) for v in (start, end, now))
    errors: list[str] = []
    if end <= start:
        return [_("The end must be after the start.")]
    if start < now:
        errors.append(_("You cannot book in the past."))
    if rules.max_advance is not None and start > now + rules.max_advance:
        errors.append(
            ngettext(
                "This resource can be booked at most %(days)d day ahead.",
                "This resource can be booked at most %(days)d days ahead.",
                rules.max_advance.days,
            )
            % {"days": rules.max_advance.days}
        )

    duration = end - start
    if duration < rules.min_duration:
        errors.append(
            _("The minimum duration is %(minutes)d minutes.")
            % {"minutes": int(rules.min_duration.total_seconds() // 60)}
        )
    if rules.max_duration is not None and duration > rules.max_duration:
        errors.append(
            _("The maximum duration is %(minutes)d minutes.")
            % {"minutes": int(rules.max_duration.total_seconds() // 60)}
        )

    if not (_on_grid(start, rules) and _on_grid(end, rules)):
        errors.append(
            _("Bookings follow %(minutes)d-minute slots.") % {"minutes": rules.slot_minutes}
        )

    # Every part of the booking must fall inside opening hours. A booking can
    # span several days only if the area is open around the clock in between.
    for day in _local_days(start, end, rules.tz):
        if not rules.is_open_on(day):
            errors.append(_("The resource is closed on %(day)s.") % {"day": day.isoformat()})
            break
        opens, closes = rules.opening_window(day)
        midnight = dt.datetime.combine(day, MIDNIGHT, tzinfo=rules.tz)
        next_midnight = dt.datetime.combine(day + dt.timedelta(days=1), MIDNIGHT, tzinfo=rules.tz)
        segment_start, segment_end = max(start, midnight), min(end, next_midnight)
        if segment_start < opens or segment_end > closes:
            errors.append(_("The booking is outside opening hours."))
            break
    return errors


# --- Permissions --------------------------------------------------------------


def is_resource_manager(user: User, resource: Resource) -> bool:
    if user.is_superuser:
        return True
    role = user_role(user, resource.space)
    if role is not None and role >= Role.MANAGER:
        return True
    return resource.managers.filter(pk=user.pk).exists()


def booking_refusals(user: User, resource: Resource, on: dt.date | None = None) -> list[str]:
    """Why ``user`` may not book ``resource`` at all (empty list: allowed)."""
    if not resource.is_active or not resource.space.is_active:
        return [_("This resource is not available.")]
    if is_resource_manager(user, resource):
        return []
    role = user_role(user, resource.space)
    if role is None or role < Role.USER:
        return [_("Your account cannot book on this platform.")]
    if resource.access == Access.MANAGERS:
        return [_("Only managers can book this resource.")]
    if resource.access == Access.AUTHORIZED:
        authorized = (
            ResourceAuthorization.objects.for_space(resource.space)
            .valid(on)
            .filter(user=user, category_id=resource.category_id)
            .exists()
        )
        if not authorized:
            return [
                _("You need an authorization for “%(category)s” to book this resource.")
                % {"category": resource.category}
            ]
    return []


def can_book(user: User, resource: Resource) -> bool:
    return not booking_refusals(user, resource)


def can_change(user: User, booking: Booking, now: dt.datetime | None = None) -> bool:
    """Owners may change or cancel until the resource's deadline; managers always."""
    if booking.is_cancelled:
        return False
    if is_resource_manager(user, booking.resource):
        return True
    if booking.user_id != user.pk and booking.created_by_id != user.pk:
        return False
    now = now or timezone.now()
    deadline = booking.start - dt.timedelta(hours=booking.resource.edit_deadline_hours)
    return now < deadline


# --- Writes -------------------------------------------------------------------------


def _conflicts(
    resource: Resource, start: dt.datetime, end: dt.datetime, exclude: int | None
) -> list[str]:
    overlap = Q(start__lt=end, end__gt=start)
    bookings = Booking.objects.filter(overlap, resource=resource, status=Status.CONFIRMED)
    if exclude is not None:
        bookings = bookings.exclude(pk=exclude)
    errors = []
    if bookings.exists():
        errors.append(_("This slot overlaps another booking."))
    closures = Closure.objects.filter(overlap).filter(
        Q(resource=resource) | Q(area_id=resource.area_id, resource__isnull=True)
    )
    closure = closures.first()
    if closure is not None:
        errors.append(_("The resource is unavailable: %(reason)s.") % {"reason": closure.reason})
    return errors


def _quota_errors(
    resource: Resource, user: User, start: dt.datetime, exclude: int | None
) -> list[str]:
    limit = resource.max_bookings_per_day
    if limit is None:
        return []
    tz = zoneinfo.ZoneInfo(resource.space.timezone)
    day = start.astimezone(tz).date()
    day_start = dt.datetime.combine(day, MIDNIGHT, tzinfo=tz)
    day_end = day_start + dt.timedelta(days=1)
    qs = Booking.objects.filter(
        resource=resource,
        user=user,
        status=Status.CONFIRMED,
        start__gte=day_start,
        start__lt=day_end,
    )
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    if qs.count() >= limit:
        return [
            ngettext(
                "You can book this resource at most %(n)d time per day.",
                "You can book this resource at most %(n)d times per day.",
                limit,
            )
            % {"n": limit}
        ]
    return []


def _lock(resource: Resource) -> None:
    # PostgreSQL/MySQL: serialise writers per resource. SQLite already holds the
    # database write lock (BEGIN IMMEDIATE), and ignores FOR UPDATE.
    Resource.objects.select_for_update().filter(pk=resource.pk).first()


def _validate(
    actor: User,
    resource: Resource,
    user: User,
    start: dt.datetime,
    end: dt.datetime,
    now: dt.datetime,
    exclude: int | None,
) -> None:
    manager = is_resource_manager(actor, resource)
    if user.pk != actor.pk and not manager:
        raise BookingError([_("Only managers can book for someone else.")])
    errors = booking_refusals(user, resource)
    if errors:
        raise BookingError(errors)
    if manager:
        # Managers bypass rules but never physics: no overlaps, no reversed slots.
        if end <= start:
            raise BookingError([_("The end must be after the start.")])
    else:
        errors = check_slot(SlotRules.for_resource(resource), start, end, now)
        errors += _quota_errors(resource, user, start, exclude)
        if errors:
            raise BookingError(errors)
    errors = _conflicts(resource, start, end, exclude)
    if errors:
        raise BookingError(errors)


def _validate_billing(
    actor: User, user: User, resource: Resource, client: Client | None, package: Package | None
) -> None:
    from apps.clients.services import clients_for

    errors = []
    if client is None:
        if clients_for(user, resource.space).exists() and not is_resource_manager(actor, resource):
            errors.append(_("Choose the client account to bill."))
    elif not (
        clients_for(actor, resource.space).filter(pk=client.pk).exists()
        and (
            clients_for(user, resource.space).filter(pk=client.pk).exists()
            or is_resource_manager(actor, resource)
        )
    ):
        errors.append(_("You cannot bill this client account."))
    if package is not None and (package.resource_id != resource.pk or not package.is_active):
        errors.append(_("This package does not apply to this resource."))
    if errors:
        raise BookingError(errors)


def reserve(
    actor: User,
    resource: Resource,
    start: dt.datetime,
    end: dt.datetime,
    *,
    for_user: User | None = None,
    client: Client | None = None,
    package: Package | None = None,
    title: str = "",
    note: str = "",
    now: dt.datetime | None = None,
) -> Booking:
    user = for_user or actor
    now = now or timezone.now()
    start, end = start.astimezone(dt.UTC), end.astimezone(dt.UTC)
    with transaction.atomic():
        _lock(resource)
        _validate(actor, resource, user, start, end, now, exclude=None)
        _validate_billing(actor, user, resource, client, package)
        booking = Booking.objects.create(
            client=client,
            package=package,
            space=resource.space,
            resource=resource,
            user=user,
            created_by=actor,
            start=start,
            end=end,
            title=title,
            note=note,
        )
    _notify(booking, "created")
    return booking


def modify(
    actor: User,
    booking: Booking,
    start: dt.datetime,
    end: dt.datetime,
    *,
    title: str | None = None,
    note: str | None = None,
    client: Client | object | None = ...,
    package: Package | object | None = ...,
    now: dt.datetime | None = None,
) -> Booking:
    """Change a booking. ``client``/``package`` left as ``...`` stay unchanged."""
    now = now or timezone.now()
    if not can_change(actor, booking, now):
        raise BookingError([_("This booking can no longer be changed.")])
    start, end = start.astimezone(dt.UTC), end.astimezone(dt.UTC)
    with transaction.atomic():
        _lock(booking.resource)
        _validate(actor, booking.resource, booking.user, start, end, now, exclude=booking.pk)
        new_client = booking.client if client is ... else client
        new_package = booking.package if package is ... else package
        _validate_billing(actor, booking.user, booking.resource, new_client, new_package)  # type: ignore[arg-type]
        booking.client, booking.package = new_client, new_package  # type: ignore[assignment]
        booking.start, booking.end = start, end
        if title is not None:
            booking.title = title
        if note is not None:
            booking.note = note
        booking.save()
    _notify(booking, "modified")
    return booking


def cancel(actor: User, booking: Booking, *, now: dt.datetime | None = None) -> Booking:
    now = now or timezone.now()
    if not can_change(actor, booking, now):
        raise BookingError([_("This booking can no longer be cancelled.")])
    booking.status = Status.CANCELLED
    booking.cancelled_at = now
    booking.cancelled_by = actor
    booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
    _notify(booking, "cancelled")
    return booking


def _notify(booking: Booking, event: str) -> None:
    from apps.booking import tasks

    transaction.on_commit(lambda: tasks.notify_booking(booking.pk, event))
