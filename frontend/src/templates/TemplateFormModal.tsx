import { Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useEffect } from "react";
import { useT } from "../i18n";
import { type Template, useCreateTemplate, useUpdateTemplate } from "./hooks";

const ALIAS_TYPES = ["host", "network", "port", "url", "urltable", "geoip", "networkgroup", "mac", "dynipv6host"];

export function TemplateFormModal(
  { opened, onClose, editing }: { opened: boolean; onClose: () => void; editing: Template | null },
) {
  const t = useT();
  const create = useCreateTemplate();
  const update = useUpdateTemplate();
  const form = useForm({
    initialValues: { name: "", type: "host", content: "", description: "" },
  });

  useEffect(() => {
    if (opened) {
      form.setValues(editing
        ? { name: editing.name, type: String(editing.body?.type ?? "host"),
            content: (Array.isArray(editing.body?.content) ? editing.body.content : []).join("\n"),
            description: editing.description ?? "" }
        : { name: "", type: "host", content: "", description: "" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, editing]);

  async function submit(v: typeof form.values) {
    const content = v.content.split("\n").map((s) => s.trim()).filter(Boolean);
    const body = { name: v.name, type: v.type, content, description: v.description };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, body: { name: v.name, description: v.description, body } });
        notifications.show({ message: t.templates.updated });
      } else {
        await create.mutateAsync({ kind: "firewall_alias", name: v.name, description: v.description, body });
        notifications.show({ message: t.templates.created });
      }
      onClose();
    } catch {
      notifications.show({ color: "red", message: t.templates.saveFailed });
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={editing ? t.templates.edit : t.templates.create}
           transitionProps={{ duration: 0 }} data-testid="tpl-modal">
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          <TextInput label={t.templates.name} required data-testid="tpl-name" {...form.getInputProps("name")} />
          <Select label={t.templates.type} data={ALIAS_TYPES} data-testid="tpl-type" {...form.getInputProps("type")} />
          <Textarea label={t.templates.content} rows={4} required data-testid="tpl-content"
                    {...form.getInputProps("content")} />
          <TextInput label={t.templates.description} data-testid="tpl-desc" {...form.getInputProps("description")} />
          <Group justify="flex-end">
            <Button type="submit" loading={create.isPending || update.isPending} data-testid="tpl-save">
              {t.templates.save}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
