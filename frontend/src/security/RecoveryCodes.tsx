import { Alert, Button, Code, Group, SimpleGrid, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";

interface RecoveryCodesProps {
  codes: string[];
  onDone?: () => void;
}

/** Shows the one-time recovery codes once, with copy-all + download. */
export function RecoveryCodes({ codes, onDone }: RecoveryCodesProps) {
  const t = useT();

  async function copyAll() {
    const text = codes.join("\n");
    try {
      await navigator.clipboard?.writeText(text);
      notifications.show({ message: t.mfa.copied });
    } catch {
      /* clipboard may be unavailable (e.g. insecure context); ignore */
    }
  }

  function download() {
    const blob = new Blob([codes.join("\n") + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "opngms-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Stack gap="sm" data-testid="mfa-recovery-codes">
      <Text fw={600}>{t.mfa.recoveryTitle}</Text>
      <Text size="sm" c="dimmed">{t.mfa.recoveryIntro}</Text>
      <Alert color="yellow" variant="light">{t.mfa.recoveryWarning}</Alert>
      <SimpleGrid cols={2} spacing="xs">
        {codes.map((c) => (
          <Code key={c} className="noc-mono">{c}</Code>
        ))}
      </SimpleGrid>
      <Group>
        <Button variant="default" size="xs" onClick={copyAll} data-testid="mfa-copy-codes">
          {t.mfa.copyAll}
        </Button>
        <Button variant="default" size="xs" onClick={download} data-testid="mfa-download-codes">
          {t.mfa.download}
        </Button>
        {onDone && (
          <Button size="xs" onClick={onDone} data-testid="mfa-codes-done">
            {t.mfa.done}
          </Button>
        )}
      </Group>
    </Stack>
  );
}
