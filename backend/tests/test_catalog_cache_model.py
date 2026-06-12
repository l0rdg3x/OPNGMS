from app.models import CatalogCache


def test_catalog_cache_table_and_columns():
    t = CatalogCache.__table__
    assert t.name == "catalog_cache"
    cols = set(t.columns.keys())
    assert {"id", "edition", "version", "sha256", "content", "fetched_at"} <= cols
    # unique on (edition, version)
    uniques = [tuple(c.name for c in con.columns)
               for con in t.constraints if con.__class__.__name__ == "UniqueConstraint"]
    assert ("edition", "version") in uniques
