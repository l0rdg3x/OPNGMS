import { Button, Modal, PasswordInput, Switch, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

interface Props {
  tenantId: string;
  opened: boolean;
  onClose: () => void;
}

export function DeviceCreateModal({ tenantId, opened, onClose }: Props) {
  const qc = useQueryClient();
  const form = useForm({
    initialValues: { name: "", base_url: "", api_key: "", api_secret: "", verify_tls: true },
  });

  const mutation = useMutation({
    mutationFn: async (values: typeof form.values) => {
      const { data, error } = await api.POST("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: tenantId } },
        body: values,
      });
      if (error) throw new Error("create failed");
      return data;
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["devices", tenantId] });
      notifications.show({ message: `Device creato (${(d as { status: string }).status})` });
      form.reset();
      onClose();
    },
    onError: () => notifications.show({ color: "red", message: "Creazione fallita" }),
  });

  return (
    <Modal opened={opened} onClose={onClose} title="Aggiungi device">
      <form onSubmit={form.onSubmit((v) => mutation.mutate(v))}>
        <TextInput label="Nome" required {...form.getInputProps("name")} />
        <TextInput label="URL (https)" required mt="sm" {...form.getInputProps("base_url")} />
        <TextInput label="API key" required mt="sm" {...form.getInputProps("api_key")} />
        <PasswordInput label="API secret" required mt="sm" {...form.getInputProps("api_secret")} />
        <Switch label="Verifica TLS" mt="md" {...form.getInputProps("verify_tls", { type: "checkbox" })} />
        <Button type="submit" mt="lg" loading={mutation.isPending}>Salva</Button>
      </form>
    </Modal>
  );
}
