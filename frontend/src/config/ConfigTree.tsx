import { useState } from "react";
import { Box, Collapse, Group, Text, UnstyledButton } from "@mantine/core";
import { useT } from "../i18n";
import type { ConfigNode } from "./types";

function NodeView({ node, depth }: { node: ConfigNode; depth: number }) {
  const t = useT();
  const hasChildren = node.children.length > 0;
  const [open, setOpen] = useState(depth < 2); // expand the top couple of levels

  if (!hasChildren) {
    return (
      <Group gap="xs" pl={depth * 16} wrap="nowrap" align="baseline">
        <Text size="sm" fw={500}>
          {node.tag}:
        </Text>
        {node.sensitive ? (
          <Text size="sm" c="dimmed">
            •••• 🔒 ({t.config.hidden})
          </Text>
        ) : (
          <Text size="sm">{node.value || "—"}</Text>
        )}
      </Group>
    );
  }

  return (
    <Box pl={depth * 16}>
      <UnstyledButton onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Group gap={6}>
          <Text size="sm" w={12}>
            {open ? "▾" : "▸"}
          </Text>
          <Text size="sm" fw={600}>
            {node.tag}
          </Text>
          {node.sensitive && <Text size="sm">🔒</Text>}
          <Text size="xs" c="dimmed">
            ({node.children.length})
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse expanded={open}>
        {node.children.map((c) => (
          <NodeView key={c.path} node={c} depth={depth + 1} />
        ))}
      </Collapse>
    </Box>
  );
}

export function ConfigTree({ root }: { root: ConfigNode }) {
  return <NodeView node={root} depth={0} />;
}
