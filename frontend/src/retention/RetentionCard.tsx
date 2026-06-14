import { useEffect, useRef, useState } from "react";
import {
  Button,
  Card,
  Loader,
  NumberInput,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useRetention, useUpdateRetention } from "./retentionHooks";
import { useT } from "../i18n";

// The retention stores surfaced per-tenant; mirrors the backend RETENTION_STORES tuple.
const STORES = ["perimeter", "events", "metrics"] as const;
type Store = (typeof STORES)[number];

// An empty input clears the override (inherit the global default).
type Draft = Record<Store, number | "">;

const emptyDraft = (): Draft => ({ perimeter: "", events: "", metrics: "" });

export function RetentionCard() {
  const t = useT();
  const rt = t.retention;
  const query = useRetention();
  const update = useUpdateRetention();

  // The form draft, seeded once from the loaded overrides (absent store → empty → inherit).
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const initializedRef = useRef(false);
  useEffect(() => {
    if (query.data && !initializedRef.current) {
      initializedRef.current = true;
      const next = emptyDraft();
      for (const store of STORES) {
        const value = query.data.overrides[store];
        if (typeof value === "number") next[store] = value;
      }
      setDraft(next);
    }
  }, [query.data]);

  if (query.isLoading) return <Loader data-testid="retention-loading" />;
  if (query.error) return <Text c="red" data-testid="retention-error">{t.errors.retentionLoad}</Text>;

  const defaults = query.data?.defaults ?? {};

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
