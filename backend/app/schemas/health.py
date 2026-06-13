from pydantic import BaseModel


class HealthOut(BaseModel):
    total_devices: int
    by_status: dict[str, int]  # e.g. {"reachable": 3, "unverified": 1}
    active_alerts: int


class CountryCountOut(BaseModel):
    """One attacker-country row: ISO alpha-2 code (or PRIVATE/UNKNOWN sentinel), count, share %.

    The country *name* is localized client-side (Intl.DisplayNames) / at report render (Babel); the
    API returns only the code so the frontend can resolve the viewer's locale.
    """
    code: str
    count: int
    pct: float
