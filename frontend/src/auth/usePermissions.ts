import { useTenant } from "../tenant/useTenant";
import { useAuth } from "./useAuth";

export interface Permissions {
  /** Active tenant role (null for a superadmin, or before a tenant is resolved). */
  role: string | null;
  /** tenant_admin-level capabilities: report config (settings + schedule), membership mgmt. */
  isTenantAdmin: boolean;
  /** operator-level or above: device write, config push, report generate, log view. */
  isOperator: boolean;
}

/**
 * Effective tenant-scoped capabilities for the current user in the active tenant.
 *
 * Mirrors the backend `can()` matrix (app/core/rbac.py): a platform superadmin is
 * allowed every action regardless of the per-tenant role. Because `/api/me/tenants`
 * reports `role: null` for superadmins (global access, no membership row needed),
 * gating the UI purely on the tenant role would hide tenant-admin/operator features
 * from the most privileged user even though the API authorizes them. This hook folds
 * `is_superadmin` in so the UI matches the API.
 */
export function usePermissions(): Permissions {
  const isSuperadmin = useAuth().me?.is_superadmin ?? false;
  const { activeId, tenants } = useTenant();
  const role = tenants.find((t) => t.id === activeId)?.role ?? null;
  const isTenantAdmin = isSuperadmin || role === "tenant_admin";
  const isOperator = isTenantAdmin || role === "operator";
  return { role, isTenantAdmin, isOperator };
}
