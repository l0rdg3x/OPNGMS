import { useEffect, useRef, useState } from "react";
import {
  Alert, Button, Card, Group, NumberInput, PasswordInput, Select, Stack, Switch, Text,
  TextInput, Title,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";

import { useAuth } from "../auth/useAuth";
import { useSmtpSettings, useTestSmtp, useUpdateSmtpSettings } from "../admin/smtpHooks";

export function SmtpSettingsPage() {
  const { me } = useAuth();
  const query = useSmtpSettings();
  const update = useUpdateSmtpSettings();
  const test = useTestSmtp();
  const initialized = useRef(false);
  const [testTo, setTestTo] = useState("");

  const form = useForm({
    initialValues: {
      enabled: false, host: "", port: 587, security: "starttls", username: "",
      from_email: "", from_name: "", password: "",
    },
  });

  useEffect(() => {
    if (query.data && !initialized.current) {
      form.setValues({
        enabled: query.data.enabled, host: query.data.host, port: query.data.port,
        security: query.data.security, username: query.data.username ?? "",
        from_email: query.data.from_email, from_name: query.data.from_name, password: "",
      });
      initialized.current = true;
    }
  }, [query.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!me?.is_superadmin) {
    return <Alert color="red" data-testid="smtp-forbidden">Superadmin only.</Alert>;
  }

  function payload() {
    const v = form.values;
    return {
      enabled: v.enabled, host: v.host, port: v.port, security: v.security,
      username: v.username || null, from_email: v.from_email, from_name: v.from_name,
      ...(v.password ? { password: v.password } : {}),
    };
  }

  async function handleSave() {
    try {
      await update.mutateAsync(payload() as never);
      form.setFieldValue("password", "");
      notifications.show({ message: "SMTP settings saved" });
    } catch {
      notifications.show({ color: "red", message: "Failed to save SMTP settings" });
    }
  }

  async function handleTest() {
    try {
      const res = await test.mutateAsync({ ...payload(), to: testTo } as never);
      notifications.show({ color: res.ok ? "green" : "red", message: res.ok ? "Test email sent" : `Test failed: ${res.detail}` });
    } catch {
      notifications.show({ color: "red", message: "Test send failed" });
    }
  }

  return (
    <Stack maw={520}>
      <Title order={3}>SMTP delivery</Title>
      <Text size="sm" c="dimmed">Outbound mail server for scheduled report delivery.</Text>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Switch label="Enable delivery" {...form.getInputProps("enabled", { type: "checkbox" })} data-testid="smtp-enabled" />
          <TextInput label="Host" {...form.getInputProps("host")} data-testid="smtp-host" />
          <Group grow>
            <NumberInput label="Port" {...form.getInputProps("port")} data-testid="smtp-port" />
            <Select label="Security" data={["starttls", "tls", "none"]} {...form.getInputProps("security")} data-testid="smtp-security" />
          </Group>
          <TextInput label="Username" {...form.getInputProps("username")} data-testid="smtp-username" />
          <PasswordInput label={query.data?.has_password ? "Password (leave blank to keep)" : "Password"} {...form.getInputProps("password")} data-testid="smtp-password" />
          <TextInput label="From email" {...form.getInputProps("from_email")} data-testid="smtp-from-email" />
          <TextInput label="From name" {...form.getInputProps("from_name")} data-testid="smtp-from-name" />
          <Group>
            <Button onClick={handleSave} loading={update.isPending} data-testid="smtp-save">Save</Button>
          </Group>
        </Stack>
      </Card>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Title order={5}>Send a test email</Title>
          <Group align="end">
            <TextInput label="Recipient" value={testTo} onChange={(e) => setTestTo(e.currentTarget.value)} data-testid="smtp-test-to" />
            <Button variant="light" onClick={handleTest} loading={test.isPending} disabled={!testTo} data-testid="smtp-test">Send test</Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
