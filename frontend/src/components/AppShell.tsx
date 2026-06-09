import { AppShell as MantineAppShell, Button, Group, NavLink, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { NavLink as RouterNavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { TenantProvider } from "../tenant/TenantProvider";
import { DevicesPage } from "../pages/DevicesPage";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { TenantSwitcher } from "./TenantSwitcher";

export function AppShell() {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();

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
              <Text fw={700}>OPNGMS</Text>
              <TenantSwitcher />
            </Group>
            <Group>
              <Text size="sm">{me?.email}</Text>
              <Button size="xs" variant="light" onClick={logout}>Esci</Button>
            </Group>
          </Group>
        </MantineAppShell.Header>
        <MantineAppShell.Navbar p="sm">
          <NavLink component={RouterNavLink} to="/" label="Device" />
        </MantineAppShell.Navbar>
        <MantineAppShell.Main>
          <Routes>
            <Route path="/" element={<DevicesPage />} />
            <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
          </Routes>
        </MantineAppShell.Main>
      </MantineAppShell>
    </TenantProvider>
  );
}
