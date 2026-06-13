// frontend/src/catalog/CatalogEditorTab.tsx
import { useState } from "react";
import { Alert, Card, Grid, Loader, ScrollArea, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { CatalogMenuTree } from "./CatalogMenuTree";
import { CatalogModelForm } from "./CatalogModelForm";
import { ConfigMapTree } from "./ConfigMapTree";
import { useCatalogDiff, useCatalogModel, useConfigMap, useDeviceCatalog, useProposeCatalogChange } from "./catalogHooks";
import type { CatalogChangeBody } from "./catalogTypes";

export function CatalogEditorTab({ deviceId, baseUrl }: { deviceId: string; baseUrl: string }) {
  const t = useT();
  const catalog = useDeviceCatalog(deviceId);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // Left-pane mode: the catalog "menu" tree (default) or the live config.xml "map".
  const [pane, setPane] = useState<"menu" | "map">("menu");
  // `baseline` null = "no comparison" → no badges. Defaults to the diff's own `from` once loaded.
  const [baseline, setBaseline] = useState<string | null>(null);
  const model = useCatalogModel(deviceId, selected);
  const propose = useProposeCatalogChange(deviceId);
  const diffQuery = useCatalogDiff(deviceId, baseline);
  const diff = diffQuery.data;
  // The config.xml map is only fetched while its pane is active (avoids a live connector hit otherwise).
  const configMap = useConfigMap(pane === "map" ? deviceId : "");
  // The active baseline the badges are computed against (selection overrides the server default).
  const activeFrom = baseline ?? diff?.from ?? null;

  // "Edit in catalog" on a map node: jump back to the menu pane and open that catalog model.
  function onEditInCatalog(modelId: string) {
    setSelected(modelId);
    setPane("menu");
  }

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
          <SegmentedControl
            fullWidth value={pane} onChange={(v) => setPane(v as "menu" | "map")}
            data={[
              { value: "menu", label: t.catalog.map.tabMenu },
              { value: "map", label: t.catalog.map.tabMap },
            ]}
            data-testid="catalog-pane-toggle" />
          {pane === "menu" && (
            <>
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
            </>
          )}
          {pane === "map" && (
            <>
              {configMap.isLoading && <Loader />}
              {configMap.data?.source === "snapshot" && (
                <Alert color="yellow" data-testid="catalog-map-stale">
                  {t.catalog.map.staleBanner.replace("{when}", configMap.data.taken_at ?? "")}
                </Alert>
              )}
              {configMap.data && (
                <ScrollArea h={500}>
                  <ConfigMapTree root={configMap.data.tree} onEdit={onEditInCatalog} />
                </ScrollArea>
              )}
            </>
          )}
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
