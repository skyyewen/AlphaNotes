import pytest

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolate_configuration(monkeypatch):
    """Do not depend on developer shell variables or a future local .env."""
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
