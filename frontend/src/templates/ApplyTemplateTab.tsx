import {
  Button,
  Card,
  Code,
  Group,
  Modal,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { type Template, useTemplates } from "./hooks";
import { useApplyTemplate, usePreviewTemplate, useUpsertOverride } from "./applyHooks";

/** Split a textarea value into a trimmed, empty-filtered content list. */
function parseContent(value: string): string[] {
  return value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** The library template's content lines (the override textarea prefill). */
function templateContent(tpl: Template | null | undefined): string {
  const body = tpl?.body as { content?: string[] } | undefined;
  return Array.isArray(body?.content) ? body.content.join("\n") : "";
}

export function ApplyTemplateTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { data: templates } = useTemplates();
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [override, setOverride] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [when, setWhen] = useState<string | null>(null);

  // Prefill the override textarea from the newly-picked template's content.
  function pickTemplate(id: string | null) {
    setTemplateId(id);
    setOverride(templateContent(templates?.find((tpl) => tpl.id === id)));
  }

  const upsert = useUpsertOverride(templateId ?? "");
  const preview = usePreviewTemplate(deviceId);
  const apply = useApplyTemplate(deviceId);

  const previewOut = preview.data?.new as { name?: string; content?: string[] } | undefined;

  async function saveOverride() {
    if (!templateId) return;
    try {
      await upsert.mutateAsync({ content: parseContent(override) });
      notifications.show({ message: t.templates.apply.overrideSaved });
    } catch {
      notifications.show({ color: "red", message: t.templates.apply.failed });
    }
  }

  function runPreview() {
    if (!templateId) return;
    preview.mutate(templateId, {
      onError: () => notifications.show({ color: "red", message: t.templates.apply.failed }),
    });
  }

  function openConfirm() {
    setWhen(null);
    setConfirming(true);
  }

  async function fire(scheduled: boolean) {
    if (!templateId) return;
    const scheduled_at = scheduled && when ? new Date(when.replace(" ", "T")).toISOString() : null;
    try {
      await apply.mutateAsync({ templateId, scheduled_at });
      notifications.show({ message: t.templates.apply.queued });
    } catch {
      notifications.show({ color: "red", message: t.templates.apply.failed });
    } finally {
      setConfirming(false);
    }
  }

  if (!templates || templates.length === 0) {
    return (
      <Text c="dimmed" mt="md">
        {t.templates.apply.none}
      </Text>
    );
  }

  return (
    <Stack mt="md">
      <Card withBorder>
        <Title order={5} mb="xs">
          {t.templates.apply.title}
        </Title>
        <Stack>
          <Select
            label={t.templates.apply.pick}
            placeholder={t.templates.apply.pick}
            data={templates.map((tpl) => ({ value: tpl.id, label: tpl.name }))}
            value={templateId}
            onChange={pickTemplate}
            data-testid="tpl-pick"
          />
          {templateId && (
            <>
              <Textarea
                label={t.templates.apply.override}
                rows={4}
                value={override}
                onChange={(e) => setOverride(e.currentTarget.value)}
                data-testid="tpl-override"
              />
              <Group>
                <Button
                  variant="light"
                  onClick={saveOverride}
                  loading={upsert.isPending}
                  data-testid="tpl-override-save"
                >
                  {t.templates.apply.saveOverride}
                </Button>
                <Button
                  variant="light"
                  onClick={runPreview}
                  loading={preview.isPending}
                  data-testid="tpl-preview"
                >
                  {t.templates.apply.preview}
                </Button>
                <Button onClick={openConfirm} data-testid="btn-tpl-apply">
                  {t.templates.apply.title}
                </Button>
              </Group>
            </>
          )}
        </Stack>
      </Card>

      {previewOut && (
        <Card withBorder data-testid="tpl-preview-out">
          <Title order={6} mb="xs">
            {t.templates.apply.previewTitle}
          </Title>
          {previewOut.name && <Text fw={500}>{previewOut.name}</Text>}
          <Code block>{(previewOut.content ?? []).join("\n")}</Code>
        </Card>
      )}

      <Modal
        opened={confirming}
        onClose={() => setConfirming(false)}
        title={t.confirm.title}
        data-testid="tpl-confirm-modal"
        transitionProps={{ duration: 0 }}
      >
        <Stack>
          <Text>{t.templates.apply.applyConfirm}</Text>
          <DateTimePicker
            label={t.templates.apply.scheduleAt}
            value={when}
            onChange={setWhen}
            minDate={new Date()}
            clearable
            data-testid="tpl-schedule-picker"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)} data-testid="btn-tpl-cancel">
              {t.confirm.cancel}
            </Button>
            <Button
              variant="light"
              onClick={() => fire(false)}
              loading={apply.isPending}
              data-testid="btn-tpl-apply-now"
            >
              {t.templates.apply.runNow}
            </Button>
            <Button
              onClick={() => fire(true)}
              disabled={!when}
              loading={apply.isPending}
              data-testid="btn-tpl-apply-schedule"
            >
              {t.templates.apply.schedule}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
