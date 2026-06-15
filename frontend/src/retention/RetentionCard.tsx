import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  List,
  Loader,
  NumberInput,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useRetention, useUpdateRetention } from "./retentionHooks";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

// The retention stores surfaced per-tenant; mirrors the backend RETENTION_STORES tuple.
const STORES = ["perimeter", "events", "metrics", "log_lake"] as const;
type Store = (typeof STORES)[number];

// An empty input clears the override (inherit the global default).
type Draft = Record<Store, number | "">;

const emptyDraft = (): Draft => ({ perimeter: "", events: "", metrics: "", log_lake: "" });

export function RetentionCard() {
  const t = useT();
  const rt = t.retention;
  const { activeId } = useTenant();
  const query = useRetention();
  const update = useUpdateRetention();

  // The form draft, (re-)seeded from the loaded overrides whenever the active tenant changes — the card
  // stays mounted across tenant switches, so the seed must follow the tenant, not just run once (absent
  // store → empty → inherit). Keying the guard on the tenant id avoids showing tenant A's draft for B.
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    if (query.data && seededFor.current !== activeId) {
      seededFor.current = activeId;
      const next = emptyDraft();
      for (const store of STORES) {
        const value = query.data.overrides[store];
        if (typeof value === "number") next[store] = value;
      }
      setDraft(next);
    }
  }, [query.data, activeId]);

  if (query.isLoading) return <Loader data-testid="retention-loading" />;
  if (query.error) return <Text c="red" data-testid="retention-error">{t.errors.retentionLoad}</Text>;

  const defaults = query.data?.defaults ?? {};
  // Enabled schedules whose covered range now exceeds the effective retention (computed on read by the API).
  const warnings = query.data?.warnings ?? [];

  async function handleSave() {
    // Empty input → null (clear the override back to inherit); a number sets an override.
    const values: Record<string, number | null> = {};
    for (const store of STORES) {
      values[store] = draft[store] === "" ? null : draft[store];
    }
    try {
      await update.mutateAsync(values);
      notifications.show({ message: rt.saved });
    } catch {
      notifications.show({ color: "red", message: t.errors.retentionSave });
    }
  }

  return (
    <Card withBorder padding="lg" radius="md" maw={480} data-testid="retention-card">
      <Stack>
        <div>
          <Title order={4}>{rt.title}</Title>
          <Text size="sm" c="dimmed">{rt.subtitle}</Text>
        </div>

        {warnings.length > 0 && (
          <Alert color="orange" title={rt.warningTitle} data-testid="retention-warnings">
            <List size="sm" spacing={4}>
              {warnings.map((w) => (
                <List.Item key={w.schedule_id}>
                  {rt.warningItem
                    .replace("{frequency}", w.frequency)
                    .replace("{range}", String(w.range_days))
                    .replace("{store}", w.limiting_store)
                    .replace("{bound}", String(w.bound))}
                </List.Item>
              ))}
            </List>
          </Alert>
        )}

        {STORES.map((store) => {
          const inherited = defaults[store];
          const hint =
            typeof inherited === "number"
              ? `${rt.inheritHint}: ${inherited}`
              : undefined;
          return (
            <NumberInput
              key={store}
              label={rt.stores[store]}
              description={hint}
              placeholder={hint}
              value={draft[store]}
              min={1}
              max={3650}
              step={1}
              allowDecimal={false}
              onChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  [store]: typeof val === "number" ? val : "",
                }))
              }
              data-testid={`retention-${store}`}
              maw={260}
            />
          );
        })}

        <Button
          onClick={handleSave}
          loading={update.isPending}
          data-testid="retention-save"
        >
          {rt.save}
        </Button>
      </Stack>
    </Card>
  );
}
