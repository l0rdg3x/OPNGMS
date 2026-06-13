// frontend/src/catalog/CatalogMenuTree.tsx
import { useState } from "react";
import { NavLink } from "@mantine/core";
import { useT } from "../i18n";
import type { MenuNode } from "./catalogTypes";

/** Validated WebGUI deep-link (http(s) base only), else undefined (mirrors DeviceActions). */
function deepLink(baseUrl: string, url: string): string | undefined {
  if (!/^https?:\/\//i.test(baseUrl)) return undefined;
  const path = url.startsWith("/") ? url : "/" + url;  // a Menu.xml url may omit the leading slash
  return baseUrl.replace(/\/$/, "") + path;
}

function matches(node: MenuNode, q: string): boolean {
  if (!q) return true;
  if (node.label.toLowerCase().includes(q) || (node.url ?? "").toLowerCase().includes(q)) return true;
  return (node.children ?? []).some((c) => matches(c, q));
}

export function CatalogMenuTree({
  nodes, baseUrl, search, selected, onSelect,
}: {
  nodes: MenuNode[];
  baseUrl: string;
  search: string;
  selected: string | null;
  onSelect: (modelId: string) => void;
}) {
  const t = useT();
  const q = search.trim().toLowerCase();
  // A branch is open when there's an active search (reveal matches) OR the user expanded it.
  // `opened` is controlled — Mantine's `defaultOpened` is uncontrolled and would NOT react to search.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setExpanded((s) => {
      const n = new Set(s);
      if (n.has(id)) { n.delete(id); } else { n.add(id); }
      return n;
    });
  return (
    <>
      {nodes.filter((n) => matches(n, q)).map((node) => {
        if (node.children && node.children.length > 0) {
          return (
            <NavLink key={node.id} label={node.label}
              opened={!!q || expanded.has(node.id)} onClick={() => toggle(node.id)}
              leftSection={node.icon ? <i className={node.icon} /> : null}>
              <CatalogMenuTree nodes={node.children} baseUrl={baseUrl} search={search}
                selected={selected} onSelect={onSelect} />
            </NavLink>
          );
        }
        if (node.model_id) {
          return (
            <NavLink key={node.id} label={node.label} active={selected === node.model_id}
              onClick={() => onSelect(node.model_id!)} />
          );
        }
        const href = node.url ? deepLink(baseUrl, node.url) : undefined;
        return (
          <NavLink key={node.id} label={node.label} disabled={!href}
            component="a" href={href} target="_blank" rel="noreferrer"
            data-testid={`catalog-menu-link-${node.id}`}
            description={href ? t.catalog.openWebgui : undefined} />
        );
      })}
    </>
  );
}
