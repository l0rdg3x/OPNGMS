from pydantic import BaseModel


class ReportSettingsIn(BaseModel):
    title: str
    owner: str = ""
    timezone: str = "UTC"
    language: str = "en"
    from_email: str = ""


class ReportSettingsOut(BaseModel):
    title: str
    owner: str
    timezone: str
    has_logo: bool
    logo_mime: str | None
    language: str
    from_email: str


class ReportLanguageOut(BaseModel):
    code: str
    name: str
