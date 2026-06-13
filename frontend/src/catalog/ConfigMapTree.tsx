// frontend/src/catalog/ConfigMapTree.tsx
// Renders a live config.xml MapNode tree (read-only), annotated against the catalog.
// `editable` nodes offer an "Edit in catalog" affordance (→ onEdit(catalog_model_id));
// non-editable nodes carry a muted "read-only (no API)" marker. Mirrors ConfigTree's
// collapse/expand shape but speaks MapNode + the catalog cross-reference.
import { useState } from "react";
import { Anchor, Box, Collapse, Group, Text, UnstyledButton } from "@mantine/core";
import { useT } from "../i18n";
import type { MapNode } from "./catalogTypes";

function NodeView({
  node,
  depth,
  onEdit,
}: {
  node: MapNode;
  depth: number;
  onEdit: (modelId: string) => void;
}) {
  const t = useT();
  const hasChildren = node.children.length > 0;
  const [open, setOpen] = useState(depth < 2); // expand the top couple of levels

  // The catalog affordance: editable → "Edit in catalog"; otherwise the read-only marker.
  const affordance =
    node.editable && node.catalog_model_id ? (
      <Anchor
        component="button"
        type="button"
        size="xs"
        onClick={() => onEdit(node.catalog_model_id!)}
        data-testid={`config-map-edit-${node.catalog_model_id}`}
      >
        {t.catalog.map.editInCatalog}
      </Anchor>
    ) : (
      <Text size="xs" c="dimmed" data-testid={`config-map-readonly-${node.path}`}>
        {t.catalog.map.readOnly}
      </Text>
    );

  if (!hasChildren) {
    return (
      <Group gap="xs" pl={depth * 16} wrap="nowrap" align="baseline">
        <Text size="sm" fw={500}>
          {node.tag}:
        </Text>
        {node.sensitive ? (
          <Text size="sm" c="dimmed">
            •••• 🔒
          </Text>
        ) : (
          <Text size="sm">{node.value || "—"}</Text>
        )}
        {affordance}
      </Group>
    );
  }

  return (
    <Box pl={depth * 16}>
      <Group gap={6} wrap="nowrap" align="baseline">
        <UnstyledButton onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <Group gap={6}>
            <Text size="sm" w={12}>
              {open ? "▾" : "▸"}
            </Text>
            <Text size="sm" fw={600}>
              {node.tag}
            </Text>
            <Text size="xs" c="dimmed">
              ({node.children.length})
            </Text>
          </Group>
        </UnstyledButton>
        {affordance}
      </Group>
      <Collapse expanded={open}>
        {node.children.map((c) => (
          <NodeView key={c.path} node={c} depth={depth + 1} onEdit={onEdit} />
        ))}
      </Collapse>
    </Box>
  );
}

export function ConfigMapTree({
  root,
  onEdit,
}: {
  root: MapNode;
  onEdit: (modelId: string) => void;
}) {
  return <NodeView node={root} depth={0} onEdit={onEdit} />;
}
