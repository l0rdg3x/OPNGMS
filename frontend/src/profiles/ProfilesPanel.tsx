import { Button, Group, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { ConfirmModal } from "../components/ConfirmModal";
import { useT } from "../i18n";
import { ProfileFormModal } from "./ProfileFormModal";
import { type Profile, useDeleteProfile, useProfiles } from "./hooks";

export function ProfilesPanel() {
  const t = useT();
  const { data: profiles } = useProfiles();
  const del = useDeleteProfile();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Profile | null>(null);
  const [toDelete, setToDelete] = useState<Profile | null>(null);

  return (
    <Stack>
      <Group justify="flex-end">
        <Button data-testid="prof-new" onClick={() => { setEditing(null); setModalOpen(true); }}>
          {t.templates.profiles.create}
        </Button>
      </Group>
      {profiles && profiles.length > 0 ? (
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th>{t.templates.profiles.name}</Table.Th>
            <Table.Th>{t.templates.profiles.description}</Table.Th>
            <Table.Th>{t.templates.profiles.members}</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {profiles.map((p) => (
              <Table.Tr key={p.id}>
                <Table.Td>{p.name}</Table.Td>
                <Table.Td>{p.description}</Table.Td>
                <Table.Td>{p.template_ids.length + " " + t.templates.profiles.memberCount}</Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end">
                    <Button size="xs" variant="light" onClick={() => { setEditing(p); setModalOpen(true); }}>
                      {t.templates.profiles.edit}
                    </Button>
                    <Button size="xs" variant="light" color="red" onClick={() => setToDelete(p)}>
                      {t.templates.profiles.delete}
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : <Text c="dimmed">{t.templates.profiles.empty}</Text>}

      <ProfileFormModal opened={modalOpen} onClose={() => setModalOpen(false)} editing={editing} />
      <ConfirmModal
        opened={!!toDelete}
        onClose={() => setToDelete(null)}
        onConfirm={async () => {
          const p = toDelete; setToDelete(null);
          if (!p) return;
          try { await del.mutateAsync(p.id); } catch { notifications.show({ color: "red", message: t.templates.profiles.saveFailed }); }
        }}
        title={t.templates.profiles.delete}
        body={t.templates.profiles.deleteConfirm}
        loading={del.isPending}
      />
    </Stack>
  );
}
