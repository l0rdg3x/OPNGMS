// frontend/src/catalog/CatalogEditorTab.tsx
import { useState } from "react";
import { Card, Grid, Loader, ScrollArea, Select, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { CatalogMenuTree } from "./CatalogMenuTree";
import { CatalogModelForm } from "./CatalogModelForm";
import { useCatalogDiff, useCatalogModel, useDeviceCatalog, useProposeCatalogChange } from "./catalogHooks";
import type { CatalogChangeBody } from "./catalogTypes";

export function CatalogEditorTab({ deviceId, baseUrl }: { deviceId: string; baseUrl: string }) {
  const t = useT();
  const catalog = useDeviceCatalog(deviceId);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // `baseline` null = "no comparison" → no badges. Defaults to the diff's own `from` once loaded.
  const [baseline, setBaseline] = useState<string | null>(null);
  const model = useCatalogModel(deviceId, selected);
  const propose = useProposeCatalogChange(deviceId);
  const diffQuery = useCatalogDiff(deviceId, baseline);
  const diff = diffQuery.data;
  // The active baseline the badges are computed against (selection overrides the server default).
  const activeFrom = baseline ?? diff?.from ?? null;

  async function onPropose(body: CatalogChangeBody) {
    if (Object.keys(body.scalars).length === 0 && body.grids.length === 0) {
      notifications.show({ message: t.catalog.noChanges });
      return;
    }
    try {
      await propose.mutateAsync(body);
      notifications.show({ message: t.catalog.proposed });
    } catch {
      notifications.show({ color: "red", message: t.catalog.proposeFailed });
    }
  }

  if (catalog.isLoading) return <Loader />;
  if (!catalog.data || (catalog.data.menu ?? []).length === 0) {
    return <Text c="dimmed">{t.catalog.noModels}</Text>;
  }

  return (
    <Grid>
      <Grid.Col span={{ base: 12, sm: 4 }}>
        <Stack gap="xs">
          <TextInput placeholder={t.catalog.searchAll} value={search}
            onChange={(e) => setSearch(e.currentTarget.value)} data-testid="catalog-search" />
          {(diff?.available_baselines.length ?? 0) > 0 && (
            <Select
              label={t.catalog.diff.baseline} placeholder={t.catalog.diff.noBaseline} clearable
              data={diff!.available_baselines} value={activeFrom}
              onChange={setBaseline} data-testid="catalog-diff-baseline" />
          )}
          <ScrollArea h={500}>
            <CatalogMenuTree nodes={catalog.data.menu ?? []} baseUrl={baseUrl} search={search}
              selected={selected} onSelect={setSelected}
              diff={activeFrom ? diff?.diff : undefined} />
          </ScrollArea>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, sm: 8 }}>
        <Card withBorder>
          {!selected && <Text c="dimmed">{t.catalog.selectModel}</Text>}
          {selected && model.isLoading && <Loader />}
          {selected && model.data && (
            <CatalogModelForm key={model.data.model.id} live={model.data} onPropose={onPropose}
              diff={diff} diffFrom={activeFrom} />
          )}
        </Card>
      </Grid.Col>
    </Grid>
  );
}
