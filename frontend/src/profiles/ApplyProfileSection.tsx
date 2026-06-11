import { Button, Card, Code, Group, Modal, Select, Stack, Text, Title } from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { useApplyProfile, usePreviewProfile, useProfiles } from "./hooks";

export function ApplyProfileSection({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { data: profiles } = useProfiles();
  const [profileId, setProfileId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [when, setWhen] = useState<string | null>(null);

  const preview = usePreviewProfile(deviceId);
  const apply = useApplyProfile(deviceId);

  // The ordered list of member previews, one line per template.
  const previewLines = preview.data?.map((p) => {
    const n = p.new as { name?: string; content?: string[] };
    return (n.name ?? "") + ": " + (n.content ?? []).join(", ");
  });

  function runPreview() {
    if (!profileId) return;
    preview.mutate(profileId, {
      onError: () => notifications.show({ color: "red", message: t.templates.profiles.apply.failed }),
    });
  }

  function openConfirm() {
    setWhen(null);
    setConfirming(true);
  }

  async function fire(scheduled: boolean) {
    if (!profileId) return;
    const scheduled_at = scheduled && when ? new Date(when.replace(" ", "T")).toISOString() : null;
    try {
      await apply.mutateAsync({ profileId, scheduled_at });
      notifications.show({ message: t.templates.profiles.apply.queued });
    } catch {
      notifications.show({ color: "red", message: t.templates.profiles.apply.failed });
    } finally {
      setConfirming(false);
    }
  }

  if (!profiles || profiles.length === 0) {
    return (
      <Text c="dimmed" mt="md">
        {t.templates.profiles.apply.empty}
      </Text>
    );
  }

  return (
    <Stack mt="md">
      <Card withBorder>
        <Title order={5} mb="xs">
          {t.templates.profiles.apply.title}
        </Title>
        <Stack>
          <Select
            label={t.templates.profiles.apply.pick}
            placeholder={t.templates.profiles.apply.pick}
            data={profiles.map((p) => ({ value: p.id, label: p.name }))}
            value={profileId}
            onChange={setProfileId}
            data-testid="prof-pick"
          />
          {profileId && (
            <Group>
              <Button
                variant="light"
                onClick={runPreview}
                loading={preview.isPending}
                data-testid="prof-preview"
              >
                {t.templates.profiles.apply.preview}
              </Button>
              <Button onClick={openConfirm} data-testid="btn-prof-apply">
                {t.templates.profiles.apply.title}
              </Button>
            </Group>
          )}
        </Stack>
      </Card>

      {previewLines && (
        <Card withBorder data-testid="prof-preview-out">
          <Title order={6} mb="xs">
            {t.templates.profiles.apply.previewTitle}
          </Title>
          {previewLines.map((line, i) => (
            <Code key={i} block>
              {line}
            </Code>
          ))}
        </Card>
      )}

      <Modal
        opened={confirming}
        onClose={() => setConfirming(false)}
        title={t.confirm.title}
        data-testid="prof-confirm-modal"
        transitionProps={{ duration: 0 }}
      >
        <Stack>
          <Text>{t.templates.profiles.apply.applyConfirm}</Text>
          <DateTimePicker
            label={t.templates.profiles.apply.scheduleAt}
            value={when}
            onChange={setWhen}
            minDate={new Date()}
            clearable
            data-testid="prof-schedule-picker"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)} data-testid="btn-prof-cancel">
              {t.confirm.cancel}
            </Button>
            <Button
              variant="light"
              onClick={() => fire(false)}
              loading={apply.isPending}
              data-testid="btn-prof-apply-now"
            >
              {t.templates.profiles.apply.runNow}
            </Button>
            <Button
              onClick={() => fire(true)}
              disabled={!when}
              loading={apply.isPending}
              data-testid="btn-prof-apply-schedule"
            >
              {t.templates.profiles.apply.schedule}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
