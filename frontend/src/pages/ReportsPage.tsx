import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { usePermissions } from "../auth/usePermissions";
import { useT } from "../i18n";
import {
  useDownloadReport,
  useGeneratedReports,
  useGenerateReport,
} from "../reports/reportHooks";
import { humanBytes } from "../utils/bytes";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function defaultFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  // DateTimePicker string format: "YYYY-MM-DD HH:mm:ss"
  return d.toISOString().replace("T", " ").slice(0, 19);
}

function defaultTo(): string {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

export function ReportsPage() {
  const t = useT();
  const { isOperator: canGenerate } = usePermissions();

  const reportsQuery = useGeneratedReports();
  const generateMutation = useGenerateReport();
  const downloadMutation = useDownloadReport();

  const [from, setFrom] = useState<string | null>(defaultFrom);
  const [to, setTo] = useState<string | null>(defaultTo);
  const [pendingDownloadId, setPendingDownloadId] = useState<string | null>(null);

  async function handleGenerate() {
    if (!from || !to) return;
    try {
      // DateTimePicker gives "YYYY-MM-DD HH:mm:ss"; convert to ISO 8601 for the API
      await generateMutation.mutateAsync({
        from: new Date(from.replace(" ", "T")).toISOString(),
        to: new Date(to.replace(" ", "T")).toISOString(),
      });
      notifications.show({ message: t.reports.page.generated });
    } catch {
      notifications.show({ color: "red", message: t.errors.reportGenerate });
    }
  }

  async function handleDownload(id: string) {
    setPendingDownloadId(id);
    try {
      await downloadMutation.mutateAsync(id);
    } catch {
      notifications.show({ color: "red", message: t.errors.reportsLoad });
    } finally {
      setPendingDownloadId(null);
    }
  }

  return (
    <Stack>
      <Title order={3}>{t.reports.page.title}</Title>

      {canGenerate && (
        <Card withBorder padding="md" data-testid="generate-card">
          <Stack gap="sm">
            <Text fw={600}>{t.reports.page.generate}</Text>
            <Group align="flex-end" gap="sm">
              <DateTimePicker
                label={t.reports.page.from}
                value={from}
                onChange={setFrom}
                data-testid="picker-from"
              />
              <DateTimePicker
                label={t.reports.page.to}
                value={to}
                onChange={setTo}
                data-testid="picker-to"
              />
              <Button
                onClick={handleGenerate}
                loading={generateMutation.isPending}
                disabled={!from || !to}
                data-testid="btn-generate"
              >
                {t.reports.page.generate}
              </Button>
            </Group>
          </Stack>
        </Card>
      )}

      {reportsQuery.isLoading && <Loader />}
      {reportsQuery.isError && (
        <Text c="red" data-testid="reports-error">
          {t.errors.reportsLoad}
        </Text>
      )}

      {!reportsQuery.isLoading && !reportsQuery.isError && (
        <>
          {(reportsQuery.data ?? []).length === 0 ? (
            <Text c="dimmed" data-testid="reports-empty">
              {t.reports.page.none}
            </Text>
          ) : (
            <Table striped highlightOnHover data-testid="reports-table">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t.reports.page.period}</Table.Th>
                  <Table.Th>{t.reports.page.kind}</Table.Th>
                  <Table.Th>{t.reports.page.created}</Table.Th>
                  <Table.Th>{t.reports.page.size}</Table.Th>
                  <Table.Th>{t.reports.page.download}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(reportsQuery.data ?? []).map((report) => (
                  <Table.Tr key={report.id} data-testid={`row-${report.id}`}>
                    <Table.Td>
                      {formatDate(report.period_from)} – {formatDate(report.period_to)}
                    </Table.Td>
                    <Table.Td>
                      <Badge color={report.kind === "on_demand" ? "blue" : "green"}>
                        {report.kind === "on_demand"
                          ? t.reports.page.kindOnDemand
                          : t.reports.page.kindScheduled}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{formatDate(report.created_at)}</Table.Td>
                    <Table.Td>{humanBytes(report.size)}</Table.Td>
                    <Table.Td>
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => handleDownload(report.id)}
                        loading={downloadMutation.isPending && pendingDownloadId === report.id}
                        data-testid={`btn-download-${report.id}`}
                      >
                        {t.reports.page.download}
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}
    </Stack>
  );
}
