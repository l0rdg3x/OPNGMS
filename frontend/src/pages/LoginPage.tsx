import { Anchor, Box, Button, Center, Paper, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import type { Me } from "../auth/AuthProvider";
import { useAuth } from "../auth/useAuth";
import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { useT } from "../i18n";

type LoginOut = components["schemas"]["LoginOut"];

function BrandMark() {
  return (
    <svg width="42" height="42" viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="login-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#54ddc2" />
          <stop offset="1" stopColor="#0b8572" />
        </linearGradient>
      </defs>
      <path d="M12 2 3.5 6v6c0 5 3.6 9 8.5 10 4.9-1 8.5-5 8.5-10V6z" fill="url(#login-mark)" opacity="0.18" />
      <path d="M12 2 3.5 6v6c0 5 3.6 9 8.5 10 4.9-1 8.5-5 8.5-10V6z" fill="none" stroke="url(#login-mark)" strokeWidth="1.6" />
      <path d="M8 12.2l2.6 2.6L16 9.4" fill="none" stroke="#9ff2e2" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function LoginPage() {
  const t = useT();
  const { me, setMe } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // null = password step; "mfa" = second-factor challenge step
  const [step, setStep] = useState<"password" | "mfa">("password");
  const [useRecovery, setUseRecovery] = useState(false);
  const form = useForm({ initialValues: { email: "", password: "" } });
  const mfaForm = useForm({ initialValues: { code: "" } });

  useEffect(() => {
    if (me) navigate("/", { replace: true });
  }, [me, navigate]);

  async function submit(values: { email: string; password: string }) {
    setError(null);
    const { data, response } = await api.POST("/api/login", { body: values });
    if (!response.ok || !data) {
      setError(t.login.invalidCredentials);
      return;
    }
    const out = data as LoginOut;
    if (out.status === "mfa_required") {
      setStep("mfa");
      return;
    }
    // "ok" or "mfa_setup_required": both carry the user; the gate handles setup.
    if (out.user) setMe(out.user as Me);
  }

  async function submitMfa(values: { code: string }) {
    setError(null);
    const { data, response } = await api.POST("/api/login/mfa", { body: { code: values.code } });
    if (!response.ok || !data) {
      setError(t.login.mfa.invalidCode);
      return;
    }
    const out = data as LoginOut;
    if (out.status === "ok" && out.user) {
      setMe(out.user as Me);
    } else {
      setError(t.login.mfa.invalidCode);
    }
  }

  return (
    <Center mih="100vh" p="md">
      <Box w="100%" maw={400} style={{ position: "relative" }}>
        {/* soft glow behind the card */}
        <Box
          aria-hidden
          style={{
            position: "absolute", inset: "-40px -10px", borderRadius: 40, zIndex: 0,
            background: "radial-gradient(420px 220px at 50% 0%, rgba(48,208,178,0.18), transparent 70%)",
            filter: "blur(6px)",
          }}
        />
        <Stack align="center" gap={6} mb="xl" style={{ position: "relative", zIndex: 1 }}>
          <BrandMark />
          <Text fw={700} size="xl" style={{ letterSpacing: "-0.02em" }}>
            OPN<span style={{ color: "var(--noc-accent)" }}>GMS</span>
          </Text>
          <Text size="sm" c="dimmed" ta="center" className="noc-mono">
            OPNsense fleet · control plane
          </Text>
        </Stack>

        <Paper withBorder shadow="xl" p="xl" radius="lg" style={{ position: "relative", zIndex: 1 }}>
          {step === "password" ? (
            <>
              <Text className="noc-eyebrow" mb="lg">{t.login.title}</Text>
              <form onSubmit={form.onSubmit(submit)}>
                <Stack gap="md">
                  <TextInput label={t.login.email} required size="md" {...form.getInputProps("email")} />
                  <PasswordInput
                    label={t.login.password}
                    required
                    size="md"
                    visibilityToggleButtonProps={{ "aria-label": t.login.passwordToggle }}
                    {...form.getInputProps("password")}
                  />
                  {error && (
                    <Text role="alert" c="red.5" size="sm">{error}</Text>
                  )}
                  <Button type="submit" fullWidth size="md" mt="xs">{t.login.submit}</Button>
                </Stack>
              </form>
            </>
          ) : (
            <>
              <Text className="noc-eyebrow" mb="xs">{t.login.mfa.title}</Text>
              <Text size="sm" c="dimmed" mb="lg">
                {useRecovery ? t.login.mfa.recoveryHint : t.login.mfa.codeHint}
              </Text>
              <form onSubmit={mfaForm.onSubmit(submitMfa)}>
                <Stack gap="md">
                  <TextInput
                    label={useRecovery ? t.login.mfa.recoveryLabel : t.login.mfa.codeLabel}
                    required
                    size="md"
                    autoFocus
                    data-testid="mfa-code"
                    inputMode={useRecovery ? "text" : "numeric"}
                    {...mfaForm.getInputProps("code")}
                  />
                  {error && (
                    <Text role="alert" c="red.5" size="sm">{error}</Text>
                  )}
                  <Button type="submit" fullWidth size="md" mt="xs" data-testid="mfa-verify">
                    {t.login.mfa.verify}
                  </Button>
                  <Anchor
                    component="button"
                    type="button"
                    size="sm"
                    ta="center"
                    data-testid="mfa-use-recovery"
                    onClick={() => {
                      setUseRecovery((v) => !v);
                      setError(null);
                      mfaForm.setFieldValue("code", "");
                    }}
                  >
                    {useRecovery ? t.login.mfa.useCode : t.login.mfa.useRecovery}
                  </Anchor>
                </Stack>
              </form>
            </>
          )}
        </Paper>

        <Stack align="center" gap="sm" mt="lg" style={{ position: "relative", zIndex: 1 }}>
          <LanguageSwitcher w={160} size="xs" />
          <Text size="xs" c="dimmed">Secured session · encrypted at rest</Text>
        </Stack>
      </Box>
    </Center>
  );
}
