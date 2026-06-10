import { Badge, Button, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { useLogoutAll, useSessions } from "./sessionHooks";

export function SessionsPage() {
  const t = useT();
  const sessionsQuery = useSessions();
  const logoutAllMutation = useLogoutAll();

  async function handleLogoutAll() {
    try {
      await logoutAllMutation.mutateAsync();
    } catch {
      notifications.show({ color: "red", message: t.sessions.logoutAllError });
    }
  }

  if (sessionsQuery.isLoading) return <Loader />;
  if (sessionsQuery.error)
    return <Text c="red" data-testid="sessions-error">{t.sessions.loadError}</Text>;

  const sessions = sessionsQuery.data ?? [];

  return (
    <Stack>
      <Title order={3}>{t.sessions.pageTitle}</Title>

      <Table data-testid="sessions-table">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t.sessions.colLastSeen}</Table.Th>
            <Table.Th>{t.sessions.colCreated}</Table.Th>
            <Table.Th>{t.sessions.colExpires}</Table.Th>
            <Table.Th>{t.sessions.colIp}</Table.Th>
            <Table.Th>{t.sessions.colUserAgent}</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {sessions.map((sess) => (
            <Table.Tr key={sess.id} data-testid={`session-row-${sess.id}`}>
              <Table.Td>{new Date(sess.last_seen_at).toLocaleString()}</Table.Td>
              <Table.Td>{new Date(sess.created_at).toLocaleString()}</Table.Td>
              <Table.Td>{new Date(sess.expires_at).toLocaleString()}</Table.Td>
              <Table.Td>{sess.ip ?? "—"}</Table.Td>
              <Table.Td>{sess.user_agent ?? "—"}</Table.Td>
              <Table.Td>
                {sess.current && (
                  <Badge color="blue" data-testid="badge-current">
                    {t.sessions.current}
                  </Badge>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Button
        color="red"
        variant="light"
        loading={logoutAllMutation.isPending}
        onClick={handleLogoutAll}
        data-testid="btn-logout-all"
      >
        {t.sessions.logoutAll}
      </Button>
    </Stack>
  );
}
