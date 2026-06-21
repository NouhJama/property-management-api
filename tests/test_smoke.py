"""Smoke tests — verifies the app can be imported and basic config loads."""

from app.core.config import settings


def test_app_name_is_set():
    assert settings.app_name


def test_database_url_uses_asyncpg():
    assert settings.database_url.startswith("postgresql+asyncpg://")
