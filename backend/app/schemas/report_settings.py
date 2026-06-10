from pydantic import BaseModel


class ReportSettingsIn(BaseModel):
    title: str
    owner: str = ""
    timezone: str = "UTC"


class ReportSettingsOut(BaseModel):
    title: str
    owner: str
    timezone: str
    has_logo: bool
    logo_mime: str | None
