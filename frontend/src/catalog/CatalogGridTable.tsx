// frontend/src/catalog/CatalogGridTable.tsx
import { useState } from "react";
import { Button, Group, Modal, Stack, Table, Text } from "@mantine/core";
import { useT } from "../i18n";
import { CatalogFieldInput } from "./CatalogFieldInput";
import type { CatalogGrid, CatalogGridOp, GridRow } from "./catalogTypes";

/** Editable table for one ArrayField grid. Tracks add/edit/delete against the live `rows`
 *  and reports the accumulated grid ops via onOps. Values are strings (see CatalogFieldInput). */
export function CatalogGridTable({
  grid, rows, disabled, onOps, fieldOptions = {},
}: {
  grid: CatalogGrid;
  rows: GridRow[];
  disabled: boolean;
  onOps: (ops: CatalogGridOp[]) => void;
  fieldOptions?: Record<string, { value: string; label: string }[]>;
}) {
  const t = useT();
  const [ops, setOps] = useState<CatalogGridOp[]>([]);
  const [editing, setEditing] = useState<null | { uuid?: string; item: Record<string, string> }>(null);

  const push = (next: CatalogGridOp[]) => { const all = [...ops, ...next]; setOps(all); onOps(all); };
  const asString = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join(",") : (v ?? ""));

  function openAdd() {
    setEditing({ item: Object.fromEntries(grid.fields.map((f) => [f.path, ""])) });
  }
  function openEdit(row: GridRow) {
    setEditing({ uuid: row.uuid, item: Object.fromEntries(grid.fields.map((f) => [f.path, asString(row[f.path])])) });
  }
  function save() {
    if (!editing) return;
    push([editing.uuid
      ? { op: "set", grid: grid.path, uuid: editing.uuid, item: editing.item }
      : { op: "add", grid: grid.path, item: editing.item }]);
    setEditing(null);
  }

  return (
    <Stack gap="xs">
      <Table data-testid={`catalog-grid-${grid.path}`}>
        <Table.Thead>
          <Table.Tr>{grid.fields.map((f) => <Table.Th key={f.path}>{f.label || f.path}</Table.Th>)}<Table.Th /></Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.length === 0 && (
            <Table.Tr><Table.Td colSpan={grid.fields.length + 1}><Text c="dimmed">{t.catalog.grid.empty}</Text></Table.Td></Table.Tr>
          )}
          {rows.map((row) => (
            <Table.Tr key={row.uuid}>
              {grid.fields.map((f) => <Table.Td key={f.path}>{asString(row[f.path])}</Table.Td>)}
              <Table.Td>
                <Group gap="xs">
                  <Button size="xs" variant="light" disabled={disabled}
                    data-testid={`catalog-grid-${grid.path}-edit-${row.uuid}`} onClick={() => openEdit(row)}>
                    {t.catalog.grid.edit}
                  </Button>
                  <Button size="xs" color="red" variant="light" disabled={disabled}
                    data-testid={`catalog-grid-${grid.path}-del-${row.uuid}`}
                    onClick={() => push([{ op: "del", grid: grid.path, uuid: row.uuid }])}>
                    {t.catalog.grid.delete}
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group>
        <Button size="xs" disabled={disabled} data-testid={`catalog-grid-${grid.path}-add`} onClick={openAdd}>
          {t.catalog.grid.add}
        </Button>
      </Group>
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={grid.path}>
        <Stack>
          {editing && grid.fields.map((f) => (
            <CatalogFieldInput key={f.path} field={f} value={editing.item[f.path] ?? ""} disabled={false}
              liveOptions={fieldOptions[f.path]}
              onChange={(p, v) => setEditing({ ...editing, item: { ...editing.item, [p]: v } })} />
          ))}
          <Button onClick={save} data-testid={`catalog-grid-${grid.path}-save`}>
            {editing?.uuid ? t.catalog.grid.save : t.catalog.grid.add}
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
