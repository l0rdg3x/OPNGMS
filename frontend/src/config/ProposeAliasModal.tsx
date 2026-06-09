import { Button, Modal, SegmentedControl, Select, Stack, TextInput, Textarea } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { useCreateChange } from "./changeHooks";

export function ProposeAliasModal({
  deviceId,
  opened,
  onClose,
}: {
  deviceId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const t = useT();
  const create = useCreateChange(deviceId);
  const form = useForm({
    initialValues: {
      operation: "set" as "add" | "set" | "delete",
      name: "",
      type: "host",
      content: "",
    },
  });

  async function submit(v: typeof form.values) {
    const content = v.content.split("\n").map((s) => s.trim()).filter(Boolean);
    try {
      await create.mutateAsync({
        kind: "alias",
        operation: v.operation,
        target: v.name,
        payload: { name: v.name, type: v.type, content },
      });
      form.reset();
      onClose();
    } catch {
      notifications.show({ color: "red", message: t.errors.configChangeAction });
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={t.config.changes.propose}>
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          <SegmentedControl
            data={[
              { label: t.config.changes.add, value: "add" },
              { label: t.config.changes.set, value: "set" },
              { label: t.config.changes.delete, value: "delete" },
            ]}
            {...form.getInputProps("operation")}
          />
          <TextInput
            label={t.config.changes.name}
            required
            {...form.getInputProps("name")}
          />
          <Select
            label={t.config.changes.type}
            required
            allowDeselect={false}
            data={["host", "network", "port", "url"]}
            {...form.getInputProps("type")}
          />
          <Textarea
            label={t.config.changes.content}
            rows={3}
            {...form.getInputProps("content")}
          />
          <Button type="submit" loading={create.isPending}>
            {t.config.changes.create}
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}
