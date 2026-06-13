import { Box, Group, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  type RSMGeography,
  ZoomableGroup,
} from "react-simple-maps";
import { useLocale, useT } from "../i18n";
// The world topojson ships with numeric ISO ids; we key our data on alpha-2, so we bridge
// via the vendored numeric→alpha2 lookup. `moduleResolution: bundler` resolves these JSON
// imports without `resolveJsonModule`.
import countries110m from "world-atlas/countries-110m.json";
import numericToAlpha2 from "./numeric-to-alpha2.json";

/** Color ramp endpoints (dark UI). Base = absent / 0 %, hot = the coral the list bars use. */
const BASE_COLOR = "#243043";
const HOT_COLOR = "#ff6b6b";
/** Country outline. */
const STROKE_COLOR = "#0b1220";

const numericMap = numericToAlpha2 as Record<string, string>;

interface CountryRow {
  code: string;
  count: number;
  pct: number;
}

interface HoverState {
  alpha2: string;
  count: number;
  pct: number;
  x: number;
  y: number;
}

/** Parse `#rrggbb` into an `[r, g, b]` triple. */
function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/** Linearly interpolate two hex colors; `frac` is clamped to `[0, 1]`. Returns `#rrggbb`. */
function lerpColor(base: string, hot: string, frac: number): string {
  const f = Math.min(1, Math.max(0, frac));
  const [r1, g1, b1] = hexToRgb(base);
  const [r2, g2, b2] = hexToRgb(hot);
  const r = Math.round(r1 + (r2 - r1) * f);
  const g = Math.round(g1 + (g2 - g1) * f);
  const b = Math.round(b1 + (b2 - b1) * f);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

/**
 * Resolve an alpha-2 code to a viewer-localized country name, falling back to the raw code.
 * Mirrors `AttackerCountriesCard`'s `countryLabel`, minus the sentinels (which never reach the
 * map — `PRIVATE`/`UNKNOWN` have no geography).
 */
function regionName(alpha2: string, locale: string): string {
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(alpha2) ?? alpha2;
  } catch {
    return alpha2;
  }
}

/**
 * Interactive world choropleth of attacker countries, shaded by each country's share (%) of
 * attacks. Renders above the ranked list in `AttackerCountriesCard`; takes the already-fetched
 * `data` so it does not double-fetch. Sentinels (`PRIVATE`/`UNKNOWN`) have no geography and are
 * silently ignored here.
 */
export function AttackerCountriesMap({ data }: { data: CountryRow[] }) {
  const t = useT();
  const { locale } = useLocale();
  const tc = t.overview.attackerCountries;
  const [hover, setHover] = useState<HoverState | null>(null);

  // Index the data by alpha-2 for O(1) lookup per geography, and find the hottest share so the
  // ramp's domain is `[0, maxPct]`.
  const { byCode, maxPct } = useMemo(() => {
    const map = new Map<string, CountryRow>();
    let max = 0;
    for (const row of data) {
      if (row.code === "PRIVATE" || row.code === "UNKNOWN") continue;
      map.set(row.code, row);
      if (row.pct > max) max = row.pct;
    }
    return { byCode: map, maxPct: max };
  }, [data]);

  if (data.length === 0) {
    return (
      <Text c="dimmed" size="sm">
        {tc.empty}
      </Text>
    );
  }

  return (
    <Box pos="relative">
      <ComposableMap
        projection="geoEqualEarth"
        width={520}
        height={260}
        style={{ width: "100%", height: "auto" }}
      >
        <ZoomableGroup minZoom={1} maxZoom={6}>
          <Geographies geography={countries110m}>
            {({ geographies }: { geographies: RSMGeography[] }) =>
              geographies.map((geo) => {
                const alpha2 = numericMap[String(geo.id)];
                const row = alpha2 ? byCode.get(alpha2) : undefined;
                const fill = row
                  ? lerpColor(BASE_COLOR, HOT_COLOR, maxPct > 0 ? row.pct / maxPct : 0)
                  : BASE_COLOR;
                return (
                  <Geography
                    key={geo.rsmKey}
                    geography={geo}
                    fill={fill}
                    stroke={STROKE_COLOR}
                    strokeWidth={0.3}
                    style={{
                      default: { outline: "none" },
                      hover: { outline: "none", cursor: row ? "pointer" : "default" },
                      pressed: { outline: "none" },
                    }}
                    onMouseEnter={(event) => {
                      if (!row || !alpha2) return;
                      setHover({
                        alpha2,
                        count: row.count,
                        pct: row.pct,
                        x: event.clientX,
                        y: event.clientY,
                      });
                    }}
                    onMouseMove={(event) => {
                      if (!row || !alpha2) return;
                      setHover((h) => (h ? { ...h, x: event.clientX, y: event.clientY } : h));
                    }}
                    onMouseLeave={() => setHover(null)}
                  />
                );
              })
            }
          </Geographies>
        </ZoomableGroup>
      </ComposableMap>

      {hover && (
        <Box
          pos="fixed"
          left={hover.x + 12}
          top={hover.y + 12}
          px="xs"
          py={4}
          style={{
            zIndex: 400,
            pointerEvents: "none",
            background: "var(--mantine-color-dark-9)",
            border: "1px solid var(--mantine-color-dark-4)",
            borderRadius: "var(--mantine-radius-sm)",
          }}
        >
          <Text size="xs">
            {regionName(hover.alpha2, locale)} · {hover.count} · {Math.round(hover.pct)}%
          </Text>
        </Box>
      )}

      <Group justify="flex-end" gap="xs" mt="xs" wrap="nowrap">
        <Text size="xs" c="dimmed">
          0%
        </Text>
        <Box
          style={{
            width: 96,
            height: 8,
            borderRadius: 4,
            background: `linear-gradient(90deg, ${BASE_COLOR}, ${HOT_COLOR})`,
          }}
        />
        <Text size="xs" c="dimmed">
          {Math.round(maxPct)}%
        </Text>
      </Group>
    </Box>
  );
}
