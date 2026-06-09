import { Select } from "@mantine/core";
import { useTenant } from "../tenant/useTenant";

export function TenantSwitcher() {
  const { tenants, activeId, setActiveId } = useTenant();
  if (tenants.length === 0) return <span>Nessun cliente</span>;
  return (
    <Select
      aria-label="Cliente attivo"
      data={tenants.map((t) => ({ value: t.id, label: t.name }))}
      value={activeId}
      onChange={(v) => v && setActiveId(v)}
      allowDeselect={false}
      w={220}
    />
  );
}
