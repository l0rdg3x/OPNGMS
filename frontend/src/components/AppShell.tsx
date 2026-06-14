import { AppShell as MantineAppShell, Box, Button, Group, Loader, NavLink, Stack, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, NavLink as RouterNavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePermissions } from "../auth/usePermissions";
import { useT } from "../i18n";
import { TenantProvider } from "../tenant/TenantProvider";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { TenantSwitcher } from "./TenantSwitcher";

// Heavy inner pages are lazy-loaded to split the initial JS bundle.
const OverviewPage = lazy(() => import("../pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const AlertsPage = lazy(() => import("../pages/AlertsPage").then((m) => ({ default: m.AlertsPage })));
const PerimeterPage = lazy(() => import("../pages/PerimeterPage").then((m) => ({ default: m.PerimeterPage })));
const DevicesPage = lazy(() => import("../pages/DevicesPage").then((m) => ({ default: m.DevicesPage })));
const DeviceDetailPage = lazy(() => import("../pages/DeviceDetailPage").then((m) => ({ default: m.DeviceDetailPage })));
const ReportsPage = lazy(() => import("../pages/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const ReportSettingsPage = lazy(() => import("../pages/ReportSettingsPage").then((m) => ({ default: m.ReportSettingsPage })));
const ReportSchedulePage = lazy(() => import("../pages/ReportSchedulePage").then((m) => ({ default: m.ReportSchedulePage })));
const LogsPage = lazy(() => import("../pages/LogsPage").then((m) => ({ default: m.LogsPage })));
const SessionsPage = lazy(() => import("../security/SessionsPage").then((m) => ({ default: m.SessionsPage })));
const MfaPage = lazy(() => import("../security/MfaPage").then((m) => ({ default: m.MfaPage })));
const TemplateLibraryPage = lazy(() => import("../pages/TemplateLibraryPage").then((m) => ({ default: m.TemplateLibraryPage })));
const SmtpSettingsPage = lazy(() => import("../pages/SmtpSettingsPage").then((m) => ({ default: m.SmtpSettingsPage })));
const LogFleetPage = lazy(() => import("../pages/LogFleetPage").then((m) => ({ default: m.LogFleetPage })));
const SystemSettingsPage = lazy(() => import("../pages/SystemSettingsPage").then((m) => ({ default: m.SystemSettingsPage })));
const GroupsPage = lazy(() => import("../pages/GroupsPage").then((m) => ({ default: m.GroupsPage })));
const AuditPage = lazy(() => import("../pages/AuditPage").then((m) => ({ default: m.AuditPage })));

// ── Inline icon set (stroke, currentColor) — keeps the bundle dependency-free ──
const ic = {
  width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
};
const IconOverview = () => (<svg {...ic}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>);
const IconDevices = () => (<svg {...ic}><rect x="3" y="4" width="18" height="6" rx="1.5" /><rect x="3" y="14" width="18" height="6" rx="1.5" /><path d="M7 7h.01M7 17h.01" /></svg>);
const IconAlerts = () => (<svg {...ic}><path d="M10.3 3.6 1.8 18a1.5 1.5 0 0 0 1.3 2.2h17.8a1.5 1.5 0 0 0 1.3-2.2L13.7 3.6a1.5 1.5 0 0 0-2.6 0z" /><path d="M12 9v4M12 17h.01" /></svg>);
const IconReports = () => (<svg {...ic}><path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></svg>);
const IconPerimeter = () => (<svg {...ic}><path d="M12 2 4 5v6c0 5 3.4 8.4 8 11 4.6-2.6 8-6 8-11V5z" /><path d="M9 12l2 2 4-4" /></svg>);
const IconSettings = () => (<svg {...ic}><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2.4" /><circle cx="8" cy="17" r="2.4" /></svg>);
const IconSessions = () => (<svg {...ic}><path d="M12 2 4 5v6c0 5 3.4 8.4 8 11 4.6-2.6 8-6 8-11V5z" /><circle cx="12" cy="10" r="2.2" /><path d="M12 12.2V15" /></svg>);
const IconMfa = () => (<svg {...ic}><circle cx="8" cy="14" r="3.4" /><path d="M10.4 11.6 19 3M16 6l2.5 2.5M14 8l2.5 2.5" /></svg>);
const IconTemplates = () => (<svg {...ic}><path d="m12 3 9 5-9 5-9-5 9-5z" /><path d="m3 13 9 5 9-5M3 17l9 5 9-5" /></svg>);
const IconSmtp = () => (<svg {...ic}><rect x="3" y="5" width="18" height="14" rx="1.5" /><path d="m3 7 9 6 9-6" /></svg>);
const IconSchedule = () => (<svg {...ic}><rect x="3" y="4" width="18" height="17" rx="1.5" /><path d="M3 9h18M8 2v4M16 2v4" /><path d="M12 13v3l2 1.5" /></svg>);
const IconLogs = () => (<svg {...ic}><rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M8 8h8M8 12h8M8 16h5" /></svg>);
const IconGroups = () => (<svg {...ic}><circle cx="9" cy="8" r="3" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" /><path d="M16 5.2a3 3 0 0 1 0 5.6M18 14c2 .8 3.5 2.7 3.5 5" /></svg>);
const IconAudit = () => (<svg {...ic}><path d="M12 8v4l2.5 1.5" /><path d="M3.05 11a9 9 0 1 1 .5 4" /><path d="M3 5v4h4" /></svg>);

function NavItem({ to, label, icon }: { to: string; label: string; icon: ReactNode }) {
  return <NavLink component={RouterNavLink} to={to} end={to === "/"} label={label} leftSection={icon} />;
}

// Route guard for superadmin-only pages: a non-superadmin who navigates directly to /admin/* is
// redirected home (the nav links are already hidden, but the routes themselves were unguarded —
// LogFleetPage/SystemSettingsPage had no in-page check and would otherwise render + fire queries).
function RequireSuperadmin({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  if (!me?.is_superadmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppShellNav() {
  const t = useT();
  const { me } = useAuth();
  const { isTenantAdmin, isOperator } = usePermissions();
  return (
    <Stack gap={2}>
      <NavItem to="/" label={t.nav.overview} icon={<IconOverview />} />
      <NavItem to="/devices" label={t.nav.devices} icon={<IconDevices />} />
      <NavItem to="/alerts" label={t.nav.alerts} icon={<IconAlerts />} />
      <NavItem to="/perimeter" label={t.nav.perimeter} icon={<IconPerimeter />} />
      <NavItem to="/reports" label={t.nav.reports} icon={<IconReports />} />
      {isOperator && (
        <NavItem to="/logs" label={t.nav.logs} icon={<IconLogs />} />
      )}
      {isTenantAdmin && (
        <NavItem to="/reports/settings" label={t.nav.reportSettings} icon={<IconSettings />} />
      )}
      {isTenantAdmin && (
        <NavItem to="/reports/schedule" label={t.nav.reportSchedule} icon={<IconSchedule />} />
      )}
      <NavItem to="/security/sessions" label={t.nav.sessions} icon={<IconSessions />} />
      <NavItem to="/security/mfa" label={t.nav.mfa} icon={<IconMfa />} />
      {me?.is_superadmin && (
        <NavItem to="/admin/groups" label={t.nav.groups} icon={<IconGroups />} />
      )}
      {me?.is_superadmin && (
        <NavItem to="/admin/templates" label={t.nav.templates} icon={<IconTemplates />} />
      )}
      {me?.is_superadmin && (
        <NavItem to="/admin/smtp" label={t.nav.smtp} icon={<IconSmtp />} />
      )}
      {me?.is_superadmin && (
        <NavItem to="/admin/log-fleet" label={t.nav.logFleet} icon={<IconLogs />} />
      )}
      {me?.is_superadmin && (
        <NavItem to="/admin/system" label={t.nav.system} icon={<IconSettings />} />
      )}
      {me?.is_superadmin && (
        <NavItem to="/admin/audit" label={t.nav.audit} icon={<IconAudit />} />
      )}
    </Stack>
  );
}

function Wordmark() {
  return (
    <Group gap={9} wrap="nowrap">
      <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
        <defs>
          <linearGradient id="noc-mark" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#54ddc2" />
            <stop offset="1" stopColor="#0b8572" />
          </linearGradient>
        </defs>
        <path d="M12 2 3.5 6v6c0 5 3.6 9 8.5 10 4.9-1 8.5-5 8.5-10V6z" fill="url(#noc-mark)" opacity="0.18" />
        <path d="M12 2 3.5 6v6c0 5 3.6 9 8.5 10 4.9-1 8.5-5 8.5-10V6z" fill="none" stroke="url(#noc-mark)" strokeWidth="1.6" />
        <path d="M8 12.2l2.6 2.6L16 9.4" fill="none" stroke="#9ff2e2" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <Text fw={700} size="lg" style={{ letterSpacing: "-0.02em" }}>
        OPN<span style={{ color: "var(--noc-accent)" }}>GMS</span>
      </Text>
    </Group>
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
      <MantineAppShell header={{ height: 60 }} navbar={{ width: 248, breakpoint: "sm" }} padding="lg">
        <MantineAppShell.Header>
          <Group h="100%" px="lg" justify="space-between" wrap="nowrap">
            <Group gap="xl" wrap="nowrap">
              <Wordmark />
              <TenantSwitcher />
            </Group>
            <Group gap="sm" wrap="nowrap">
              <Text size="sm" c="dimmed" className="noc-mono" visibleFrom="xs">{me?.email}</Text>
              <LanguageSwitcher w={140} size="xs" />
              <Button size="xs" variant="default" onClick={logout}>{t.common.logout}</Button>
            </Group>
          </Group>
        </MantineAppShell.Header>
        <MantineAppShell.Navbar p="md">
          <Box mb="xs" px="xs">
            <Text className="noc-eyebrow">Console</Text>
          </Box>
          <AppShellNav />
        </MantineAppShell.Navbar>
        <MantineAppShell.Main>
          <Suspense fallback={<Loader />}>
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/devices" element={<DevicesPage />} />
              <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/perimeter" element={<PerimeterPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/reports/settings" element={<ReportSettingsPage />} />
              <Route path="/reports/schedule" element={<ReportSchedulePage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/security/sessions" element={<SessionsPage />} />
              <Route path="/security/mfa" element={<MfaPage />} />
              <Route path="/admin/groups" element={<RequireSuperadmin><GroupsPage /></RequireSuperadmin>} />
              <Route path="/admin/templates" element={<RequireSuperadmin><TemplateLibraryPage /></RequireSuperadmin>} />
              <Route path="/admin/smtp" element={<RequireSuperadmin><SmtpSettingsPage /></RequireSuperadmin>} />
              <Route path="/admin/log-fleet" element={<RequireSuperadmin><LogFleetPage /></RequireSuperadmin>} />
              <Route path="/admin/system" element={<RequireSuperadmin><SystemSettingsPage /></RequireSuperadmin>} />
              <Route path="/admin/audit" element={<RequireSuperadmin><AuditPage /></RequireSuperadmin>} />
            </Routes>
          </Suspense>
        </MantineAppShell.Main>
      </MantineAppShell>
    </TenantProvider>
  );
}
