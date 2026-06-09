import { Button, Container, Paper, PasswordInput, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Me } from "../auth/AuthProvider";
import { useAuth } from "../auth/useAuth";
import { useT } from "../i18n";

export function LoginPage() {
  const t = useT();
  const { me, setMe } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const form = useForm({ initialValues: { email: "", password: "" } });

  useEffect(() => {
    if (me) navigate("/", { replace: true });
  }, [me, navigate]);

  async function submit(values: { email: string; password: string }) {
    setError(null);
    const { data, response } = await api.POST("/api/login", { body: values });
    if (response.ok && data) {
      setMe(data as Me);
    } else {
      setError(t.login.invalidCredentials);
    }
  }

  return (
    <Container size={420} mt={80}>
      <Title order={2} ta="center" mb="lg">{t.login.title}</Title>
      <Paper withBorder shadow="sm" p="lg" radius="md">
        <form onSubmit={form.onSubmit(submit)}>
          <TextInput label={t.login.email} required {...form.getInputProps("email")} />
          <PasswordInput
            label={t.login.password}
            required
            mt="md"
            visibilityToggleButtonProps={{ "aria-label": t.login.passwordToggle }}
            {...form.getInputProps("password")}
          />
          {error && <div role="alert" style={{ color: "red", marginTop: 8 }}>{error}</div>}
          <Button type="submit" fullWidth mt="lg">{t.login.submit}</Button>
        </form>
      </Paper>
    </Container>
  );
}
