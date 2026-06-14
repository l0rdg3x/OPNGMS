from datetime import datetime

from pydantic import BaseModel


class PerimeterAttackerOut(BaseModel):
    """One attacker IP in the perimeter view (failed logins / firewall blocks).

    `country` is an ISO alpha-2 code or a PRIVATE/UNKNOWN sentinel (localized client-side, like the
    attacker-countries view). `count` is cumulative; `label` is the last attempted username
    (login_failed) or the most-targeted port (firewall_block)."""

    src_ip: str
    country: str
    count: int
    last_seen: datetime
    label: str
