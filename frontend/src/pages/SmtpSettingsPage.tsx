import { useEffect, useRef, useState } from "react";
import {
  Alert, Button, Card, Group, NumberInput, PasswordInput, SegmentedControl, Select, Stack, Switch,
  Text, TextInput, Title,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";

import { useAuth } from "../auth/useAuth";
import { useT } from "../i18n";
import { useSmtpSettings, useTestSmtp, useUpdateSmtpSettings } from "../admin/smtpHooks";

export function SmtpSettingsPage() {
  const { me } = useAuth();
  const t = useT();
  const query = useSmtpSettings();
  const update = useUpdateSmtpSettings();
  const test = useTestSmtp();
  const initialized = useRef(false);
  const [testTo, setTestTo] = useState("");

  const form = useForm({
    initialValues: {
      enabled: false, host: "", port: 587, security: "starttls", username: "",
      from_email: "", from_name: "", password: "",
      auth_method: "password", oauth_provider: "google", oauth_client_id: "",
      oauth_client_secret: "", oauth_refresh_token: "", oauth_tenant_id: "",
    },
  });

  useEffect(() => {
    if (query.data && !initialized.current) {
      form.setValues({
        enabled: query.data.enabled, host: query.data.host, port: query.data.port,
        security: query.data.security, username: query.data.username ?? "",
        from_email: query.data.from_email, from_name: query.data.from_name, password: "",
        auth_method: query.data.auth_method ?? "password",
        oauth_provider: query.data.oauth_provider ?? "google",
        oauth_client_id: query.data.oauth_client_id ?? "",
        oauth_client_secret: "", oauth_refresh_token: "",
        oauth_tenant_id: query.data.oauth_tenant_id ?? "",
      });
      initialized.current = true;
    }
  }, [query.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!me?.is_superadmin) {
    return <Alert color="red" data-testid="smtp-forbidden">Superadmin only.</Alert>;
  }

  const isOauth = form.values.auth_method === "oauth";
  const isMicrosoft = form.values.oauth_provider === "microsoft";

  function payload() {
    const v = form.values;
    const base = {
      enabled: v.enabled, host: v.host, port: v.port, security: v.security,
      from_email: v.from_email, from_name: v.from_name,
      // The SegmentedControl constrains this to exactly these two values.
      auth_method: v.auth_method as "password" | "oauth", clear_password: false,
      clear_client_secret: false, clear_refresh_token: false,
    };
    if (v.auth_method === "oauth") {
      return {
        ...base,
        username: null,
        // The provider Select constrains this to exactly these two values.
        oauth_provider: v.oauth_provider as "google" | "microsoft",
        oauth_client_id: v.oauth_client_id || null,
        oauth_tenant_id: v.oauth_provider === "microsoft" ? (v.oauth_tenant_id || null) : null,
        // Write-only secrets: send only when the field is non-empty (blank = keep existing).
        ...(v.oauth_client_secret ? { oauth_client_secret: v.oauth_client_secret } : {}),
        ...(v.oauth_refresh_token ? { oauth_refresh_token: v.oauth_refresh_token } : {}),
      };
    }
    return {
      ...base,
      username: v.username || null,
      ...(v.password ? { password: v.password } : {}),
    };
  }

  async function handleSave() {
    try {
      await update.mutateAsync(payload());
      form.setFieldValue("password", "");
      form.setFieldValue("oauth_client_secret", "");
      form.setFieldValue("oauth_refresh_token", "");
      notifications.show({ message: "SMTP settings saved" });
    } catch {
      notifications.show({ color: "red", message: "Failed to save SMTP settings" });
    }
  }

  async function handleTest() {
    try {
      const res = await test.mutateAsync({ ...payload(), to: testTo });
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
          <div>
            <Text size="sm" fw={500} mb={4}>{t.smtp.authMethod}</Text>
            <SegmentedControl
              data={[
                { value: "password", label: t.smtp.authPassword },
                { value: "oauth", label: t.smtp.authOauth },
              ]}
              {...form.getInputProps("auth_method")}
              data-testid="smtp-auth-method"
            />
          </div>
          {isOauth ? (
            <>
              <Select
                label={t.smtp.oauthProvider}
                data={[
                  { value: "google", label: t.smtp.oauthGoogle },
                  { value: "microsoft", label: t.smtp.oauthMicrosoft },
                ]}
                {...form.getInputProps("oauth_provider")}
                data-testid="smtp-oauth-provider"
              />
              <TextInput label={t.smtp.oauthClientId} {...form.getInputProps("oauth_client_id")} data-testid="smtp-oauth-client-id" />
              <PasswordInput
                label={t.smtp.oauthClientSecret}
                placeholder={query.data?.has_client_secret ? t.smtp.oauthSecretSaved : undefined}
                {...form.getInputProps("oauth_client_secret")}
                data-testid="smtp-oauth-client-secret"
              />
              <PasswordInput
                label={t.smtp.oauthRefreshToken}
                placeholder={query.data?.has_refresh_token ? t.smtp.oauthSecretSaved : undefined}
                {...form.getInputProps("oauth_refresh_token")}
                data-testid="smtp-oauth-refresh-token"
              />
              {isMicrosoft && (
                <TextInput label={t.smtp.oauthTenantId} {...form.getInputProps("oauth_tenant_id")} data-testid="smtp-oauth-tenant-id" />
              )}
            </>
          ) : (
            <>
              <TextInput label="Username" {...form.getInputProps("username")} data-testid="smtp-username" />
              <PasswordInput label={query.data?.has_password ? "Password (leave blank to keep)" : "Password"} {...form.getInputProps("password")} data-testid="smtp-password" />
            </>
          )}
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
