import httpx


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
        url = f"{self._base_url}/api/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify, timeout=self._timeout, auth=self._auth
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:  # ConnectError/Timeout/TLS/etc.
            raise ReachabilityError(str(exc)) from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text[:200])
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError(str(exc)) from exc

    async def get_firmware_status(self) -> dict:
        return await self._get("core/firmware/status")

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
