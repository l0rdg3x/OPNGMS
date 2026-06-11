import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { MfaSetupGate } from "../security/MfaSetupGate";
import { useAuth } from "./useAuth";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return null;
  if (!me) return <Navigate to="/login" replace />;
  // Policy requires MFA but the user is not enrolled: force the full-screen setup gate
  // instead of the app, until enrollment upgrades the session (GET /api/me clears the flag).
  if (me.mfa_setup_required) return <MfaSetupGate />;
  return <>{children}</>;
}
