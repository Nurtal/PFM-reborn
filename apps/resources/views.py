from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.db.models import QuerySet
from django.forms import BaseForm
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.permissions import SpaceViewMixin
from apps.resources import forms
from apps.resources.models import Area, Authorization, Resource, ResourceCategory


def visible_resources(user: Any, space: Any) -> QuerySet[Resource]:
    qs = Resource.objects.for_space(space).select_related("category", "area")
    if not user.has_perm("core.manage_space", space):
        qs = qs.filter(is_active=True, area__is_restricted=False)
    return qs


class ResourceListView(SpaceViewMixin, ListView):
    permission_required = "core.view_space"
    template_name = "resources/resource_list.html"
    context_object_name = "resources"

    def get_queryset(self) -> QuerySet[Resource]:
        return visible_resources(self.request.user, self.space)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = self.request.user.has_perm("core.manage_space", self.space)
        return ctx


class SpaceFormMixin(SpaceViewMixin):
    """Create/update views for space-scoped models, managers only."""

    permission_required = "core.manage_space"
    template_name: str | None = "resources/form.html"
    list_url_name = ""
    title: Any = ""

    def get_queryset(self) -> QuerySet[Any]:
        return self.model.objects.for_space(self.space)  # type: ignore[attr-defined]

    def get_object(self, queryset: Any = None) -> Any:
        lookup = (
            {"public_id": self.kwargs["pk"]}
            if "public_id"
            in [
                f.name
                for f in self.model._meta.fields  # type: ignore[attr-defined]
            ]
            else {"pk": self.kwargs["pk"]}
        )
        return get_object_or_404(self.get_queryset(), **lookup)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()  # type: ignore[misc]
        kwargs["space"] = self.space
        return kwargs

    def get_success_url(self) -> str:
        return reverse(self.list_url_name, args=[self.space.slug])

    def form_valid(self, form: BaseForm) -> HttpResponse:
        messages.success(self.request, _("Saved."))
        return super().form_valid(form)  # type: ignore[misc]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)  # type: ignore[misc]
        ctx["title"] = self.title
        ctx["cancel_url"] = self.get_success_url()
        return ctx


class ResourceCreateView(SpaceFormMixin, CreateView):
    model = Resource
    form_class = forms.ResourceForm
    list_url_name = "resources:list"
    title = gettext_lazy("New resource")


class ResourceUpdateView(SpaceFormMixin, UpdateView):
    model = Resource
    form_class = forms.ResourceForm
    list_url_name = "resources:list"
    title = gettext_lazy("Edit resource")


class AreaListView(SpaceViewMixin, ListView):
    permission_required = "core.manage_space"
    template_name = "resources/settings.html"

    def get_queryset(self) -> QuerySet[Area]:
        return Area.objects.for_space(self.space)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["areas"] = ctx["object_list"]
        ctx["categories"] = ResourceCategory.objects.for_space(self.space)
        return ctx


class AreaCreateView(SpaceFormMixin, CreateView):
    model = Area
    form_class = forms.AreaForm
    list_url_name = "resources:settings"
    title = gettext_lazy("New area")


class AreaUpdateView(SpaceFormMixin, UpdateView):
    model = Area
    form_class = forms.AreaForm
    list_url_name = "resources:settings"
    title = gettext_lazy("Edit area")


class CategoryCreateView(SpaceFormMixin, CreateView):
    model = ResourceCategory
    form_class = forms.CategoryForm
    list_url_name = "resources:settings"
    title = gettext_lazy("New category")


class CategoryUpdateView(SpaceFormMixin, UpdateView):
    model = ResourceCategory
    form_class = forms.CategoryForm
    list_url_name = "resources:settings"
    title = gettext_lazy("Edit category")


class AuthorizationListView(SpaceViewMixin, ListView):
    permission_required = "core.manage_space"
    template_name = "resources/authorization_list.html"
    context_object_name = "authorizations"
    paginate_by = 50

    def get_queryset(self) -> QuerySet[Authorization]:
        qs = Authorization.objects.for_space(self.space).select_related(
            "user", "category", "granted_by"
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = (
                qs.filter(user__last_name__icontains=q)
                | qs.filter(user__username__icontains=q)
                | qs.filter(category__name__icontains=q)
            )
        return qs.order_by("user__last_name", "user__username", "category__name")

    def get_template_names(self) -> list[str]:
        if self.request.htmx:  # type: ignore[attr-defined]
            return ["resources/_authorization_rows.html"]
        return [self.template_name]


class AuthorizationCreateView(SpaceFormMixin, CreateView):
    model = Authorization
    form_class = forms.AuthorizationForm
    list_url_name = "resources:authorizations"
    title = gettext_lazy("New authorization")


class AuthorizationUpdateView(SpaceFormMixin, UpdateView):
    model = Authorization
    form_class = forms.AuthorizationForm
    list_url_name = "resources:authorizations"
    title = gettext_lazy("Edit authorization")
