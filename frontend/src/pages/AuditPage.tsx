import { useState } from "react";
import {
  Alert,
  Button,
  Code,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";

import { downloadAuditCsv, useAuditQuery, type AuditEntryOut } from "../audit/auditHooks";
import { useT } from "../i18n";

const PAGE_SIZE = 50;

// DateTimePicker yields "YYYY-MM-DD HH:mm:ss"; convert to ISO 8601 for the API.
function toIso(value: string | null): string | undefined {
  if (!value) return undefined;
  const d = new Date(value.replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

// Render the JSON details blob compactly; an empty object shows as a dash.
function DetailsCell({ details }: { details: AuditEntryOut["details"] }) {
  const keys = Object.keys(details ?? {});
  if (keys.length === 0) return <Text c="dimmed">—</Text>;
  return (
    <Code block style={{ maxWidth: 320, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {JSON.stringify(details, null, 2)}
    </Code>
  );
}

export function AuditPage() {
  const t = useT();

  // Draft filter inputs (edited freely); committed to `applied` on Apply so the query only refetches
  // when the user asks for it. Offset resets to 0 whenever filters change.
  const [actorEmail, setActorEmail] = useState("");
  const [tenant, setTenant] = useState("");
  const [action, setAction] = useState("");
  const [from, setFrom] = useState<string | null>(null);
  const [to, setTo] = useState<string | null>(null);
  const [applied, setApplied] = useState<{
    actor_email?: string;
    tenant_id?: string;
    action?: string;
    frm?: string;
    to?: string;
  }>({});
  const [offset, setOffset] = useState(0);

  const filters = { ...applied, limit: PAGE_SIZE, offset };
  const q = useAuditQuery(filters);

  function applyFilters() {
    setApplied({
      actor_email: actorEmail.trim() || undefined,
      tenant_id: tenant.trim() || undefined,
      action: action.trim() || undefined,
      frm: toIso(from),
      to: toIso(to),
    });
    setOffset(0);
  }

  function resetFilters() {
    setActorEmail("");
    setTenant("");
    setAction("");
    setFrom(null);
    setTo(null);
    setApplied({});
    setOffset(0);
  }

  async function exportCsv() {
    try {
      await downloadAuditCsv(applied);
    } catch {
      notifications.show({ color: "red", message: t.audit.exportFailed });
    }
  }

  const total = q.data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={3}>{t.audit.title}</Title>
          <Text size="sm" c="dimmed">{t.audit.subtitle}</Text>
        </Stack>
        <Button variant="default" size="xs" onClick={exportCsv} data-testid="audit-export">
          {t.audit.export}
        </Button>
      </Group>

      <Group align="flex-end" gap="sm" wrap="wrap">
        <TextInput
          label={t.audit.filters.actor}
          value={actorEmail}
          onChange={(e) => setActorEmail(e.currentTarget.value)}
          data-testid="audit-filter-actor"
        />
        <TextInput
          label={t.audit.filters.tenant}
          value={tenant}
          onChange={(e) => setTenant(e.currentTarget.value)}
          data-testid="audit-filter-tenant"
        />
        <TextInput
          label={t.audit.filters.action}
          value={action}
          onChange={(e) => setAction(e.currentTarget.value)}
          data-testid="audit-filter-action"
        />
        <DateTimePicker
          label={t.audit.filters.from}
          value={from}
          onChange={setFrom}
          data-testid="audit-filter-from"
        />
        <DateTimePicker
          label={t.audit.filters.to}
          value={to}
          onChange={setTo}
          data-testid="audit-filter-to"
        />
        <Button size="xs" onClick={applyFilters} data-testid="audit-apply">
          {t.audit.filters.apply}
        </Button>
        <Button size="xs" variant="subtle" onClick={resetFilters} data-testid="audit-reset">
          {t.audit.filters.reset}
        </Button>
      </Group>

      {q.isLoading && <Loader />}
      {q.isError && <Alert color="red">{t.errors.auditLoad}</Alert>}
      {q.data && q.data.items.length === 0 && (
        <Text c="dimmed" data-testid="audit-empty">{t.audit.empty}</Text>
      )}
      {q.data && q.data.items.length > 0 && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.audit.columns.time}</Table.Th>
              <Table.Th>{t.audit.columns.actor}</Table.Th>
              <Table.Th>{t.audit.columns.tenant}</Table.Th>
              <Table.Th>{t.audit.columns.action}</Table.Th>
              <Table.Th>{t.audit.columns.target}</Table.Th>
              <Table.Th>{t.audit.columns.ip}</Table.Th>
              <Table.Th>{t.audit.columns.details}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.items.map((e) => (
              <Table.Tr key={e.id} data-testid={`audit-row-${e.id}`}>
                <Table.Td className="noc-mono">{e.ts}</Table.Td>
                <Table.Td>{e.actor_email ?? e.actor_user_id ?? "—"}</Table.Td>
                <Table.Td>{e.tenant_name ?? "—"}</Table.Td>
                <Table.Td><Code>{e.action}</Code></Table.Td>
                <Table.Td>
                  {e.target_type ? `${e.target_type}${e.target_id ? `:${e.target_id}` : ""}` : "—"}
                </Table.Td>
                <Table.Td className="noc-mono">{e.ip ?? "—"}</Table.Td>
                <Table.Td><DetailsCell details={e.details} /></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {q.data && total > 0 && (
        <Group justify="space-between">
          <Text size="sm" c="dimmed">{t.audit.showingTotal.replace("{total}", String(total))}</Text>
          <Group gap="sm">
            <Button
              size="xs"
              variant="default"
              disabled={!hasPrev}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              data-testid="audit-prev"
            >
              {t.audit.prev}
            </Button>
            <Text size="sm">{t.audit.page.replace("{page}", String(page))}</Text>
            <Button
              size="xs"
              variant="default"
              disabled={!hasNext}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
              data-testid="audit-next"
            >
              {t.audit.next}
            </Button>
          </Group>
        </Group>
      )}
    </Stack>
  );
}
