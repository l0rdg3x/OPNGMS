import {
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  PasswordInput,
  SegmentedControl,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { ConfirmModal } from "../components/ConfirmModal";
import { useAuth } from "../auth/useAuth";
import { useT } from "../i18n";
import { MfaEnrollFlow } from "./MfaEnrollFlow";
import { RecoveryCodes } from "./RecoveryCodes";
import {
  LastFactorError,
  PasskeyConfigError,
  useAddPasskey,
  useMfaDisable,
  useMfaPolicy,
  useMfaRegenerate,
  useMfaStatus,
  useRemovePasskey,
  useResetUserMfa,
  useSetMfaPolicy,
  useUsers,
  useWebAuthnCredentials,
  type UserOut,
  type WebAuthnCredential,
} from "./mfaHooks";
import { webauthnSupported } from "./webauthnClient";

// ── Manage block shown when MFA is enabled (regenerate + disable) ─────────────
function MfaManage() {
  const t = useT();
  const regenerate = useMfaRegenerate();
  const disable = useMfaDisable();
  const [regenPassword, setRegenPassword] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const [regenError, setRegenError] = useState<string | null>(null);
  const [disableError, setDisableError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function doRegen() {
    setRegenError(null);
    try {
      const out = await regenerate.mutateAsync(regenPassword);
      setNewCodes(out.recovery_codes);
      setRegenPassword("");
    } catch {
      setRegenError(t.mfa.regenerateError);
    }
  }

  async function doDisable() {
    setDisableError(null);
    try {
      await disable.mutateAsync(disablePassword);
      setConfirmOpen(false);
      setDisablePassword("");
      notifications.show({ message: t.mfa.disabledNotice });
    } catch {
      setConfirmOpen(false);
      setDisableError(t.mfa.disableError);
    }
  }

  return (
    <Stack gap="lg">
      {newCodes ? (
        <RecoveryCodes codes={newCodes} onDone={() => setNewCodes(null)} />
      ) : (
        <Stack gap="sm">
          <Text fw={600}>{t.mfa.regenerate}</Text>
          <Text size="sm" c="dimmed">{t.mfa.passwordHint}</Text>
          <PasswordInput
            label={t.mfa.password}
            data-testid="mfa-regen-password"
            value={regenPassword}
            onChange={(e) => setRegenPassword(e.currentTarget.value)}
          />
          {regenError && <Text role="alert" c="red.5" size="sm">{regenError}</Text>}
          <Group>
            <Button
              variant="default"
              onClick={doRegen}
              loading={regenerate.isPending}
              data-testid="mfa-regen"
            >
              {t.mfa.regenerate}
            </Button>
          </Group>
        </Stack>
      )}

      <Divider />

      <Stack gap="sm">
        <Text fw={600} c="red.4">{t.mfa.disable}</Text>
        <PasswordInput
          label={t.mfa.password}
          data-testid="mfa-disable-password"
          value={disablePassword}
          onChange={(e) => setDisablePassword(e.currentTarget.value)}
        />
        {disableError && <Text role="alert" c="red.5" size="sm">{disableError}</Text>}
        <Group>
          <Button color="red" variant="light" onClick={() => setConfirmOpen(true)} data-testid="mfa-disable">
            {t.mfa.disable}
          </Button>
        </Group>
      </Stack>

      <ConfirmModal
        opened={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={doDisable}
        title={t.mfa.disableConfirmTitle}
        body={t.mfa.disableConfirmBody}
        confirmLabel={t.mfa.disable}
        loading={disable.isPending}
      />
    </Stack>
  );
}

// ── Passkeys (WebAuthn) — shown only when the org has WebAuthn configured ──────
function PasskeysSection() {
  const t = useT();
  const supported = webauthnSupported();
  const credsQuery = useWebAuthnCredentials(supported);
  const add = useAddPasskey();
  const remove = useRemovePasskey();
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [target, setTarget] = useState<WebAuthnCredential | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  async function doAdd() {
    setAddError(null);
    try {
      await add.mutateAsync({ password, name });
      setPassword("");
      setName("");
      notifications.show({ message: t.mfa.passkeys.addedNotice });
    } catch (err) {
      if (err instanceof PasskeyConfigError) setAddError(t.mfa.passkeys.notConfigured);
      else setAddError(t.mfa.passkeys.addError);
    }
  }

  async function doRemove() {
    if (!target) return;
    setRemoveError(null);
    try {
      await remove.mutateAsync(target.id);
      notifications.show({ message: t.mfa.passkeys.removedNotice });
    } catch (err) {
      setRemoveError(
        err instanceof LastFactorError ? t.mfa.passkeys.lastFactorError : t.mfa.passkeys.removeError,
      );
    } finally {
      setTarget(null);
    }
  }

  const creds = credsQuery.data ?? [];

  return (
    <Stack gap="md" data-testid="mfa-passkeys">
      <Text fw={600}>{t.mfa.passkeys.title}</Text>
      <Text size="sm" c="dimmed">{t.mfa.passkeys.intro}</Text>

      {!supported && (
        <Text role="alert" c="red.5" size="sm" data-testid="mfa-passkey-unsupported">
          {t.mfa.passkeys.notSupported}
        </Text>
      )}

      {removeError && <Text role="alert" c="red.5" size="sm">{removeError}</Text>}

      {creds.length > 0 && (
        <Table data-testid="mfa-passkeys-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.mfa.passkeys.colName}</Table.Th>
              <Table.Th>{t.mfa.passkeys.colCreated}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {creds.map((c) => (
              <Table.Tr key={c.id} data-testid={`mfa-passkey-row-${c.id}`}>
                <Table.Td>{c.name || t.mfa.passkeys.unnamed}</Table.Td>
                <Table.Td>{new Date(c.created_at).toLocaleDateString()}</Table.Td>
                <Table.Td>
                  <Button
                    size="xs"
                    variant="light"
                    color="red"
                    onClick={() => {
                      setRemoveError(null);
                      setTarget(c);
                    }}
                    data-testid={`mfa-passkey-remove-${c.id}`}
                  >
                    {t.mfa.passkeys.remove}
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Divider />

      <Stack gap="sm">
        <Text fw={600}>{t.mfa.passkeys.add}</Text>
        <Text size="sm" c="dimmed">{t.mfa.passwordHint}</Text>
        <PasswordInput
          label={t.mfa.passkeys.accountPassword}
          data-testid="mfa-passkey-password"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <TextInput
          label={t.mfa.passkeys.name}
          placeholder={t.mfa.passkeys.namePlaceholder}
          data-testid="mfa-passkey-name"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
        />
        {addError && <Text role="alert" c="red.5" size="sm">{addError}</Text>}
        <Group>
          <Button
            variant="default"
            onClick={doAdd}
            loading={add.isPending}
            disabled={!supported}
            data-testid="mfa-passkey-add"
          >
            {t.mfa.passkeys.add}
          </Button>
        </Group>
      </Stack>

      <ConfirmModal
        opened={target !== null}
        onClose={() => setTarget(null)}
        onConfirm={doRemove}
        title={t.mfa.passkeys.removeConfirmTitle}
        body={t.mfa.passkeys.removeConfirmBody}
        confirmLabel={t.mfa.passkeys.remove}
        loading={remove.isPending}
      />
    </Stack>
  );
}

// ── Superadmin: org policy + per-user reset ──────────────────────────────────
function MfaPolicyControl() {
  const t = useT();
  const policyQuery = useMfaPolicy();
  const setPolicy = useSetMfaPolicy();

  async function change(mode: string) {
    try {
      await setPolicy.mutateAsync(mode);
      notifications.show({ message: t.mfa.policySaved });
    } catch {
      notifications.show({ color: "red", message: t.mfa.policySaveError });
    }
  }

  if (policyQuery.isLoading) return <Loader size="sm" />;
  if (policyQuery.error)
    return <Text c="red" data-testid="mfa-policy-error">{t.mfa.policyLoadError}</Text>;

  const mode = policyQuery.data?.mode ?? "off";

  return (
    <Stack gap="sm">
      <Text fw={600}>{t.mfa.policyTitle}</Text>
      <Text size="sm" c="dimmed">{t.mfa.policyIntro}</Text>
      <SegmentedControl
        data-testid="mfa-policy"
        value={mode}
        onChange={change}
        data={[
          { value: "off", label: t.mfa.policyOff },
          { value: "all", label: t.mfa.policyAll },
          { value: "privileged", label: t.mfa.policyPrivileged },
        ]}
      />
    </Stack>
  );
}

function MfaUsersTable() {
  const t = useT();
  const usersQuery = useUsers();
  const reset = useResetUserMfa();
  const [target, setTarget] = useState<UserOut | null>(null);

  async function doReset() {
    if (!target) return;
    try {
      await reset.mutateAsync(target.id);
      notifications.show({ message: t.mfa.resetDone });
    } catch {
      notifications.show({ color: "red", message: t.mfa.resetError });
    } finally {
      setTarget(null);
    }
  }

  if (usersQuery.isLoading) return <Loader size="sm" />;
  if (usersQuery.error)
    return <Text c="red" data-testid="mfa-users-error">{t.mfa.usersLoadError}</Text>;

  const users = usersQuery.data ?? [];

  return (
    <Stack gap="sm">
      <Text fw={600}>{t.mfa.usersTitle}</Text>
      <Table data-testid="mfa-users-table">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t.mfa.colUser}</Table.Th>
            <Table.Th>{t.mfa.colEmail}</Table.Th>
            <Table.Th>{t.mfa.colRole}</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {users.map((u) => (
            <Table.Tr key={u.id} data-testid={`mfa-user-row-${u.id}`}>
              <Table.Td>{u.name}</Table.Td>
              <Table.Td>{u.email}</Table.Td>
              <Table.Td>
                {u.is_superadmin ? (
                  <Badge color="grape">{t.mfa.superadmin}</Badge>
                ) : (
                  <Badge variant="default">{t.mfa.member}</Badge>
                )}
              </Table.Td>
              <Table.Td>
                <Button
                  size="xs"
                  variant="light"
                  color="red"
                  onClick={() => setTarget(u)}
                  data-testid={`mfa-reset-${u.id}`}
                >
                  {t.mfa.reset}
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <ConfirmModal
        opened={target !== null}
        onClose={() => setTarget(null)}
        onConfirm={doReset}
        title={t.mfa.resetConfirmTitle}
        body={t.mfa.resetConfirmBody}
        confirmLabel={t.mfa.reset}
        loading={reset.isPending}
      />
    </Stack>
  );
}

// ── Main panel ───────────────────────────────────────────────────────────────
export function MfaPanel() {
  const t = useT();
  const { me } = useAuth();
  const statusQuery = useMfaStatus();

  return (
    <Stack gap="lg">
      <Title order={3}>{t.mfa.pageTitle}</Title>
      <Text size="sm" c="dimmed">{t.mfa.intro}</Text>

      <Card withBorder padding="lg" radius="md">
        {statusQuery.isLoading ? (
          <Loader />
        ) : statusQuery.error ? (
          <Text c="red" data-testid="mfa-status-error">{t.mfa.statusError}</Text>
        ) : statusQuery.data?.enabled ? (
          <Stack gap="lg">
            <Group gap="sm">
              <Text fw={600}>{t.mfa.status}:</Text>
              <Badge color="teal" data-testid="mfa-status-badge">{t.mfa.enabled}</Badge>
            </Group>
            <Group gap="sm">
              <Text size="sm" c="dimmed">{t.mfa.recoveryRemaining}:</Text>
              <Text fw={600}>{statusQuery.data.recovery_codes_remaining}</Text>
            </Group>
            <Divider />
            <MfaManage />
          </Stack>
        ) : (
          <Stack gap="lg">
            <Group gap="sm">
              <Text fw={600}>{t.mfa.status}:</Text>
              <Badge color="gray" data-testid="mfa-status-badge">{t.mfa.disabled}</Badge>
            </Group>
            <Divider />
            <Title order={5}>{t.mfa.enroll}</Title>
            <MfaEnrollFlow onComplete={() => statusQuery.refetch()} />
          </Stack>
        )}
      </Card>

      {statusQuery.data?.webauthn?.configured && (
        <Card withBorder padding="lg" radius="md">
          <PasskeysSection />
        </Card>
      )}

      {me?.is_superadmin && (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="lg">
            <MfaPolicyControl />
            <Divider />
            <MfaUsersTable />
          </Stack>
        </Card>
      )}
    </Stack>
  );
}
