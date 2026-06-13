// frontend/src/catalog/CatalogMenuTree.tsx
import { useState } from "react";
import { Badge, NavLink } from "@mantine/core";
import { useT } from "../i18n";
import type { CatalogDiffData, MenuNode } from "./catalogTypes";

/** Does this model id carry cross-version changes (added model, or non-empty field diff)? */
function hasDiff(diff: CatalogDiffData | undefined, modelId: string): boolean {
  if (!diff) return false;
  if (diff.added_models.includes(modelId)) return true;
  const m = diff.models[modelId];
  return !!m && (m.added_fields.length + m.removed_fields.length + m.changed_fields.length > 0);
}

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
  nodes, baseUrl, search, selected, onSelect, diff,
}: {
  nodes: MenuNode[];
  baseUrl: string;
  search: string;
  selected: string | null;
  onSelect: (modelId: string) => void;
  diff?: CatalogDiffData;
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
                selected={selected} onSelect={onSelect} diff={diff} />
            </NavLink>
          );
        }
        if (node.model_id) {
          const flagged = hasDiff(diff, node.model_id);
          return (
            <NavLink key={node.id} label={node.label} active={selected === node.model_id}
              onClick={() => onSelect(node.model_id!)}
              rightSection={flagged ? (
                <Badge color="teal" size="xs" circle data-testid={`catalog-menu-diff-${node.model_id}`}
                  aria-label={t.catalog.diff.changes} />
              ) : null} />
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
