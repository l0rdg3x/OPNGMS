import { Alert, Button, Modal, PasswordInput, Switch, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";

interface Props {
  tenantId: string;
  opened: boolean;
  onClose: () => void;
}

export function DeviceCreateModal({ tenantId, opened, onClose }: Props) {
  const t = useT();
  const qc = useQueryClient();
  const form = useForm({
    initialValues: {
      name: "", base_url: "", api_key: "", api_secret: "", verify_tls: true, tls_fingerprint: "",
    },
  });

  const mutation = useMutation({
    mutationFn: async (values: typeof form.values) => {
      const { tls_fingerprint, ...rest } = values;
      const fingerprint = tls_fingerprint.trim();
      const body = fingerprint ? { ...rest, tls_fingerprint: fingerprint } : rest;
      const { data, error } = await api.POST("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: tenantId } },
        body,
      });
      if (error) throw new Error("create failed");
      return data;
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["devices", tenantId] });
      notifications.show({ message: `${t.deviceCreate.created} (${(d as { status: string }).status})` });
      form.reset();
      onClose();
    },
    onError: () => notifications.show({ color: "red", message: t.deviceCreate.createFailed }),
  });

  return (
    <Modal opened={opened} onClose={onClose} title={t.deviceCreate.title}>
      <form onSubmit={form.onSubmit((v) => mutation.mutate(v))}>
        <TextInput label={t.deviceCreate.name} required {...form.getInputProps("name")} />
        <TextInput label={t.deviceCreate.url} required mt="sm" {...form.getInputProps("base_url")} />
        <TextInput label={t.deviceCreate.apiKey} required mt="sm" {...form.getInputProps("api_key")} />
        <PasswordInput label={t.deviceCreate.apiSecret} required mt="sm" {...form.getInputProps("api_secret")} />
        <Switch label={t.deviceCreate.verifyTls} mt="md" {...form.getInputProps("verify_tls", { type: "checkbox" })} />
        {!form.values.verify_tls && (
          <>
            <Alert color="yellow" mt="sm" data-testid="verify-tls-warning">
              {t.deviceCreate.tlsWarning}
            </Alert>
            <TextInput
              label={t.deviceCreate.tlsFingerprintLabel}
              placeholder={t.deviceCreate.tlsFingerprintPlaceholder}
              mt="sm"
              data-testid="tls-fingerprint"
              {...form.getInputProps("tls_fingerprint")}
            />
          </>
        )}
        <Button type="submit" mt="lg" loading={mutation.isPending}>{t.deviceCreate.save}</Button>
      </form>
    </Modal>
  );
}
