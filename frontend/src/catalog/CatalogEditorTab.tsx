// frontend/src/catalog/CatalogEditorTab.tsx
import { useMemo, useState } from "react";
import { Badge, Card, Grid, Loader, NavLink, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { CatalogModelForm } from "./CatalogModelForm";
import { useCatalogModel, useDeviceCatalog, useProposeCatalogChange } from "./catalogHooks";
import type { CatalogChangeBody } from "./catalogTypes";

export function CatalogEditorTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const catalog = useDeviceCatalog(deviceId);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const model = useCatalogModel(deviceId, selected);
  const propose = useProposeCatalogChange(deviceId);

  const models = useMemo(() => {
    const all = Object.values(catalog.data?.models ?? {});
    const q = search.trim().toLowerCase();
    return all
      .filter((m) => !q || m.id.toLowerCase().includes(q) || (m.title ?? "").toLowerCase().includes(q))
      .sort((a, b) => (a.title ?? a.id).localeCompare(b.title ?? b.id));
  }, [catalog.data, search]);

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
  if (!catalog.data || Object.keys(catalog.data.models).length === 0) {
    return <Text c="dimmed">{t.catalog.noModels}</Text>;
  }

  return (
    <Grid>
      <Grid.Col span={{ base: 12, sm: 4 }}>
        <Stack gap="xs">
          <TextInput placeholder={t.catalog.searchModels} value={search}
            onChange={(e) => setSearch(e.currentTarget.value)} data-testid="catalog-search" />
          <ScrollArea h={500}>
            {models.map((m) => (
              <NavLink key={m.id} label={m.title || m.id} active={selected === m.id}
                onClick={() => setSelected(m.id)}
                rightSection={m.read_only ? <Badge size="xs" color="gray">RO</Badge> : null} />
            ))}
          </ScrollArea>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, sm: 8 }}>
        <Card withBorder>
          {!selected && <Text c="dimmed">{t.catalog.selectModel}</Text>}
          {selected && model.isLoading && <Loader />}
          {selected && model.data && <CatalogModelForm live={model.data} onPropose={onPropose} />}
        </Card>
      </Grid.Col>
    </Grid>
  );
}
