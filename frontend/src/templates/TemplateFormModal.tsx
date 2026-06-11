import { Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useEffect, useState } from "react";
import { useT } from "../i18n";
import { type Template, useCreateTemplate, useUpdateTemplate } from "./hooks";
import { IdsRulesetForm } from "./IdsRulesetForm";
import { OpnsenseSettingForm } from "./OpnsenseSettingForm";
import { FirewallRuleForm } from "./FirewallRuleForm";

const ALIAS_TYPES = ["host", "network", "port", "url", "urltable", "geoip", "networkgroup", "mac", "dynipv6host"];

type SettingBody = { endpoint_key: string; payload: Record<string, string> };
const EMPTY_SETTING: SettingBody = { endpoint_key: "", payload: {} };

type IdsBody = { rulesets: string[] };
const EMPTY_IDS: IdsBody = { rulesets: [] };

type RuleBody = { payload: Record<string, string> };
const EMPTY_RULE: RuleBody = { payload: {} };

export function TemplateFormModal(
  { opened, onClose, editing }: { opened: boolean; onClose: () => void; editing: Template | null },
) {
  const t = useT();
  const create = useCreateTemplate();
  const update = useUpdateTemplate();
  const [kind, setKind] = useState<string>("firewall_alias");
  const [settingBody, setSettingBody] = useState<SettingBody>(EMPTY_SETTING);
  const [idsBody, setIdsBody] = useState<IdsBody>(EMPTY_IDS);
  const [ruleBody, setRuleBody] = useState<RuleBody>(EMPTY_RULE);
  const form = useForm({
    initialValues: { name: "", type: "host", content: "", description: "" },
  });

  useEffect(() => {
    if (opened) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setKind(editing?.kind ?? "firewall_alias");
      setSettingBody(editing?.kind === "opnsense_setting"
        ? ((editing.body as SettingBody | undefined) ?? EMPTY_SETTING)
        : EMPTY_SETTING);
      setIdsBody(editing?.kind === "suricata_ruleset"
        ? ((editing.body as IdsBody | undefined) ?? EMPTY_IDS)
        : EMPTY_IDS);
      setRuleBody(editing?.kind === "firewall_rule"
        ? { payload: (editing.body as Record<string, string> | undefined) ?? {} }
        : EMPTY_RULE);
      form.setValues(editing && editing.kind !== "opnsense_setting"
        ? { name: editing.name, type: String(editing.body?.type ?? "host"),
            content: (Array.isArray(editing.body?.content) ? editing.body.content : []).join("\n"),
            description: editing.description ?? "" }
        : editing
        ? { name: editing.name, type: "host", content: "", description: editing.description ?? "" }
        : { name: "", type: "host", content: "", description: "" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, editing]);

  async function submit(v: typeof form.values) {
    try {
      if (kind === "opnsense_setting") {
        if (editing) {
          await update.mutateAsync({ id: editing.id,
            body: { name: v.name, description: v.description, body: settingBody } });
          notifications.show({ message: t.templates.updated });
        } else {
          await create.mutateAsync({ kind: "opnsense_setting", name: v.name,
            description: v.description, body: settingBody });
          notifications.show({ message: t.templates.created });
        }
      } else if (kind === "suricata_ruleset") {
        if (editing) {
          await update.mutateAsync({ id: editing.id,
            body: { name: v.name, description: v.description, body: idsBody } });
          notifications.show({ message: t.templates.updated });
        } else {
          await create.mutateAsync({ kind: "suricata_ruleset", name: v.name,
            description: v.description, body: idsBody });
          notifications.show({ message: t.templates.created });
        }
      } else if (kind === "firewall_rule") {
        if (editing) {
          await update.mutateAsync({ id: editing.id,
            body: { name: v.name, description: v.description, body: ruleBody.payload } });
          notifications.show({ message: t.templates.updated });
        } else {
          await create.mutateAsync({ kind: "firewall_rule", name: v.name,
            description: v.description, body: ruleBody.payload });
          notifications.show({ message: t.templates.created });
        }
      } else {
        const content = v.content.split("\n").map((s) => s.trim()).filter(Boolean);
        const body = { name: v.name, type: v.type, content, description: v.description };
        if (editing) {
          await update.mutateAsync({ id: editing.id, body: { name: v.name, description: v.description, body } });
          notifications.show({ message: t.templates.updated });
        } else {
          await create.mutateAsync({ kind: "firewall_alias", name: v.name, description: v.description, body });
          notifications.show({ message: t.templates.created });
        }
      }
      onClose();
    } catch {
      notifications.show({ color: "red", message: t.templates.saveFailed });
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={editing ? t.templates.edit : t.templates.create}
           transitionProps={{ duration: 0 }} data-testid="tpl-modal">
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          <TextInput label={t.templates.name} required data-testid="tpl-name" {...form.getInputProps("name")} />
          <TextInput label={t.templates.description} data-testid="tpl-desc" {...form.getInputProps("description")} />
          <Select
            label={t.templates.kindLabel}
            data-testid="tpl-kind"
            data={[
              { value: "firewall_alias", label: t.templates.kindAlias },
              { value: "opnsense_setting", label: t.templates.kindSetting },
              { value: "suricata_ruleset", label: t.templates.kindIdsRulesets },
              { value: "firewall_rule", label: t.templates.kindFirewallRule },
            ]}
            value={kind}
            onChange={(k) => setKind(k ?? "firewall_alias")}
            allowDeselect={false}
          />
          {kind === "opnsense_setting"
            ? <OpnsenseSettingForm value={settingBody} onChange={setSettingBody} />
            : kind === "suricata_ruleset"
            ? <IdsRulesetForm value={idsBody} onChange={setIdsBody} />
            : kind === "firewall_rule"
            ? <FirewallRuleForm value={ruleBody} onChange={setRuleBody} />
            : (
              <>
                <Select label={t.templates.type} data={ALIAS_TYPES} data-testid="tpl-type"
                        {...form.getInputProps("type")} />
                <Textarea label={t.templates.content} rows={4} required data-testid="tpl-content"
                          {...form.getInputProps("content")} />
              </>
            )}
          <Group justify="flex-end">
            <Button type="submit" loading={create.isPending || update.isPending} data-testid="tpl-save">
              {t.templates.save}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
