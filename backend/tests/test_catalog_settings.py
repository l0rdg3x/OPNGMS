from app.core.config import Settings


def _minimal(**over):
    base = dict(database_url="postgresql+asyncpg://x", session_secret="s", master_key="k")
    base.update(over)
    return Settings(**base)


def test_catalog_settings_have_defaults():
    s = _minimal()
    assert s.catalog_release_base_url.startswith("https://github.com/")
    assert s.catalog_auto_fetch is True


def test_catalog_settings_overridable():
    s = _minimal(catalog_release_base_url="https://x/y", catalog_auto_fetch=False)
    assert s.catalog_release_base_url == "https://x/y"
    assert s.catalog_auto_fetch is False
