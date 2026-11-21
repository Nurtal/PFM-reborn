from __future__ import annotations

import datetime as dt
import json
from typing import Any, cast

from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.accounts.models import User
from apps.booking import ical, services
from apps.booking.forms import BookingForm
from apps.booking.models import Booking, Closure, FeedToken, Status
from apps.core.permissions import SpaceViewMixin
from apps.resources.models import Resource
from apps.resources.views import visible_resources

MAX_RANGE = dt.timedelta(days=62)


def _hx_done(message: str) -> HttpResponse:
    """Empty response telling the page to close the dialog, refresh and toast."""
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"bookingChanged": True, "toast": message})
    return response


class ResourceMixin(SpaceViewMixin):
    permission_required = "core.view_space"
    resource: Resource

    def setup_resource(self) -> None:
        self.resource = get_object_or_404(
            visible_resources(self.request.user, self.space).select_related("space"),
            public_id=self.kwargs["resource_id"],
        )


class BookingHomeView(SpaceViewMixin, View):
    permission_required = "core.view_space"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        first = visible_resources(request.user, self.space).filter(is_active=True).first()
        if first is None:
            return redirect("resources:list", space_slug=self.space.slug)
        return redirect("booking:calendar", space_slug=self.space.slug, resource_id=first.public_id)


class CalendarView(ResourceMixin, TemplateView):
    template_name = "booking/calendar.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        self.setup_resource()
        ctx = super().get_context_data(**kwargs)
        area = self.resource.area
        user = self.request.user
        refusals = services.booking_refusals(user, self.resource)  # type: ignore[arg-type]
        ctx.update(
            resource=self.resource,
            resources=visible_resources(user, self.space).filter(is_active=True),
            refusals=refusals,
            calendar_config={
                "eventsUrl": f"{self.request.path}events/",
                "newUrl": f"{self.request.path}new/",
                "canBook": not refusals,
                "slotMinutes": area.slot_minutes,
                "slotMinTime": area.day_start.strftime("%H:%M"),
                "slotMaxTime": "24:00"
                if area.day_end == dt.time(0)
                else area.day_end.strftime("%H:%M"),
                "hiddenDays": [(d + 1) % 7 for d in range(7) if not area.is_open_on(d)],
                "color": self.resource.color,
            },
        )
        return ctx


class EventsView(ResourceMixin, View):
    """FullCalendar JSON feed for one resource."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        self.setup_resource()
        start = parse_datetime(request.GET.get("start", ""))
        end = parse_datetime(request.GET.get("end", ""))
        if start is None or end is None or end <= start or end - start > MAX_RANGE:
            return JsonResponse({"error": "invalid range"}, status=400)
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        if timezone.is_naive(end):
            end = timezone.make_aware(end)

        user = request.user
        manager = services.is_resource_manager(user, self.resource)  # type: ignore[arg-type]
        bookings = Booking.objects.filter(
            resource=self.resource, status=Status.CONFIRMED, start__lt=end, end__gt=start
        ).select_related("user")
        events: list[dict[str, Any]] = []
        for b in bookings:
            mine = b.user_id == user.pk
            # Data minimisation: other users only see that the slot is taken.
            title = (b.title or str(b.user)) if (mine or manager) else _("Booked")
            events.append(
                {
                    "id": str(b.public_id),
                    "title": title,
                    "start": b.start.isoformat(),
                    "end": b.end.isoformat(),
                    "classNames": ["pfm-mine"] if mine else [],
                    "extendedProps": {
                        "detailUrl": f"/s/{self.space.slug}/bookings/{b.public_id}/"
                        if (mine or manager)
                        else None
                    },
                }
            )
        closures = Closure.objects.filter(start__lt=end, end__gt=start).filter(
            resource=self.resource
        ) | Closure.objects.filter(
            start__lt=end, end__gt=start, area_id=self.resource.area_id, resource__isnull=True
        )
        for c in closures:
            events.append(
                {
                    "title": c.reason,
                    "start": c.start.isoformat(),
                    "end": c.end.isoformat(),
                    "display": "background",
                    "classNames": ["pfm-closure"],
                }
            )
        return JsonResponse(events, safe=False)


class BookingCreateView(ResourceMixin, TemplateView):
    """HTMX dialog: GET shows the form for a selected range, POST books it."""

    template_name = "booking/_booking_form.html"

    def get_form(self) -> BookingForm:
        initial = {}
        for key in ("start", "end"):
            value = parse_datetime(self.request.GET.get(key, ""))
            if value is not None:
                initial[key] = timezone.localtime(value) if timezone.is_aware(value) else value
        return BookingForm(
            self.request.POST or None,
            initial=initial,
            actor=self.request.user,
            resource=self.resource,
        )

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.setup_resource()
        return self.render_to_response({"form": self.get_form(), "resource": self.resource})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.setup_resource()
        form = self.get_form()
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.reserve(
                    request.user,  # type: ignore[arg-type]
                    self.resource,
                    d["start"],
                    d["end"],
                    for_user=d.get("for_user"),
                    title=d["title"],
                    note=d["note"],
                )
            except services.BookingError as exc:
                for message in exc.messages:
                    form.add_error(None, message)
            else:
                return _hx_done(_("Booking confirmed."))
        return self.render_to_response({"form": form, "resource": self.resource})


class BookingDetailView(SpaceViewMixin, TemplateView):
    """HTMX dialog to view, move or cancel one booking."""

    permission_required = "core.view_space"
    template_name = "booking/_booking_detail.html"

    def get_booking(self) -> Booking:
        booking = get_object_or_404(
            Booking.objects.for_space(self.space).select_related("resource__area", "user"),
            public_id=self.kwargs["booking_id"],
        )
        user = self.request.user
        if booking.user_id != user.pk and not services.is_resource_manager(
            user,  # type: ignore[arg-type]
            booking.resource,
        ):
            raise Http404
        return booking

    def context(self, booking: Booking, form: BookingForm | None = None) -> dict[str, Any]:
        can_change = services.can_change(self.request.user, booking)  # type: ignore[arg-type]
        if form is None and can_change:
            form = BookingForm(instance=booking, actor=self.request.user, resource=booking.resource)
        return {"booking": booking, "form": form, "can_change": can_change}

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return self.render_to_response(self.context(self.get_booking()))

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        booking = self.get_booking()
        user = request.user
        try:
            if request.POST.get("action") == "cancel":
                services.cancel(user, booking)  # type: ignore[arg-type]
                return _hx_done(_("Booking cancelled."))
            form = BookingForm(
                request.POST, instance=booking, actor=user, resource=booking.resource
            )
            if form.is_valid():
                d = form.cleaned_data
                services.modify(
                    user,  # type: ignore[arg-type]
                    booking,
                    d["start"],
                    d["end"],
                    title=d["title"],
                    note=d["note"],
                )
                return _hx_done(_("Booking updated."))
        except services.BookingError as exc:
            booking.refresh_from_db()
            form = BookingForm(
                request.POST, instance=booking, actor=user, resource=booking.resource
            )
            form.is_valid()
            for message in exc.messages:
                form.add_error(None, message)
        return self.render_to_response(self.context(booking, form))


class MyBookingsView(SpaceViewMixin, ListView):
    permission_required = "core.view_space"
    template_name = "booking/my_bookings.html"
    context_object_name = "bookings"
    paginate_by = 30

    def get_queryset(self) -> Any:
        qs = Booking.objects.for_space(self.space).filter(
            user=self.request.user.pk, status=Status.CONFIRMED
        )
        if self.request.GET.get("past"):
            qs = qs.filter(end__lte=timezone.now()).order_by("-start")
        else:
            qs = qs.filter(end__gt=timezone.now())
        return qs.select_related("resource")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        user = cast(User, self.request.user)  # login required by middleware
        token, _created = FeedToken.objects.get_or_create(user=user)
        ctx["feed_url"] = self.request.build_absolute_uri(f"/ical/{token.token}.ics")
        ctx["show_past"] = bool(self.request.GET.get("past"))
        return ctx


@login_not_required
def ical_feed(request: HttpRequest, token: str) -> HttpResponse:
    """Read-only iCalendar feed of a user's bookings (secret URL, like CalDAV clients expect)."""
    feed = (
        FeedToken.objects.select_related("user").filter(token=token, user__is_active=True).first()
    )
    if feed is None:
        raise Http404
    since = timezone.now() - dt.timedelta(days=90)
    bookings = (
        Booking.objects.filter(user=feed.user, status=Status.CONFIRMED, end__gte=since)
        .select_related("resource", "space")
        .order_by("start")
    )
    response = HttpResponse(ical.render(bookings), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'inline; filename="pfm.ics"'
    response["Cache-Control"] = "private, max-age=300"
    return response
