from urllib.parse import urlsplit

import httpx

from app.connectors.opnsense.url_safety import UnsafeUrlError, validate_base_url


class OpnsenseError(Exception):
    """Base per gli errori del connector OPNsense."""


class AuthError(OpnsenseError):
    """Credenziali API rifiutate (401/403)."""


class ReachabilityError(OpnsenseError):
    """Device non raggiungibile (DNS/TLS/connessione/timeout)."""


class ApiError(OpnsenseError):
    """Risposta HTTP di errore (4xx/5xx non-auth)."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class ParseError(OpnsenseError):
    """Risposta non interpretabile come JSON."""


class OpnsenseClient:
    """Unico confine HTTP verso un device OPNsense.

    Auth HTTP Basic (api_key come username, api_secret come password) su HTTPS.
    NOTA: gli endpoint esatti sono DA VERIFICARE contro un OPNsense reale; qui si usa
    `core/firmware/status` per il test di connessione + versione firmware.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        verify_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (api_key, api_secret)
        self._verify = verify_tls
        self._timeout = timeout

    async def _get(self, path: str) -> dict:
        # Guardia SSRF: valida lo schema/userinfo/host e risolve+pinna l'IP.
        try:
            pinned_ip, host, port = validate_base_url(self._base_url)
        except UnsafeUrlError as exc:
            # Messaggio SANITIZZATO: niente dettaglio dell'URL non sicuro.
            raise ReachabilityError("destinazione non sicura") from exc
        # Connetti all'IP pinnato (anti DNS-rebinding); l'hostname originale resta
        # per l'header Host e per SNI/verifica cert TLS.
        conn_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = conn_host if port is None else f"{conn_host}:{port}"
        base_path = urlsplit(self._base_url).path.rstrip("/")
        url = f"https://{netloc}{base_path}/api/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify,
                timeout=self._timeout,
                auth=self._auth,
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    url, headers={"Host": host}, extensions={"sni_hostname": host}
                )
        except httpx.HTTPError as exc:  # ConnectError/Timeout/TLS/etc.
            raise ReachabilityError("device non raggiungibile") from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # NON includere il body upstream nell'errore.
            raise ApiError(resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("risposta non interpretabile") from exc

    async def get_firmware_status(self) -> dict:
        return await self._get("core/firmware/status")

    async def get_system_info(self) -> dict:
        """CPU/mem/disco/uptime. NOTA: endpoint+campi DA VERIFICARE su un OPNsense reale."""
        data = await self._get("diagnostics/system/systemInformation")
        return {
            "cpu_pct": float((data.get("cpu") or {}).get("used", 0.0)),
            "mem_pct": float((data.get("memory") or {}).get("used_pct", 0.0)),
            "disk_pct": float((data.get("disk") or {}).get("used_pct", 0.0)),
            "uptime_seconds": int(data.get("uptime_seconds", 0)),
        }

    @staticmethod
    def _num(v) -> float:
        """Estrae il primo float da una stringa tipo '12.3 ms' / '0.0 %' / numero.

        NOTA: il formato esatto dei campi delay/loss/bytes è DA VERIFICARE contro
        un OPNsense reale; la regex è difensiva per gestire varianti di stringa.
        """
        import re

        if isinstance(v, (int, float)):
            return float(v)
        m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
        return float(m.group()) if m else 0.0

    async def get_interfaces(self) -> list[dict]:
        """Statistiche per interfaccia di rete.

        NOTA: endpoint `diagnostics/interface/getInterfaceStatistics` e i campi
        bytes_received/bytes_transmitted sono DA VERIFICARE su un OPNsense reale.
        """
        data = await self._get("diagnostics/interface/getInterfaceStatistics")
        out = []
        for it in data.get("interfaces", []):
            out.append({
                "name": it.get("name", ""),
                "up": it.get("status") == "up",
                "bytes_in": self._num(it.get("bytes_received")),
                "bytes_out": self._num(it.get("bytes_transmitted")),
            })
        return out

    async def get_gateways(self) -> list[dict]:
        """Stato dei gateway (RTT, packet-loss).

        NOTA: endpoint `routes/gateway/status`, chiave `items`, e i campi
        delay/loss (con unità " ms"/" %") sono DA VERIFICARE su un OPNsense reale.
        Gateway è down solo se status in {"down", "force_down"}.
        """
        data = await self._get("routes/gateway/status")
        out = []
        for g in data.get("items", []):
            status = str(g.get("status", "")).lower()
            out.append({
                "name": g.get("name", ""),
                "up": status not in ("down", "force_down"),
                "rtt_ms": self._num(g.get("delay")),
                "loss_pct": self._num(g.get("loss")),
            })
        return out

    async def get_vpn_status(self) -> list[dict]:
        """Stato dei tunnel WireGuard.

        NOTA: endpoint `wireguard/service/show` e la chiave `tunnels` con campo
        `connected` sono DA VERIFICARE su un OPNsense reale. OpenVPN usa un endpoint
        diverso (non ancora implementato).
        """
        data = await self._get("wireguard/service/show")
        return [
            {"name": t.get("name", ""), "up": bool(t.get("connected"))}
            for t in data.get("tunnels", [])
        ]

    async def test_connection(self) -> str | None:
        """Verifica raggiungibilità+credenziali; ritorna la versione firmware o None.

        Solleva AuthError/ReachabilityError/ApiError/ParseError in caso di problemi.
        """
        data = await self.get_firmware_status()
        # Campo DA VERIFICARE su un OPNsense reale (nome esatto può differire).
        version = data.get("product_version")
        if version is None and isinstance(data.get("product"), dict):
            version = data["product"].get("product_version")
        return version
