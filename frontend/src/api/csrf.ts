// Reads the per-session CSRF token from the readable `opngms_csrf` cookie set at login.
// The value is echoed back in the X-OPNGMS-CSRF header on mutating requests.
export function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)opngms_csrf=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}
