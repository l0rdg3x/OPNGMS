import { Alert, Button, Group, Stack, Table, Text } from "@mantine/core";
import { useState } from "react";
import { ConfirmModal } from "../components/ConfirmModal";
import { useT } from "../i18n";
import {
  useRevokeTrustedDevice,
  useRevokeAllTrustedDevices,
  useTrustedDevices,
  type TrustedDevice,
} from "./trustedDeviceHooks";

export function TrustedDevicesSection() {
  const t = useT();
  const devicesQuery = useTrustedDevices(true);
  const revoke = useRevokeTrustedDevice();
  const revokeAll = useRevokeAllTrustedDevices();
  const [target, setTarget] = useState<TrustedDevice | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  async function doRevoke() {
    if (!target) return;
    setRevokeError(null);
    try {
      await revoke.mutateAsync(target.id);
    } catch {
      setRevokeError(t.mfa.trustedDevices.revokeError);
    } finally {
      setTarget(null);
    }
  }

  async function doRevokeAll() {
    setRevokeError(null);
    try {
      await revokeAll.mutateAsync();
    } catch {
      setRevokeError(t.mfa.trustedDevices.revokeError);
    }
  }

  const devices = devicesQuery.data ?? [];

  return (
    <Stack gap="md" data-testid="mfa-trusted-devices">
      <Text fw={600}>{t.mfa.trustedDevices.title}</Text>
      <Text size="sm" c="dimmed">{t.mfa.trustedDevices.intro}</Text>

      {devicesQuery.error && (
        <Alert color="red" role="alert">{t.mfa.trustedDevices.loadError}</Alert>
      )}

      {revokeError && (
        <Text role="alert" c="red.5" size="sm">{revokeError}</Text>
      )}

      {devices.length === 0 && !devicesQuery.error ? (
        <Text size="sm" c="dimmed">{t.mfa.trustedDevices.empty}</Text>
      ) : (
        <Table data-testid="trusted-devices-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.mfa.trustedDevices.colDevice}</Table.Th>
              <Table.Th>{t.mfa.trustedDevices.colIp}</Table.Th>
              <Table.Th>{t.mfa.trustedDevices.colLastUsed}</Table.Th>
              <Table.Th>{t.mfa.trustedDevices.colExpires}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {devices.map((d) => (
              <Table.Tr key={d.id} data-testid={`trusted-device-row-${d.id}`}>
                <Table.Td>{d.user_agent ?? t.mfa.trustedDevices.unknownDevice}</Table.Td>
                <Table.Td>{d.ip ?? "—"}</Table.Td>
                <Table.Td>{new Date(d.last_used_at).toLocaleString()}</Table.Td>
                <Table.Td>{new Date(d.expires_at).toLocaleString()}</Table.Td>
                <Table.Td>
                  <Button
                    size="xs"
                    variant="light"
                    color="red"
                    onClick={() => {
                      setRevokeError(null);
                      setTarget(d);
                    }}
                    data-testid={`trusted-device-revoke-${d.id}`}
                  >
                    {t.mfa.trustedDevices.revoke}
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {devices.length > 0 && (
        <Group>
          <Button
            size="xs"
            variant="light"
            color="red"
            onClick={doRevokeAll}
            loading={revokeAll.isPending}
            data-testid="trusted-devices-revoke-all"
          >
            {t.mfa.trustedDevices.revokeAll}
          </Button>
        </Group>
      )}

      <ConfirmModal
        opened={target !== null}
        onClose={() => setTarget(null)}
        onConfirm={doRevoke}
        title={t.mfa.trustedDevices.confirmTitle}
        body={t.mfa.trustedDevices.confirmBody}
        confirmLabel={t.mfa.trustedDevices.revoke}
        loading={revoke.isPending}
      />
    </Stack>
  );
}
