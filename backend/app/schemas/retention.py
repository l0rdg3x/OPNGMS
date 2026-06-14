from typing import Annotated

from pydantic import BaseModel, Field


class RetentionOut(BaseModel):
    overrides: dict[str, int]          # the stored per-tenant overrides
    defaults: dict[str, int]           # effective global defaults (for "inherit (N)" hints)


# A per-store value: an int in [1, 3650] sets an override, null clears it (back to inherit).
RetentionValue = Annotated[int, Field(ge=1, le=3650)] | None


class RetentionPatch(BaseModel):
    # each store optional; an int sets an override, null clears it. Unknown keys rejected in the handler.
    values: dict[str, RetentionValue] = Field(default_factory=dict)
