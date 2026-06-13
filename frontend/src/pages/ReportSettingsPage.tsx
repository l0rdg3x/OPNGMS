import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  FileInput,
  Group,
  Loader,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { usePermissions } from "../auth/usePermissions";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import {
  useDeleteLogo,
  useReportLanguages,
  useReportSettings,
  useUpdateReportSettings,
  useUploadLogo,
} from "../reports/settingsHooks";
import {
  buildSectionsMap,
  REPORT_SECTION_KEYS,
  type ReportSectionKey,
  seedSectionState,
  sectionLabel,
} from "../reports/sections";

export function ReportSettingsPage() {
  const t = useT();
  const { isTenantAdmin } = usePermissions();

  if (!isTenantAdmin) {
    return (
      <Alert color="yellow" data-testid="admins-only-alert">
        {t.reports.settings.adminsOnly}
      </Alert>
    );
  }

  return <ReportSettingsForm />;
}

function ReportSettingsForm() {
  const t = useT();
  const { activeId } = useTenant();
  const settingsQuery = useReportSettings();
  const languagesQuery = useReportLanguages();
  const updateMutation = useUpdateReportSettings();
  const uploadLogoMutation = useUploadLogo();
  const deleteLogoMutation = useDeleteLogo();

  const [logoFile, setLogoFile] = useState<File | null>(null);
  // Cache-buster for the logo preview image
  const [logoCacheBust, setLogoCacheBust] = useState(0);
  // Tenant-level section defaults (seeded from the loaded settings).
  const [sections, setSections] = useState<Record<ReportSectionKey, boolean>>(() =>
    seedSectionState(undefined),
  );

  const form = useForm({
    initialValues: {
      title: "",
      owner: "",
      timezone: "UTC",
      language: "en",
      from_email: "",
    },
  });

  // Track whether the form has been initialized from the query data
  const initializedRef = useRef(false);
  useEffect(() => {
    if (settingsQuery.data && !initializedRef.current) {
      initializedRef.current = true;
      form.setValues({
        title: settingsQuery.data.title,
        owner: settingsQuery.data.owner,
        timezone: settingsQuery.data.timezone,
        language: settingsQuery.data.language ?? "en",
        from_email: settingsQuery.data.from_email ?? "",
      });
      setSections(seedSectionState(settingsQuery.data.sections));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingsQuery.data]);

  const languageOptions = (languagesQuery.data ?? []).map((l) => ({
    value: l.code,
    label: l.name,
  }));

  async function handleSave() {
    try {
      await updateMutation.mutateAsync({
        title: form.values.title,
        owner: form.values.owner,
        timezone: form.values.timezone,
        language: form.values.language,
        from_email: form.values.from_email,
        sections: buildSectionsMap(sections),
      });
      notifications.show({ message: t.reports.settings.saved });
    } catch {
      notifications.show({ color: "red", message: t.errors.reportSettingsAction });
    }
  }

  async function handleLogoUpload() {
    if (!logoFile) return;
    try {
      await uploadLogoMutation.mutateAsync(logoFile);
      setLogoFile(null);
      setLogoCacheBust((n) => n + 1);
      notifications.show({ message: t.reports.settings.logoUploaded });
    } catch {
      notifications.show({ color: "red", message: t.errors.reportSettingsAction });
    }
  }

  async function handleLogoDelete() {
    try {
      await deleteLogoMutation.mutateAsync();
      setLogoCacheBust((n) => n + 1);
      notifications.show({ message: t.reports.settings.logoRemoved });
    } catch {
      notifications.show({ color: "red", message: t.errors.reportSettingsAction });
    }
  }

  if (settingsQuery.isLoading) return <Loader />;
  if (settingsQuery.error) return <Text c="red">{t.errors.reportSettingsLoad}</Text>;

  const hasLogo = settingsQuery.data?.has_logo ?? false;
  const logoPreviewUrl = hasLogo && activeId
    ? `${import.meta.env.VITE_API_BASE ?? ""}/api/tenants/${activeId}/reports/settings/logo?cb=${logoCacheBust}`
    : null;

  return (
    <Stack maw={480}>
      <Title order={3}>{t.reports.settings.pageTitle}</Title>

      <TextInput
        label={t.reports.settings.title}
        {...form.getInputProps("title")}
        data-testid="field-title"
      />
      <TextInput
        label={t.reports.settings.owner}
        {...form.getInputProps("owner")}
        data-testid="field-owner"
      />
      <TextInput
        label={t.reports.settings.timezone}
        {...form.getInputProps("timezone")}
        data-testid="field-timezone"
      />
      <TextInput
        label={t.reports.settings.fromEmail}
        {...form.getInputProps("from_email")}
        data-testid="field-from-email"
      />

      <Select
        label={t.reports.settings.language}
        data={languageOptions}
        {...form.getInputProps("language")}
        data-testid="field-language"
      />

      <Title order={5} mt="md">{t.reports.sections.title}</Title>
      <Text size="sm" c="dimmed">{t.reports.sections.description}</Text>
      <Stack gap="xs">
        {REPORT_SECTION_KEYS.map((key) => (
          <Switch
            key={key}
            label={sectionLabel(t, key)}
            checked={sections[key]}
            onChange={(e) =>
              setSections((prev) => ({ ...prev, [key]: e.currentTarget.checked }))
            }
            data-testid={`section-toggle-${key}`}
          />
        ))}
      </Stack>

      <Button
        onClick={handleSave}
        loading={updateMutation.isPending}
        data-testid="btn-save"
      >
        {t.reports.settings.save}
      </Button>

      <Title order={5} mt="md">{t.reports.settings.logo}</Title>

      <Text size="sm" c="dimmed">
        {hasLogo ? t.reports.settings.hasLogo : t.reports.settings.noLogo}
      </Text>

      {logoPreviewUrl && (
        <img
          src={logoPreviewUrl}
          alt="logo"
          style={{ maxHeight: "80px", objectFit: "contain", alignSelf: "flex-start" }}
          data-testid="logo-preview"
        />
      )}

      <FileInput
        accept="image/png,image/jpeg"
        placeholder={t.reports.settings.upload}
        value={logoFile}
        onChange={setLogoFile}
        data-testid="file-input-logo"
      />

      <Group>
        <Button
          onClick={handleLogoUpload}
          loading={uploadLogoMutation.isPending}
          disabled={!logoFile}
          data-testid="btn-upload"
        >
          {t.reports.settings.upload}
        </Button>

        {hasLogo && (
          <Button
            color="red"
            variant="light"
            onClick={handleLogoDelete}
            loading={deleteLogoMutation.isPending}
            data-testid="btn-remove-logo"
          >
            {t.reports.settings.remove}
          </Button>
        )}
      </Group>
    </Stack>
  );
}
