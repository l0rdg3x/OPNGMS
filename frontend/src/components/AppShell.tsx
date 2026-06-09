import { useAuth } from "../auth/useAuth";
export function AppShell() {
  const { me } = useAuth();
  return <div>{me?.email}</div>;
}
