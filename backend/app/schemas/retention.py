from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field


class RetentionWarning(BaseModel):
    """One enabled report schedule whose covered range now exceeds the tenant's effective retention.

    Computed on read (SP-1 PR4b) so it always reflects current truth: an existing schedule can become
    over-long when retention is later lowered below the window it covers.
    """
    schedule_id: UUID
    frequency: str                     # "weekly" | "monthly" (never "on_demand" — those carry no window)
    range_days: int                    # the fixed window the schedule covers (weekly=7, monthly=30)
    bound: int                         # current effective retention of the limiting store (< range_days)
    limiting_store: str                # the store (perimeter/events/metrics) that sets the bound


class RetentionOut(BaseModel):
    overrides: dict[str, int]          # the stored per-tenant overrides
    defaults: dict[str, int]           # effective global defaults (for "inherit (N)" hints)
    warnings: list[RetentionWarning] = []  # enabled schedules now over-long vs. effective retention


# A per-store value: an int in [1, 3650] sets an override, null clears it (back to inherit).
RetentionValue = Annotated[int, Field(ge=1, le=3650)] | None


class RetentionPatch(BaseModel):
    # each store optional; an int sets an override, null clears it. Unknown keys rejected in the handler.
    values: dict[str, RetentionValue] = Field(default_factory=dict)
