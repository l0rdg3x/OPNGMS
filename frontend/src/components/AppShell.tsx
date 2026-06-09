import { AppShell as MantineAppShell, Button, Group, NavLink, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { NavLink as RouterNavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { useT } from "../i18n";
import { TenantProvider } from "../tenant/TenantProvider";
import { OverviewPage } from "../pages/OverviewPage";
import { AlertsPage } from "../pages/AlertsPage";
import { DevicesPage } from "../pages/DevicesPage";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { TenantSwitcher } from "./TenantSwitcher";

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
          <NavLink component={RouterNavLink} to="/" label={t.nav.overview} />
          <NavLink component={RouterNavLink} to="/devices" label={t.nav.devices} />
          <NavLink component={RouterNavLink} to="/alerts" label={t.nav.alerts} />
        </MantineAppShell.Navbar>
        <MantineAppShell.Main>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/devices" element={<DevicesPage />} />
            <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
          </Routes>
        </MantineAppShell.Main>
      </MantineAppShell>
    </TenantProvider>
  );
}
