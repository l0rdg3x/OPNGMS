import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Stack,
  Switch,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useRuntimeSettings, useUpdateRuntimeSettings } from "./systemHooks";
import type { RuntimeSettingOut } from "./systemHooks";
import { useT } from "../i18n";

const GROUP_ORDER = ["firmware", "distribution", "maintenance", "security_login", "security_session"];

type Draft = Record<string, boolean | number>;

export function RuntimeSettingsSection() {
  const t = useT();
  const rt = t.system.runtime;
  const groupLabels = rt.groups as Record<string, string>;
  const itemMeta = rt.items as Record<string, { label: string; help: string }>;

  const query = useRuntimeSettings();
  const update = useUpdateRuntimeSettings();
  const [draft, setDraft] = useState<Draft>({});

  const settings = useMemo(() => query.data?.settings ?? [], [query.data]);
  const effective = useMemo<Draft>(
    () => Object.fromEntries(settings.map((s) => [s.key, s.value])),
    [settings],
  );

  // The committed value, unless the operator has edited it in this form.
  const valueOf = (s: RuntimeSettingOut): boolean | number =>
    s.key in draft ? draft[s.key] : s.value;

  const dirtyKeys = settings.filter((s) => s.key in draft && draft[s.key] !== effective[s.key]);

  function setValue(key: string, value: boolean | number) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function handleSave() {
    const values: Draft = Object.fromEntries(dirtyKeys.map((s) => [s.key, draft[s.key]]));
    try {
      await update.mutateAsync(values);
      setDraft({});
      notifications.show({ message: rt.saved });
    } catch {
      notifications.show({ color: "red", message: rt.saveError });
    }
  }

  const groups = GROUP_ORDER.filter((g) => settings.some((s) => s.group === g));
  const labelOf = (key: string) => itemMeta[key]?.label ?? key.replace(/_/g, " ");

  return (
    <Card withBorder padding="lg" radius="md" data-testid="runtime-settings">
      <Stack>
        <div>
          <Title order={4}>{rt.title}</Title>
          <Text size="sm" c="dimmed">{rt.subtitle}</Text>
        </div>

        {query.isLoading ? (
          <Loader size="sm" data-testid="runtime-settings-loading" />
        ) : query.isError ? (
          <Alert color="red" data-testid="runtime-settings-error">{rt.loadError}</Alert>
        ) : (
          <>
            {groups.map((group) => (
              <Stack key={group} gap="xs">
                <Text fw={600} size="sm">{groupLabels[group] ?? group}</Text>
                {settings
                  .filter((s) => s.group === group)
                  .map((s) => {
                    const meta = itemMeta[s.key];
                    const v = valueOf(s);
                    if (s.kind === "bool") {
                      return (
                        <Switch
                          key={s.key}
                          label={labelOf(s.key)}
                          description={meta?.help}
                          checked={v === true}
                          onChange={(e) => setValue(s.key, e.currentTarget.checked)}
                          data-testid={`rs-${s.key}`}
                        />
                      );
                    }
                    return (
                      <NumberInput
                        key={s.key}
                        label={
                          <Group gap={6} wrap="nowrap">
                            <span>{labelOf(s.key)}</span>
                            <Tooltip label={`${rt.defaultLabel}: ${s.default}`} withArrow>
                              <Text span size="xs" c="dimmed">({rt.defaultLabel} {s.default})</Text>
                            </Tooltip>
                          </Group>
                        }
                        description={meta?.help}
                        value={typeof v === "number" ? v : Number(v)}
                        min={s.minimum ?? undefined}
                        max={s.maximum ?? undefined}
                        step={s.kind === "float" ? 0.1 : 1}
                        allowDecimal={s.kind === "float"}
                        onChange={(val) =>
                          setValue(s.key, typeof val === "number" ? val : Number(val))
                        }
                        data-testid={`rs-${s.key}`}
                        maw={260}
                      />
                    );
                  })}
              </Stack>
            ))}

            <Group>
              <Button
                onClick={handleSave}
                loading={update.isPending}
                disabled={dirtyKeys.length === 0}
                data-testid="runtime-settings-save"
              >
                {rt.save}
              </Button>
              {dirtyKeys.length > 0 && (
                <Button
                  variant="subtle"
                  onClick={() => setDraft({})}
                  disabled={update.isPending}
                  data-testid="runtime-settings-discard"
                >
                  {rt.discard}
                </Button>
              )}
            </Group>
          </>
        )}
      </Stack>
    </Card>
  );
}
