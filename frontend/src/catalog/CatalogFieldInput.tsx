// frontend/src/catalog/CatalogFieldInput.tsx
import { MultiSelect, NumberInput, Select, Switch, TextInput } from "@mantine/core";
import type { CatalogField } from "./catalogTypes";

/** A single catalog field as a controlled Mantine input. `value` is always a string
 *  (multienum = comma-joined keys); onChange reports the new string value for the path. */
export function CatalogFieldInput({
  field, value, onChange, disabled, liveOptions,
}: {
  field: CatalogField;
  value: string;
  onChange: (path: string, value: string) => void;
  disabled: boolean;
  liveOptions?: { value: string; label: string }[];
}) {
  const label = field.label || field.path;
  const testid = `catalog-field-${field.path}`;
  // Live dropdown: prefer device-provided options for ref/enum/multienum.
  const live = liveOptions && liveOptions.length > 0 ? liveOptions : null;
  const options = live ?? (field.options ?? []).map((o) => ({ value: o, label: o }));

  if (field.type === "bool") {
    return (
      <Switch
        label={label} data-testid={testid} disabled={disabled}
        checked={value === "1"}
        onChange={(e) => onChange(field.path, e.currentTarget.checked ? "1" : "0")} />
    );
  }
  if (field.type === "int") {
    return (
      <NumberInput
        label={label} data-testid={testid} disabled={disabled}
        value={value === "" ? "" : Number(value)}
        onChange={(v) => onChange(field.path, v === "" || v == null ? "" : String(v))} />
    );
  }
  if (field.type === "enum" || (field.type === "ref" && live)) {
    return (
      <Select
        label={label} data={options} data-testid={testid} disabled={disabled}
        value={value} onChange={(v) => onChange(field.path, v ?? "")} />
    );
  }
  if (field.type === "multienum") {
    const selected = value.split(",").filter(Boolean);
    return (
      <MultiSelect
        label={label} data={options} data-testid={testid} disabled={disabled}
        value={selected} onChange={(keys) => onChange(field.path, keys.join(","))} />
    );
  }
  return (
    <TextInput
      label={label} data-testid={testid} disabled={disabled}
      value={value} onChange={(e) => onChange(field.path, e.currentTarget.value)} />
  );
}
