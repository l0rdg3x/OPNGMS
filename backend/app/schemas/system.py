from pydantic import BaseModel


class LivePushIn(BaseModel):
    enabled: bool


class LivePushOut(BaseModel):
    enabled: bool


class RuntimeSettingOut(BaseModel):
    key: str
    value: bool | int | float  # effective value (override or default)
    default: bool | int | float  # the env/code default
    kind: str  # "int" | "float" | "bool"
    minimum: float | None = None
    maximum: float | None = None
    group: str


class RuntimeSettingsOut(BaseModel):
    settings: list[RuntimeSettingOut]


class RuntimeSettingsPatch(BaseModel):
    values: dict[str, bool | int | float]
