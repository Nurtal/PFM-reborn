import pytest
from django.urls import reverse

from apps.resources.tests.factories import AreaFactory, ResourceFactory
from apps.services.models import ServiceItem

pytestmark = pytest.mark.django_db


def test_catalogue_is_opt_in_and_public(client, setup_done):
    r = ResourceFactory(name="Visible scope")
    url = reverse("catalogue", args=[r.space.slug])
    assert client.get(url).status_code == 404
    r.space.catalogue_public = True
    r.space.save()
    hidden_area = AreaFactory(space=r.space, is_restricted=True)
    ResourceFactory(space=r.space, area=hidden_area, category=r.category, name="Secret room")
    ResourceFactory(
        space=r.space, area=r.area, category=r.category, name="Old scope", is_active=False
    )
    ServiceItem.objects.create(space=r.space, name="Sample prep")
    response = client.get(url)  # anonymous
    body = response.content.decode()
    assert response.status_code == 200
    assert "Visible scope" in body and "Sample prep" in body
    assert "Secret room" not in body and "Old scope" not in body


def test_inactive_space_has_no_catalogue(client, setup_done):
    r = ResourceFactory()
    r.space.catalogue_public, r.space.is_active = True, False
    r.space.save()
    assert client.get(reverse("catalogue", args=[r.space.slug])).status_code == 404
