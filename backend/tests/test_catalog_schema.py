import pytest
from pydantic import ValidationError

from app.schemas.catalog import CatalogChangeIn, CatalogGridOpIn


def test_catalog_change_in_minimal_scalars_only():
    c = CatalogChangeIn(model_id="unbound", scalars={"general.enabled": "1"})
    assert c.grids == []


def test_catalog_grid_op_rejects_unknown_op():
    with pytest.raises(ValidationError):
        CatalogGridOpIn(op="explode", grid="hosts")


def test_catalog_change_in_with_grid():
    c = CatalogChangeIn(
        model_id="unbound",
        grids=[CatalogGridOpIn(op="del", grid="hosts", uuid="abc")])
    assert c.grids[0].op == "del"
