import { Button, CopyButton, Group, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { useT } from "../i18n";
import { QrCode } from "./QrCode";
import { RecoveryCodes } from "./RecoveryCodes";
import { useMfaConfirm, useMfaSetup, type SetupOut } from "./mfaHooks";

interface MfaEnrollFlowProps {
  /** Called after recovery codes are dismissed (e.g. to refresh status / clear a gate). */
  onComplete?: () => void;
  /** Optional cancel handler shown in the password step. */
  onCancel?: () => void;
}

/**
 * The self-contained enrollment wizard: password → setup (QR + secret) → confirm code →
 * recovery codes. Reused by both the Account panel and the forced setup gate.
 */
export function MfaEnrollFlow({ onComplete, onCancel }: MfaEnrollFlowProps) {
  const t = useT();
  const setup = useMfaSetup();
  const confirm = useMfaConfirm();

  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState<SetupOut | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function startSetup() {
    setError(null);
    try {
      const out = await setup.mutateAsync(password);
      setPending(out);
    } catch {
      setError(t.mfa.setupError);
    }
  }

  async function doConfirm() {
    setError(null);
    try {
      const out = await confirm.mutateAsync(code);
      setCodes(out.recovery_codes);
    } catch {
      setError(t.mfa.confirmError);
    }
  }

  if (codes) {
    return <RecoveryCodes codes={codes} onDone={onComplete} />;
  }

  if (pending) {
    return (
      <Stack gap="md">
        <Text size="sm" c="dimmed">{t.mfa.scanHint}</Text>
        <QrCode value={pending.otpauth_uri} />
        <div>
          <Text size="sm" fw={600}>{t.mfa.secretLabel}</Text>
          <Group gap="xs" align="center">
            <Text className="noc-mono" data-testid="mfa-secret" style={{ userSelect: "all" }}>
              {pending.secret}
            </Text>
            <CopyButton value={pending.secret}>
              {({ copied, copy }) => (
                <Button size="compact-xs" variant="subtle" onClick={copy}>
                  {copied ? t.mfa.copied : t.mfa.copyAll}
                </Button>
              )}
            </CopyButton>
          </Group>
        </div>
        <TextInput
          label={t.mfa.confirmCodeLabel}
          description={t.mfa.confirmHint}
          inputMode="numeric"
          data-testid="mfa-confirm-code"
          value={code}
          onChange={(e) => setCode(e.currentTarget.value)}
        />
        {error && <Text role="alert" c="red.5" size="sm">{error}</Text>}
        <Group>
          <Button onClick={doConfirm} loading={confirm.isPending} data-testid="mfa-confirm">
            {t.mfa.confirm}
          </Button>
        </Group>
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">{t.mfa.passwordHint}</Text>
      <PasswordInput
        label={t.mfa.password}
        data-testid="mfa-enroll-password"
        value={password}
        onChange={(e) => setPassword(e.currentTarget.value)}
      />
      {error && <Text role="alert" c="red.5" size="sm">{error}</Text>}
      <Group>
        <Button onClick={startSetup} loading={setup.isPending} data-testid="mfa-enroll">
          {t.mfa.enrollStart}
        </Button>
        {onCancel && (
          <Button variant="default" onClick={onCancel}>{t.mfa.cancel}</Button>
        )}
      </Group>
    </Stack>
  );
}
