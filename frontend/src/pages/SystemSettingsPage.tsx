import { Alert, Card, Loader, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useLivePush, useSetLivePush } from "../admin/systemHooks";

export function SystemSettingsPage() {
  const query = useLivePush();
  const update = useSetLivePush();

  async function handleToggle(enabled: boolean) {
    try {
      await update.mutateAsync(enabled);
      notifications.show({ message: `Live config push ${enabled ? "enabled" : "disabled"}` });
    } catch {
      notifications.show({ color: "red", message: "Failed to update live config push" });
    }
  }

  return (
    <Stack maw={560}>
      <Title order={3}>System</Title>
      <Text size="sm" c="dimmed">Platform-wide runtime settings.</Text>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          {query.isLoading ? (
            <Loader size="sm" data-testid="live-push-loading" />
          ) : query.isError ? (
            <Alert color="red" data-testid="live-push-error">Failed to load the live-push setting.</Alert>
          ) : (
            <>
              <Switch
                label="Live config push to devices"
                checked={query.data?.enabled ?? false}
                onChange={(e) => handleToggle(e.currentTarget.checked)}
                disabled={update.isPending}
                data-testid="live-push-switch"
              />
              <Text size="sm" c="dimmed">
                When ON, applying a config change writes to the real device. When OFF, changes are dry-run only.
              </Text>
            </>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
