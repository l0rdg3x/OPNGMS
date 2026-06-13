// frontend/src/catalog/CatalogModelForm.tsx
import { useMemo, useState } from "react";
import { Alert, Badge, Button, Group, Stack, Text, Title } from "@mantine/core";
import { useT } from "../i18n";
import { CatalogFieldInput } from "./CatalogFieldInput";
import { CatalogGridTable } from "./CatalogGridTable";
import type { CatalogChangeBody, CatalogDiff, CatalogField, CatalogGridOp, CatalogModelLive } from "./catalogTypes";

const toStr = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join(",") : (v ?? ""));

export function CatalogModelForm({
  live, onPropose, diff, diffFrom,
}: {
  live: CatalogModelLive;
  onPropose: (body: CatalogChangeBody) => Promise<unknown>;
  diff?: CatalogDiff;
  diffFrom?: string | null;
}) {
  const t = useT();
  const { model, values, grids, field_options, grid_field_options, reachable, read_only } = live;
  const editable = reachable && !read_only;

  // Cross-version diff for this model (only when a baseline is selected).
  const modelDiff = diff && diffFrom ? diff.diff.models[model.id] : undefined;
  function fieldBadge(path: string) {
    if (!modelDiff || !diffFrom) return null;
    if (modelDiff.added_fields.includes(path)) {
      return (
        <Badge color="teal" size="sm" variant="light" data-testid={`catalog-diff-new-${path}`}>
          {t.catalog.diff.newSince.replace("{v}", diffFrom)}
        </Badge>
      );
    }
    if (modelDiff.changed_fields.includes(path)) {
      return (
        <Badge color="yellow" size="sm" variant="light" data-testid={`catalog-diff-changed-${path}`}>
          {t.catalog.diff.changedSince.replace("{v}", diffFrom)}
        </Badge>
      );
    }
    return null;
  }

  // Seed working scalar state from the live values (all as strings).
  const seeded = useMemo(() => {
    const s: Record<string, string> = {};
    for (const f of model.fields) s[f.path] = toStr(values[f.path]);
    return s;
  }, [model, values]);
  const [work, setWork] = useState<Record<string, string>>(seeded);
  const [gridOps, setGridOps] = useState<Record<string, CatalogGridOp[]>>({});

  const fieldByPath = useMemo(() => {
    const m = new Map<string, CatalogField>();
    for (const f of model.fields) m.set(f.path, f);
    for (const g of model.grids) for (const f of g.fields) m.set(`${g.path}.${f.path}`, f);
    return m;
  }, [model]);

  function build(): CatalogChangeBody {
    const scalars: Record<string, string> = {};
    for (const [path, val] of Object.entries(work)) if (val !== seeded[path]) scalars[path] = val;
    const ops = Object.values(gridOps).flat();
    return { model_id: model.id, scalars, grids: ops };
  }

  if (read_only) {
    return <Alert color="yellow">{t.catalog.readOnly}</Alert>;
  }

  return (
    <Stack>
      <Title order={4}>{model.title}</Title>
      {!reachable && <Alert color="red">{t.catalog.unreachable}</Alert>}
      {model.pages.map((page) => (
        <Stack key={page.id} gap="xs">
          <Text fw={600}>{page.id}</Text>
          {page.fields.map((path) => {
            const f = fieldByPath.get(path);
            if (!f) return null;
            const badge = fieldBadge(path);
            return (
              <Group key={path} gap="xs" align="flex-end" wrap="nowrap">
                <CatalogFieldInput field={f} value={work[path] ?? ""} disabled={!editable}
                  liveOptions={field_options[path]}
                  onChange={(p, v) => setWork((w) => ({ ...w, [p]: v }))} />
                {badge}
              </Group>
            );
          })}
        </Stack>
      ))}
      {model.grids.map((g) => (
        <Stack key={g.path} gap="xs">
          <Text fw={600}>{g.path}</Text>
          <CatalogGridTable grid={g} rows={grids[g.path] ?? []} disabled={!editable}
            fieldOptions={grid_field_options[g.path] ?? {}}
            onOps={(ops) => setGridOps((m) => ({ ...m, [g.path]: ops }))} />
        </Stack>
      ))}
      {editable && (
        <Button data-testid="catalog-propose" onClick={() => onPropose(build())}>
          {t.catalog.propose}
        </Button>
      )}
    </Stack>
  );
}
