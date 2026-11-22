import pytest
from django.utils import translation


@pytest.fixture(autouse=True)
def _english():
    """LocaleMiddleware activates the request language in the test thread and
    never resets it; start every test in English."""
    translation.activate("en")
    yield
    translation.deactivate()
