import { useAuth } from "./useAuth";

/** True when the current user is a platform superadmin (manages the global template library). */
export function useIsSuperadmin(): boolean {
  return useAuth().me?.is_superadmin ?? false;
}
