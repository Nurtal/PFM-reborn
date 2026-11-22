import pytest

from apps.core import services
from apps.core.tests.factories import complete_setup


@pytest.fixture(autouse=True)
def _reset_setup_cache():
    services.reset_setup_cache()
    yield
    services.reset_setup_cache()


@pytest.fixture
def setup_done(db):
    return complete_setup()
