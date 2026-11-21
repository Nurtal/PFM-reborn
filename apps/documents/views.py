from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import messages
from django.db.models import QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.models import User
from apps.core.permissions import SpaceViewMixin
from apps.core.uploads import check_upload
from apps.documents.models import Document, Visibility


class UploadForm(forms.Form):
    title = forms.CharField(label=gettext_lazy("Title"), max_length=200)
    description = forms.CharField(
        label=gettext_lazy("Description"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    visibility = forms.ChoiceField(
        label=gettext_lazy("Visible to"), choices=Visibility.choices, initial=Visibility.MEMBERS
    )
    file = forms.FileField(label=gettext_lazy("File"))

    def clean_file(self) -> Any:
        upload = self.cleaned_data["file"]
        self.checked = check_upload(upload)
        return upload


def visible_documents(user: Any, space: Any) -> QuerySet[Document]:
    qs = Document.objects.for_space(space).select_related("uploaded_by")
    if not user.has_perm("core.manage_space", space):
        qs = qs.filter(visibility=Visibility.MEMBERS)
    return qs


class DocumentListView(SpaceViewMixin, TemplateView):
    permission_required = "core.view_space"
    template_name = "documents/list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        can_manage = self.request.user.has_perm("core.manage_space", self.space)
        ctx.update(
            documents=visible_documents(self.request.user, self.space),
            can_manage=can_manage,
            form=kwargs.get("form") or (UploadForm() if can_manage else None),
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.has_perm("core.manage_space", self.space):
            return HttpResponse(status=404)
        if "delete" in request.POST:
            doc = get_object_or_404(
                Document.objects.for_space(self.space), public_id=request.POST["delete"]
            )
            doc.file.delete(save=False)
            doc.delete()
            messages.success(request, _("Document deleted."))
            return redirect("documents:list", space_slug=self.space.slug)
        form = UploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        checked = form.checked
        doc = Document(
            space=self.space,
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            visibility=form.cleaned_data["visibility"],
            original_name=checked.original_name,
            content_type=checked.content_type,
            size=checked.size,
            uploaded_by=cast(User, request.user),
        )
        doc.file.save(checked.storage_name(), form.cleaned_data["file"], save=False)
        doc.save()
        messages.success(request, _("Document uploaded."))
        return redirect("documents:list", space_slug=self.space.slug)


class DocumentDownloadView(SpaceViewMixin, View):
    permission_required = "core.view_space"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> FileResponse:
        doc = get_object_or_404(
            visible_documents(request.user, self.space), public_id=kwargs["doc_id"]
        )
        response = FileResponse(
            doc.file.open("rb"),
            as_attachment=True,
            filename=doc.original_name,
            content_type=doc.content_type,
        )
        # Even if a browser decided to render it, it would get no script rights.
        response["Content-Security-Policy"] = "sandbox; default-src 'none'"
        return response
