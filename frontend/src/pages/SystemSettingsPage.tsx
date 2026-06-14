import { Alert, Card, Loader, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { RuntimeSettingsSection } from "../admin/RuntimeSettingsSection";
import { useLivePush, useSetLivePush } from "../admin/systemHooks";
import { useT } from "../i18n";

export function SystemSettingsPage() {
  const t = useT();
  const query = useLivePush();
  const update = useSetLivePush();

  async function handleToggle(enabled: boolean) {
    try {
      await update.mutateAsync(enabled);
      notifications.show({ message: enabled ? t.system.livePush.enabled : t.system.livePush.disabled });
    } catch {
      notifications.show({ color: "red", message: t.system.livePush.updateError });
    }
  }

  return (
    <Stack maw={560}>
      <Title order={3}>{t.system.title}</Title>
      <Text size="sm" c="dimmed">{t.system.subtitle}</Text>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          {query.isLoading ? (
            <Loader size="sm" data-testid="live-push-loading" />
          ) : query.isError ? (
            <Alert color="red" data-testid="live-push-error">{t.system.livePush.loadError}</Alert>
          ) : (
            <>
              <Switch
                label={t.system.livePush.label}
                checked={query.data?.enabled ?? false}
                onChange={(e) => handleToggle(e.currentTarget.checked)}
                disabled={update.isPending}
                data-testid="live-push-switch"
              />
              <Text size="sm" c="dimmed">{t.system.livePush.help}</Text>
            </>
          )}
        </Stack>
      </Card>
      <RuntimeSettingsSection />
    </Stack>
  );
}
