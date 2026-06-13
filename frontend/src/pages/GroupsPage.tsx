import { useMemo, useState } from "react";
import {
  ActionIcon, Alert, Badge, Button, Card, Group, MultiSelect, Select, Stack, Switch, Text,
  TextInput, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { ConfirmModal } from "../components/ConfirmModal";
import { useIsSuperadmin } from "../auth/useIsSuperadmin";
import { useT } from "../i18n";
import { useUsers } from "../security/mfaHooks";
import {
  type GroupGrantOut,
  type GroupOut,
  useAddGroupGrant,
  useAllTenants,
  useCreateGroup,
  useDeleteGroup,
  useDeleteGroupGrant,
  useGroups,
  useSetGroupMembers,
  useUpdateGroup,
} from "../groups/groupHooks";

const ROLES = ["tenant_admin", "operator", "read_only"] as const;
type Role = (typeof ROLES)[number];

export function GroupsPage() {
  const t = useT();
  const isSuper = useIsSuperadmin();
  const groups = useGroups();
  const users = useUsers();
  const tenants = useAllTenants();
  const create = useCreateGroup();
  const [toDelete, setToDelete] = useState<GroupOut | null>(null);
  const del = useDeleteGroup();

  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");

  if (!isSuper) {
    return <Alert color="yellow" data-testid="groups-superadmin-gate">{t.groups.superadminOnly}</Alert>;
  }

  async function handleCreate() {
    if (!newName.trim()) return;
    try {
      await create.mutateAsync({ name: newName.trim(), description: newDescription.trim() });
      setNewName("");
      setNewDescription("");
      notifications.show({ message: t.groups.created });
    } catch {
      notifications.show({ color: "red", message: t.groups.saveFailed });
    }
  }

  return (
    <Stack maw={720}>
      <Title order={3}>{t.groups.title}</Title>
      <Text size="sm" c="dimmed">{t.groups.description}</Text>

      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Title order={5}>{t.groups.createTitle}</Title>
          <TextInput
            label={t.groups.name}
            placeholder={t.groups.namePlaceholder}
            value={newName}
            onChange={(e) => setNewName(e.currentTarget.value)}
            data-testid="group-new-name"
          />
          <TextInput
            label={t.groups.descriptionField}
            placeholder={t.groups.descriptionPlaceholder}
            value={newDescription}
            onChange={(e) => setNewDescription(e.currentTarget.value)}
            data-testid="group-new-description"
          />
          <Group justify="flex-end">
            <Button
              onClick={handleCreate}
              loading={create.isPending}
              disabled={!newName.trim()}
              data-testid="group-create"
            >
              {t.groups.create}
            </Button>
          </Group>
        </Stack>
      </Card>

      {groups.isError && <Alert color="red" data-testid="groups-error">{t.groups.loadError}</Alert>}

      {groups.data && groups.data.length === 0 && (
        <Text c="dimmed" data-testid="groups-empty">{t.groups.empty}</Text>
      )}

      {groups.data?.map((group) => (
        <GroupCard
          key={group.id}
          group={group}
          users={users.data ?? []}
          tenants={(tenants.data ?? []).map((tn) => ({ value: tn.id, label: tn.name }))}
          tenantNames={Object.fromEntries((tenants.data ?? []).map((tn) => [tn.id, tn.name]))}
          onRequestDelete={() => setToDelete(group)}
        />
      ))}

      <ConfirmModal
        opened={!!toDelete}
        onClose={() => setToDelete(null)}
        onConfirm={async () => {
          const g = toDelete;
          setToDelete(null);
          if (!g) return;
          try {
            await del.mutateAsync(g.id);
            notifications.show({ message: t.groups.deleted });
          } catch {
            notifications.show({ color: "red", message: t.groups.saveFailed });
          }
        }}
        title={t.groups.delete}
        body={t.groups.deleteConfirm}
        loading={del.isPending}
      />
    </Stack>
  );
}

interface GroupCardProps {
  group: GroupOut;
  users: { id: string; email: string }[];
  tenants: { value: string; label: string }[];
  tenantNames: Record<string, string>;
  onRequestDelete: () => void;
}

function GroupCard({ group, users, tenants, tenantNames, onRequestDelete }: GroupCardProps) {
  const t = useT();
  const update = useUpdateGroup();
  const setMembers = useSetGroupMembers();
  const addGrant = useAddGroupGrant();
  const delGrant = useDeleteGroupGrant();

  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description);

  // Add-grant form state.
  const [allTenants, setAllTenants] = useState(false);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [role, setRole] = useState<Role>("read_only");

  const userOptions = useMemo(
    () => users.map((u) => ({ value: u.id, label: u.email })),
    [users],
  );

  const roleLabels: Record<Role, string> = {
    tenant_admin: t.groups.roleTenantAdmin,
    operator: t.groups.roleOperator,
    read_only: t.groups.roleReadOnly,
  };
  const roleOptions = ROLES.map((r) => ({ value: r, label: roleLabels[r] }));

  function grantScope(grant: GroupGrantOut): string {
    return grant.all_tenants
      ? t.groups.allTenantsScope
      : (grant.tenant_id ? tenantNames[grant.tenant_id] ?? grant.tenant_id : "");
  }

  function grantRole(grant: GroupGrantOut): string {
    return roleLabels[grant.role as Role] ?? grant.role;
  }

  async function handleSaveDetails() {
    try {
      await update.mutateAsync({ id: group.id, body: { name: name.trim(), description } });
      notifications.show({ message: t.groups.updated });
    } catch {
      notifications.show({ color: "red", message: t.groups.saveFailed });
    }
  }

  async function handleSetMembers(userIds: string[]) {
    try {
      await setMembers.mutateAsync({ id: group.id, userIds });
      notifications.show({ message: t.groups.membersSaved });
    } catch {
      notifications.show({ color: "red", message: t.groups.saveFailed });
    }
  }

  async function handleAddGrant() {
    if (!allTenants && !tenantId) return;
    try {
      await addGrant.mutateAsync({
        id: group.id,
        body: allTenants
          ? { all_tenants: true, role }
          : { all_tenants: false, tenant_id: tenantId, role },
      });
      setAllTenants(false);
      setTenantId(null);
      setRole("read_only");
      notifications.show({ message: t.groups.grantAdded });
    } catch {
      notifications.show({ color: "red", message: t.groups.saveFailed });
    }
  }

  async function handleRemoveGrant(grantId: string) {
    try {
      await delGrant.mutateAsync({ id: group.id, grantId });
      notifications.show({ message: t.groups.grantRemoved });
    } catch {
      notifications.show({ color: "red", message: t.groups.saveFailed });
    }
  }

  return (
    <Card withBorder padding="lg" radius="md" data-testid={`group-card-${group.id}`}>
      <Stack>
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Text fw={600} data-testid={`group-name-${group.id}`}>{group.name}</Text>
          <Button
            size="xs"
            variant="light"
            color="red"
            onClick={onRequestDelete}
            data-testid={`group-delete-${group.id}`}
          >
            {t.groups.delete}
          </Button>
        </Group>

        <TextInput
          label={t.groups.name}
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          data-testid={`group-edit-name-${group.id}`}
        />
        <TextInput
          label={t.groups.descriptionField}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          data-testid={`group-edit-description-${group.id}`}
        />
        <Group justify="flex-end">
          <Button
            size="xs"
            onClick={handleSaveDetails}
            loading={update.isPending}
            disabled={!name.trim()}
            data-testid={`group-save-${group.id}`}
          >
            {t.groups.save}
          </Button>
        </Group>

        <MultiSelect
          label={t.groups.members}
          placeholder={t.groups.membersPlaceholder}
          data={userOptions}
          value={group.member_ids}
          onChange={handleSetMembers}
          searchable
          data-testid={`group-members-${group.id}`}
        />

        <Stack gap="xs">
          <Text size="sm" fw={500}>{t.groups.grants}</Text>
          {group.grants.length === 0 ? (
            <Text size="sm" c="dimmed" data-testid={`group-no-grants-${group.id}`}>
              {t.groups.noGrants}
            </Text>
          ) : (
            group.grants.map((grant) => (
              <Group key={grant.id} justify="space-between" wrap="nowrap" data-testid={`grant-${grant.id}`}>
                <Badge variant="light">
                  {grantScope(grant)} → {grantRole(grant)}
                </Badge>
                <ActionIcon
                  variant="subtle"
                  color="red"
                  aria-label={t.groups.removeGrant}
                  onClick={() => handleRemoveGrant(grant.id)}
                  data-testid={`grant-remove-${grant.id}`}
                >
                  ✕
                </ActionIcon>
              </Group>
            ))
          )}
        </Stack>

        <Card withBorder padding="md" radius="sm" bg="var(--mantine-color-default)">
          <Stack gap="sm">
            <Text size="sm" fw={500}>{t.groups.addGrant}</Text>
            <Switch
              label={t.groups.allTenants}
              checked={allTenants}
              onChange={(e) => setAllTenants(e.currentTarget.checked)}
              data-testid={`grant-all-tenants-${group.id}`}
            />
            {!allTenants && (
              <Select
                label={t.groups.tenant}
                placeholder={t.groups.tenantPlaceholder}
                data={tenants}
                value={tenantId}
                onChange={setTenantId}
                searchable
                data-testid={`grant-tenant-${group.id}`}
              />
            )}
            <Select
              label={t.groups.role}
              data={roleOptions}
              value={role}
              onChange={(v) => v && setRole(v as Role)}
              allowDeselect={false}
              data-testid={`grant-role-${group.id}`}
            />
            <Group justify="flex-end">
              <Button
                size="xs"
                variant="light"
                onClick={handleAddGrant}
                loading={addGrant.isPending}
                disabled={!allTenants && !tenantId}
                data-testid={`grant-add-${group.id}`}
              >
                {t.groups.addGrant}
              </Button>
            </Group>
          </Stack>
        </Card>
      </Stack>
    </Card>
  );
}
