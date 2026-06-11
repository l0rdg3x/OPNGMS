import { Box, Center, Paper, Stack, Text, Title } from "@mantine/core";
import { useAuth } from "../auth/useAuth";
import { useT } from "../i18n";
import { MfaEnrollFlow } from "./MfaEnrollFlow";

/**
 * Full-screen forced enrollment gate. Shown by ProtectedRoute when the session is setup-only
 * (`me.mfa_setup_required`). Reuses the standard enrollment wizard; on completion it refreshes
 * `/api/me` so the upgraded (full) session clears the gate.
 */
export function MfaSetupGate() {
  const t = useT();
  const { refresh } = useAuth();

  return (
    <Center mih="100vh" p="md">
      <Box w="100%" maw={460} style={{ position: "relative" }}>
        <Box
          aria-hidden
          style={{
            position: "absolute", inset: "-40px -10px", borderRadius: 40, zIndex: 0,
            background: "radial-gradient(420px 220px at 50% 0%, rgba(48,208,178,0.18), transparent 70%)",
            filter: "blur(6px)",
          }}
        />
        <Paper
          withBorder
          shadow="xl"
          p="xl"
          radius="lg"
          style={{ position: "relative", zIndex: 1 }}
          data-testid="mfa-setup-gate"
        >
          <Stack gap="md">
            <Title order={4}>{t.mfa.gateTitle}</Title>
            <Text size="sm" c="dimmed">{t.mfa.gateIntro}</Text>
            <MfaEnrollFlow onComplete={refresh} />
          </Stack>
        </Paper>
      </Box>
    </Center>
  );
}
