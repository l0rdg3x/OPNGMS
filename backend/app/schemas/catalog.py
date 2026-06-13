from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogGridOpIn(BaseModel):
    """One ArrayField grid op the editor wants applied. `grid` is the catalog grid path."""
    op: Literal["add", "set", "del"]
    grid: str
    uuid: str | None = None
    item: dict | None = None


class CatalogChangeIn(BaseModel):
    """A generic catalog edit: scalar field values + grid ops for one model. Endpoints are resolved
    server-side from the device's catalog (never trusted from the client)."""
    # `model_id` lives in Pydantic's protected `model_` namespace; opt out (it is a plain field).
    model_config = ConfigDict(protected_namespaces=())

    model_id: str = Field(min_length=1)
    scalars: dict[str, str] = Field(default_factory=dict)
    grids: list[CatalogGridOpIn] = Field(default_factory=list)


class PluginModelOut(BaseModel):
    package: str
    model_id: str
    title: str = ""
