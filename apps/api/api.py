"""REST API v1 (django-ninja, OpenAPI at /api/v1/docs).

Same rules as the web UI: permissions come from django-rules, writes go
through the business services, foreign spaces answer 404.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, cast

from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.api.auth import TokenAuth
from apps.booking import services as booking_services
from apps.booking.models import Booking
from apps.booking.models import Status as BookingStatus
from apps.clients.models import Client
from apps.core.models import Space
from apps.core.services import spaces_for
from apps.invoices.models import Invoice, Kind
from apps.invoices.models import Status as InvoiceStatus
from apps.resources.views import visible_resources

api = NinjaAPI(
    title="PFM-NG API",
    version="1",
    auth=TokenAuth(),
    urls_namespace="api",
    # Swagger UI loads its scripts from a CDN, which the CSP forbids: only the
    # OpenAPI schema is served (/api/v1/openapi.json), for client generators.
    docs_url=None,
)

MAX_RANGE = dt.timedelta(days=62)


def _user(request: HttpRequest) -> User:
    return cast(User, request.user)


def _space(request: HttpRequest, slug: str, perm: str = "core.view_space") -> Space:
    space = Space.objects.filter(slug=slug).first()
    if space is None or not request.user.has_perm(perm, space):
        raise Http404
    return space


# --- Schemas -------------------------------------------------------------------------


class MeOut(Schema):
    username: str
    email: str
    first_name: str
    last_name: str


class SpaceOut(Schema):
    slug: str
    name: str
    timezone: str
    currency: str


class ResourceOut(Schema):
    id: uuid.UUID
    name: str
    category: str
    area: str
    is_active: bool


class BookingOut(Schema):
    id: uuid.UUID
    resource: uuid.UUID
    start: dt.datetime
    end: dt.datetime
    mine: bool
    title: str | None = None
    user: str | None = None


class BookingIn(Schema):
    resource: uuid.UUID
    start: dt.datetime
    end: dt.datetime
    title: str = ""
    note: str = ""
    client: uuid.UUID | None = None


class InvoiceOut(Schema):
    id: uuid.UUID
    kind: str
    number: str | None
    client: str
    issue_date: dt.date | None
    total_excl_tax: Decimal
    total_incl_tax: Decimal
    paid_at: dt.date | None


class ErrorOut(Schema):
    detail: str
    errors: list[str] = []


# --- Endpoints ---------------------------------------------------------------------


@api.get("/me", response=MeOut, tags=["account"])
def me(request: HttpRequest) -> Any:
    return _user(request)


@api.get("/spaces", response=list[SpaceOut], tags=["spaces"])
def list_spaces(request: HttpRequest) -> Any:
    return spaces_for(_user(request))


@api.get("/spaces/{slug}/resources", response=list[ResourceOut], tags=["booking"])
def list_resources(request: HttpRequest, slug: str) -> Any:
    space = _space(request, slug)
    return [
        {
            "id": r.public_id,
            "name": r.name,
            "category": r.category.name,
            "area": r.area.name,
            "is_active": r.is_active,
        }
        for r in visible_resources(request.user, space)
    ]


def _booking_out(b: Booking, viewer: User, manager: bool) -> dict[str, Any]:
    mine = b.user_id == viewer.pk
    visible = mine or manager
    return {
        "id": b.public_id,
        "resource": b.resource.public_id,
        "start": b.start,
        "end": b.end,
        "mine": mine,
        "title": b.title if visible else None,
        "user": str(b.user) if visible else None,
    }


@api.get("/spaces/{slug}/bookings", response=list[BookingOut], tags=["booking"])
def list_bookings(
    request: HttpRequest,
    slug: str,
    start: dt.datetime,
    end: dt.datetime,
    resource: uuid.UUID | None = None,
) -> Any:
    space = _space(request, slug)
    if end <= start or end - start > MAX_RANGE:
        raise HttpError(400, "The range must be positive and at most 62 days.")
    resources = visible_resources(request.user, space)
    if resource is not None:
        resources = resources.filter(public_id=resource)
    qs = Booking.objects.filter(
        resource__in=resources, status=BookingStatus.CONFIRMED, start__lt=end, end__gt=start
    ).select_related("resource", "user")
    user = _user(request)
    manager = bool(request.user.has_perm("core.manage_space", space))
    return [_booking_out(b, user, manager) for b in qs.order_by("start")]


@api.post(
    "/spaces/{slug}/bookings",
    response={201: BookingOut, 400: ErrorOut},
    tags=["booking"],
)
def create_booking(request: HttpRequest, slug: str, payload: BookingIn) -> Any:
    space = _space(request, slug)
    resource = visible_resources(request.user, space).filter(public_id=payload.resource).first()
    if resource is None:
        raise Http404
    client = None
    if payload.client is not None:
        client = Client.objects.filter(space=space, public_id=payload.client).first()
        if client is None:
            return 400, {"detail": "Unknown client.", "errors": []}
    if timezone.is_naive(payload.start) or timezone.is_naive(payload.end):
        return 400, {"detail": "Datetimes must include a time zone offset.", "errors": []}
    try:
        b = booking_services.reserve(
            _user(request),
            resource,
            payload.start,
            payload.end,
            client=client,
            title=payload.title,
            note=payload.note,
        )
    except booking_services.BookingError as exc:
        return 400, {"detail": "Booking refused.", "errors": exc.messages}
    return 201, _booking_out(b, _user(request), True)


@api.delete(
    "/spaces/{slug}/bookings/{booking_id}", response={204: None, 400: ErrorOut}, tags=["booking"]
)
def cancel_booking(request: HttpRequest, slug: str, booking_id: uuid.UUID) -> Any:
    space = _space(request, slug)
    b = get_object_or_404(Booking.objects.for_space(space), public_id=booking_id)
    user = _user(request)
    if b.user_id != user.pk and not booking_services.is_resource_manager(user, b.resource):
        raise Http404
    try:
        booking_services.cancel(user, b)
    except booking_services.BookingError as exc:
        return 400, {"detail": "Cancellation refused.", "errors": exc.messages}
    return 204, None


@api.get("/spaces/{slug}/invoices", response=list[InvoiceOut], tags=["invoicing"])
def list_invoices(request: HttpRequest, slug: str, year: int | None = None) -> Any:
    space = _space(request, slug, "core.manage_space")
    qs = (
        Invoice.objects.for_space(space)
        .filter(status=InvoiceStatus.ISSUED)
        .exclude(kind=Kind.QUOTE)
        .select_related("client")
    )
    if year:
        qs = qs.filter(issue_date__year=year)
    return [
        {
            "id": i.public_id,
            "kind": i.kind,
            "number": i.number,
            "client": i.client_name or i.client.name,
            "issue_date": i.issue_date,
            "total_excl_tax": i.total_excl_tax,
            "total_incl_tax": i.total_incl_tax,
            "paid_at": i.paid_at,
        }
        for i in qs.order_by("-issue_date", "-number")
    ]
