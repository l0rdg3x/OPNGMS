import { Select } from "@mantine/core";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

export function TenantSwitcher() {
  const t = useT();
  const { tenants, activeId, setActiveId } = useTenant();
  if (tenants.length === 0) return <span>{t.tenant.none}</span>;
  return (
    <Select
      aria-label={t.tenant.activeLabel}
      data={tenants.map((t) => ({ value: t.id, label: t.name }))}
      value={activeId}
      onChange={(v) => v && setActiveId(v)}
      allowDeselect={false}
      w={220}
    />
  );
}
