import { Badge, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useT } from "../i18n";
import type { components } from "../api/schema";

type Inventory = components["schemas"]["CapabilityInventory"];

export function CapabilitiesPanel({ inv }: { inv: Inventory }) {
  const t = useT();
  return (
    <Card withBorder>
      <Title order={5} mb="xs">
        {t.config.capabilities}
      </Title>
      <Stack gap="xs">
        <Text size="sm">
          {t.config.version}: <b>{inv.opnsense_version || "—"}</b>
        </Text>

        <Text size="sm" fw={600}>
          {t.config.interfaces}
        </Text>
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.config.interfaces}</Table.Th>
              <Table.Th>{t.config.nic}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {inv.interfaces.map((i) => (
              <Table.Tr key={i.name}>
                <Table.Td>{i.name}</Table.Td>
                <Table.Td>{i.nic || "—"}</Table.Td>
                <Table.Td>{i.description}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>

        <Text size="sm" fw={600}>
          {t.config.configuredSections}
        </Text>
        <Group gap="xs">
          {inv.configured_sections.map((s) => (
            <Badge key={s} variant="light" color="blue">
              {s}
            </Badge>
          ))}
        </Group>

        <Text size="sm" fw={600}>
          {t.config.available}
        </Text>
        <Group gap="xs">
          {inv.available_capabilities.map((c) => (
            <Badge key={c.id} variant="outline" color="gray">
              {c.label}
            </Badge>
          ))}
        </Group>
      </Stack>
    </Card>
  );
}
