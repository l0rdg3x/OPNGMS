import { Button, Group, Modal, MultiSelect, Stack, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useEffect } from "react";
import { useT } from "../i18n";
import { useTemplates } from "../templates/hooks";
import { type Profile, useCreateProfile, useUpdateProfile } from "./hooks";

export function ProfileFormModal(
  { opened, onClose, editing }: { opened: boolean; onClose: () => void; editing: Profile | null },
) {
  const t = useT();
  const { data: templates } = useTemplates();
  const create = useCreateProfile();
  const update = useUpdateProfile();
  const form = useForm<{ name: string; description: string; template_ids: string[] }>({
    initialValues: { name: "", description: "", template_ids: [] },
  });

  useEffect(() => {
    if (opened) {
      form.setValues(editing
        ? { name: editing.name, description: editing.description ?? "",
            template_ids: editing.template_ids ?? [] }
        : { name: "", description: "", template_ids: [] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, editing]);

  const options = (templates ?? []).map((tpl) => ({ value: tpl.id, label: tpl.name }));

  async function submit(v: typeof form.values) {
    const body = { name: v.name, description: v.description, template_ids: v.template_ids };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, body });
        notifications.show({ message: t.templates.profiles.updated });
      } else {
        await create.mutateAsync(body);
        notifications.show({ message: t.templates.profiles.created });
      }
      onClose();
    } catch {
      notifications.show({ color: "red", message: t.templates.profiles.saveFailed });
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={editing ? t.templates.profiles.edit : t.templates.profiles.create}
           transitionProps={{ duration: 0 }} data-testid="prof-modal">
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          <TextInput label={t.templates.profiles.name} required data-testid="prof-name"
                     {...form.getInputProps("name")} />
          <TextInput label={t.templates.profiles.description} data-testid="prof-desc"
                     {...form.getInputProps("description")} />
          <MultiSelect label={t.templates.profiles.members} data={options} searchable
                       data-testid="prof-members" {...form.getInputProps("template_ids")} />
          <Group justify="flex-end">
            <Button type="submit" loading={create.isPending || update.isPending} data-testid="prof-save">
              {t.templates.profiles.save}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
