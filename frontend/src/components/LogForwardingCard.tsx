import { useState } from "react";
import { Alert, Badge, Button, Card, Group, Loader, Stack, Text, Title, Tooltip } from "@mantine/core";
import dayjs from "dayjs";

import { usePermissions } from "../auth/usePermissions";
import { useT } from "../i18n";
import {
  useDisableLogForwarding, useEnableLogForwarding, useLogForwardingStatus,
  useRevokeLogForwarding, useRotateLogForwarding,
} from "../logs/logForwardingHooks";
import { ConfirmModal } from "./ConfirmModal";

function liveness(lastLogAt: string | null | undefined): { color: string; label: string } {
  if (!lastLogAt) return { color: "gray", label: "no logs yet" };
  const mins = dayjs().diff(dayjs(lastLogAt), "minute");
  if (mins <= 15) return { color: "green", label: "active" };
  if (mins <= 60 * 24) return { color: "yellow", label: "quiet" };
  return { color: "gray", label: "stale" };
}

export function LogForwardingCard({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { isOperator: canWrite } = usePermissions();
  const status = useLogForwardingStatus(deviceId);
  const enable = useEnableLogForwarding(deviceId);
  const disable = useDisableLogForwarding(deviceId);
  const rotate = useRotateLogForwarding(deviceId);
  const revoke = useRevokeLogForwarding(deviceId);
  const [confirm, setConfirm] = useState<null | "enable" | "disable">(null);
  const [lifecycle, setLifecycle] = useState<null | "rotate" | "revoke">(null);

  if (status.isLoading) return <Loader data-testid="lf-loader" />;
  const s = status.data;
  const enabled = s?.enabled ?? false;
  const revoked = !enabled && !!s?.revoked_at;
  const live = liveness(s?.last_log_at);
  const expiry = s?.cert_not_after ? dayjs(s.cert_not_after) : null;
  const expSoon = expiry !== null && expiry.diff(dayjs(), "day") <= 30;
  const expired = expiry !== null && expiry.isBefore(dayjs());

  async function run(action: "enable" | "disable") {
    setConfirm(null);
    try {
      if (action === "enable") await enable.mutateAsync();
      else await disable.mutateAsync();
    } catch {
      // the mutation's isError drives the alert below
    }
  }

  async function runLifecycle(action: "rotate" | "revoke") {
    setLifecycle(null);
    try {
      if (action === "rotate") await rotate.mutateAsync();
      else await revoke.mutateAsync(null);
    } catch {
      // isError drives the alert
    }
  }

  return (
    <Card withBorder padding="md" radius="md">
      <Stack>
        <Group justify="space-between">
          <Title order={4}>{t.logForwarding.tab}</Title>
          <Badge color={enabled ? "green" : revoked ? "red" : "gray"} data-testid="lf-status">
            {enabled ? "Enabled" : revoked ? "Revoked" : "Disabled"}
          </Badge>
        </Group>

        {enabled && (
          <>
            <Text size="sm" c="dimmed">mTLS TLS syslog</Text>
            <Group gap="xs">
              <Text size="sm">Cert {s?.cert_fingerprint?.slice(0, 12)}…</Text>
              {expiry && (
                <Text size="sm" c={expired ? "red" : expSoon ? "yellow" : "dimmed"}>
                  {expired ? "expired" : "expires"} {expiry.format("YYYY-MM-DD")}
                </Text>
              )}
            </Group>
            <Group gap="xs" data-testid="lf-liveness">
              <Tooltip label="Time since the most recent log reached the lake">
                <Badge color={live.color} variant="dot">{live.label}</Badge>
              </Tooltip>
            </Group>
          </>
        )}

        {(enable.isError || disable.isError || rotate.isError || revoke.isError) && (
          <Alert color="red">The device rejected the change. Please retry.</Alert>
        )}

        {canWrite && (
          <Group>
            {!enabled && (
              <Button data-testid="lf-enable" loading={enable.isPending} onClick={() => setConfirm("enable")}>
                Enable
              </Button>
            )}
            {enabled && (
              <Button data-testid="lf-rotate" variant="light" loading={rotate.isPending}
                      onClick={() => setLifecycle("rotate")}>
                Rotate cert
              </Button>
            )}
            {enabled && (
              <Button data-testid="lf-revoke" color="red" loading={revoke.isPending}
                      onClick={() => setLifecycle("revoke")}>
                Revoke
              </Button>
            )}
            {enabled && (
              <Button data-testid="lf-disable" color="red" variant="light"
                      loading={disable.isPending} onClick={() => setConfirm("disable")}>
                Disable
              </Button>
            )}
          </Group>
        )}
      </Stack>

      <ConfirmModal
        opened={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => run(confirm!)}
        title={confirm === "enable" ? "Enable log forwarding?" : "Disable log forwarding?"}
        body={confirm === "enable"
          ? "This imports a client certificate and configures a TLS syslog target on the device."
          : "This removes the syslog target and certificate from the device."}
      />

      <ConfirmModal
        opened={lifecycle !== null}
        onClose={() => setLifecycle(null)}
        onConfirm={() => runLifecycle(lifecycle!)}
        title={lifecycle === "rotate" ? "Rotate certificate?" : "Revoke certificate?"}
        body={lifecycle === "rotate"
          ? "Issues a new client certificate and swaps it on the device — no logs are lost."
          : "Removes the certificate and marks it revoked. Re-enabling will issue a brand-new certificate."}
      />
    </Card>
  );
}
