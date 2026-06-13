from pydantic import BaseModel, field_validator

from app.services.reporting.sections import SECTION_KEYS


def _only_known_sections(v: dict[str, bool]) -> dict[str, bool]:
    """Drop unknown section keys (forward-compat / stale-map safe); coerce to bool."""
    return {k: bool(val) for k, val in v.items() if k in SECTION_KEYS}


class ReportSettingsIn(BaseModel):
    title: str
    owner: str = ""
    timezone: str = "UTC"
    language: str = "en"
    from_email: str = ""
    sections: dict[str, bool] = {}

    @field_validator("sections")
    @classmethod
    def _sections(cls, v: dict[str, bool]) -> dict[str, bool]:
        return _only_known_sections(v)


class ReportSettingsOut(BaseModel):
    title: str
    owner: str
    timezone: str
    has_logo: bool
    logo_mime: str | None
    language: str
    from_email: str
    sections: dict[str, bool] = {}


class ReportLanguageOut(BaseModel):
    code: str
    name: str
