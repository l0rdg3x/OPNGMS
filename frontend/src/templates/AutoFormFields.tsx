import { MultiSelect, Select, Switch, TextInput } from "@mantine/core";
import type { SettingField } from "./settingHooks";

/**
 * Presentational renderer for an introspection-driven field schema. Renders the
 * switch/select/multiselect/text control per field, reading/writing the controlled
 * `payload` via `onField`. Shared by the setting form and the firewall-rule form.
 */
export function AutoFormFields({
  fields,
  payload,
  onField,
  testidPrefix = "setting",
}: {
  fields: SettingField[];
  payload: Record<string, string>;
  onField: (path: string, value: string) => void;
  testidPrefix?: string;
}) {
  return (
    <>
      {fields.map((field) => {
        const current = payload[field.path];
        if (field.control === "switch") {
          const checked = (current ?? String(field.value)) === "1";
          return (
            <Switch
              key={field.path}
              label={field.label}
              data-testid={`${testidPrefix}-field-${field.path}`}
              checked={checked}
              onChange={(e) => onField(field.path, e.currentTarget.checked ? "1" : "0")}
            />
          );
        }
        if (field.control === "select") {
          return (
            <Select
              key={field.path}
              label={field.label}
              data={field.options ?? []}
              data-testid={`${testidPrefix}-field-${field.path}`}
              value={current ?? String(field.value)}
              onChange={(key) => onField(field.path, key ?? "")}
            />
          );
        }
        if (field.control === "multiselect") {
          const fallback = Array.isArray(field.value) ? field.value.join(",") : "";
          const selected = (current ?? fallback).split(",").filter(Boolean);
          return (
            <MultiSelect
              key={field.path}
              label={field.label}
              data={field.options ?? []}
              data-testid={`${testidPrefix}-field-${field.path}`}
              value={selected}
              onChange={(keys) => onField(field.path, keys.join(","))}
            />
          );
        }
        return (
          <TextInput
            key={field.path}
            label={field.label}
            data-testid={`${testidPrefix}-field-${field.path}`}
            value={current ?? String(field.value)}
            onChange={(e) => onField(field.path, e.currentTarget.value)}
          />
        );
      })}
    </>
  );
}
