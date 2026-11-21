import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.documents.models import Document

pytestmark = pytest.mark.django_db


@pytest.fixture
def space(setup_done):
    return SpaceFactory()


def upload(client, space, name="charte.pdf", content=b"%PDF-1.4 x", visibility="members"):
    return client.post(
        reverse("documents:list", args=[space.slug]),
        {"title": "Charte", "visibility": visibility, "file": SimpleUploadedFile(name, content)},
    )


def test_manager_uploads_and_member_downloads(client, space):
    manager = MembershipFactory(space=space, role=Role.MANAGER).user
    client.force_login(manager)
    assert upload(client, space).status_code == 302
    doc = Document.objects.get()
    assert doc.original_name == "charte.pdf"
    assert "charte" not in doc.file.name  # random storage name
    member = MembershipFactory(space=space, role=Role.USER).user
    client.force_login(member)
    response = client.get(reverse("documents:download", args=[space.slug, doc.public_id]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    assert "sandbox" in response["Content-Security-Policy"]


def test_rejected_upload_shows_error(client, space):
    client.force_login(MembershipFactory(space=space, role=Role.MANAGER).user)
    response = upload(client, space, name="evil.html", content=b"<script>")
    assert response.status_code == 200
    assert "file" in response.context["form"].errors
    assert not Document.objects.exists()


def test_members_cannot_upload_or_see_manager_documents(client, space):
    client.force_login(MembershipFactory(space=space, role=Role.MANAGER).user)
    upload(client, space, visibility="managers")
    doc = Document.objects.get()
    client.force_login(MembershipFactory(space=space, role=Role.USER).user)
    assert upload(client, space).status_code == 404
    assert (
        client.get(reverse("documents:download", args=[space.slug, doc.public_id])).status_code
        == 404
    )
    assert "Charte" not in client.get(reverse("documents:list", args=[space.slug])).content.decode()


def test_other_space_cannot_download(client, space):
    client.force_login(MembershipFactory(space=space, role=Role.MANAGER).user)
    upload(client, space)
    doc = Document.objects.get()
    outsider = MembershipFactory(role=Role.ADMIN)
    from allauth.mfa.models import Authenticator

    Authenticator.objects.create(user=outsider.user, type=Authenticator.Type.TOTP, data={})
    client.force_login(outsider.user)
    url = reverse("documents:download", args=[outsider.space.slug, doc.public_id])
    assert client.get(url).status_code == 404
