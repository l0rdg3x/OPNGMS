import { AppShell as MantineAppShell, Button, Group, Loader, NavLink, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { NavLink as RouterNavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { useT } from "../i18n";
import { TenantProvider } from "../tenant/TenantProvider";
import { useTenant } from "../tenant/useTenant";
import { TenantSwitcher } from "./TenantSwitcher";

// Heavy inner pages are lazy-loaded to split the initial JS bundle.
const OverviewPage = lazy(() => import("../pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const AlertsPage = lazy(() => import("../pages/AlertsPage").then((m) => ({ default: m.AlertsPage })));
const DevicesPage = lazy(() => import("../pages/DevicesPage").then((m) => ({ default: m.DevicesPage })));
const DeviceDetailPage = lazy(() => import("../pages/DeviceDetailPage").then((m) => ({ default: m.DeviceDetailPage })));
const ReportsPage = lazy(() => import("../pages/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const ReportSettingsPage = lazy(() => import("../pages/ReportSettingsPage").then((m) => ({ default: m.ReportSettingsPage })));
const SessionsPage = lazy(() => import("../security/SessionsPage").then((m) => ({ default: m.SessionsPage })));

function AppShellNav() {
  const t = useT();
  const { activeId, tenants } = useTenant();
  const role = tenants.find((ten) => ten.id === activeId)?.role ?? null;
  return (
    <>
      <NavLink component={RouterNavLink} to="/" label={t.nav.overview} />
      <NavLink component={RouterNavLink} to="/devices" label={t.nav.devices} />
      <NavLink component={RouterNavLink} to="/alerts" label={t.nav.alerts} />
      <NavLink component={RouterNavLink} to="/reports" label={t.nav.reports} />
      {role === "tenant_admin" && (
        <NavLink component={RouterNavLink} to="/reports/settings" label={t.nav.reportSettings} />
      )}
      <NavLink component={RouterNavLink} to="/security/sessions" label={t.nav.sessions} />
    </>
  );
}

export function AppShell() {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const t = useT();

  async function logout() {
    await api.POST("/api/logout");
    qc.clear();
    refresh();
    navigate("/login");
  }

  return (
    <TenantProvider>
      <MantineAppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: "sm" }} padding="md">
        <MantineAppShell.Header>
          <Group h="100%" px="md" justify="space-between">
            <Group>
              <Text fw={700}>{t.common.appName}</Text>
              <TenantSwitcher />
            </Group>
            <Group>
              <Text size="sm">{me?.email}</Text>
              <Button size="xs" variant="light" onClick={logout}>{t.common.logout}</Button>
            </Group>
          </Group>
        </MantineAppShell.Header>
        <MantineAppShell.Navbar p="sm">
          <AppShellNav />
        </MantineAppShell.Navbar>
        <MantineAppShell.Main>
          <Suspense fallback={<Loader />}>
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/devices" element={<DevicesPage />} />
              <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/reports/settings" element={<ReportSettingsPage />} />
              <Route path="/security/sessions" element={<SessionsPage />} />
            </Routes>
          </Suspense>
        </MantineAppShell.Main>
      </MantineAppShell>
    </TenantProvider>
  );
}
