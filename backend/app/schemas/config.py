import uuid
from datetime import datetime

from pydantic import BaseModel


class ConfigSnapshotOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    taken_at: datetime
    canonical_hash: str
    opnsense_version: str
    size_bytes: int
    # NB: content is NEVER exposed (it holds secrets).

    model_config = {"from_attributes": True}


class ConfigDiffEntry(BaseModel):
    path: str
    change: str  # added | removed | modified


class DriftSummary(BaseModel):
    version_count: int
    latest_taken_at: datetime | None
    changed_since_previous: bool


class Interface(BaseModel):
    name: str
    nic: str
    description: str


class Capability(BaseModel):
    id: str
    label: str
    area: str


class CapabilityInventory(BaseModel):
    opnsense_version: str
    interfaces: list[Interface]
    configured_sections: list[str]
    available_capabilities: list[Capability]
