from pydantic import BaseModel


class ReportSettingsIn(BaseModel):
    title: str
    owner: str = ""
    timezone: str = "UTC"
    language: str = "en"


class ReportSettingsOut(BaseModel):
    title: str
    owner: str
    timezone: str
    has_logo: bool
    logo_mime: str | None
    language: str


class ReportLanguageOut(BaseModel):
    code: str
    name: str
