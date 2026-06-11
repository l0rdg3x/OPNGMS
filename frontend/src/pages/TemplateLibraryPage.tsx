import { Alert, Badge, Button, Group, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { ConfirmModal } from "../components/ConfirmModal";
import { useIsSuperadmin } from "../auth/useIsSuperadmin";
import { useT } from "../i18n";
import { ProfilesPanel } from "../profiles/ProfilesPanel";
import { type Template, useDeleteTemplate, useTemplates } from "../templates/hooks";
import { TemplateFormModal } from "../templates/TemplateFormModal";

export function TemplateLibraryPage() {
  const t = useT();
  const isSuper = useIsSuperadmin();
  const { data: templates } = useTemplates();
  const del = useDeleteTemplate();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Template | null>(null);
  const [toDelete, setToDelete] = useState<Template | null>(null);

  if (!isSuper) {
    return <Alert color="yellow" data-testid="tpl-superadmin-gate">{t.templates.superadminOnly}</Alert>;
  }

  return (
    <Stack>
      <Title order={3}>{t.templates.libraryTitle}</Title>
      <Tabs defaultValue="templates">
        <Tabs.List>
          <Tabs.Tab value="templates" data-testid="tab-templates">{t.templates.templatesTab}</Tabs.Tab>
          <Tabs.Tab value="profiles" data-testid="tab-profiles">{t.templates.profiles.tab}</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="templates" pt="md">
          <Stack>
            <Group justify="flex-end">
              <Button data-testid="tpl-new" onClick={() => { setEditing(null); setModalOpen(true); }}>
                {t.templates.create}
              </Button>
            </Group>
            {templates && templates.length > 0 ? (
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th>{t.templates.name}</Table.Th><Table.Th>{t.templates.kind}</Table.Th>
            <Table.Th>{t.templates.description}</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {templates.map((tpl) => (
              <Table.Tr key={tpl.id}>
                <Table.Td>{tpl.name}</Table.Td>
                <Table.Td><Badge variant="light">{tpl.kind}</Badge></Table.Td>
                <Table.Td>{tpl.description}</Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end">
                    <Button size="xs" variant="light" onClick={() => { setEditing(tpl); setModalOpen(true); }}>
                      {t.templates.edit}
                    </Button>
                    <Button size="xs" variant="light" color="red" onClick={() => setToDelete(tpl)}>
                      {t.templates.delete}
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : <Text c="dimmed">{t.templates.empty}</Text>}

            <TemplateFormModal opened={modalOpen} onClose={() => setModalOpen(false)} editing={editing} />
            <ConfirmModal
              opened={!!toDelete}
              onClose={() => setToDelete(null)}
              onConfirm={async () => {
                const tpl = toDelete; setToDelete(null);
                if (!tpl) return;
                try { await del.mutateAsync(tpl.id); } catch { notifications.show({ color: "red", message: t.templates.saveFailed }); }
              }}
              title={t.templates.delete}
              body={t.templates.deleteConfirm}
              loading={del.isPending}
            />
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="profiles" pt="md">
          <ProfilesPanel />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
