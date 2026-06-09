from pydantic import BaseModel


class HealthOut(BaseModel):
    total_devices: int
    by_status: dict[str, int]  # es. {"reachable": 3, "unverified": 1}
    active_alerts: int
