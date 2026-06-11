import { createTheme, type MantineColorsTuple } from "@mantine/core";

// "Midnight NOC" — a dark operations-console aesthetic for a multi-tenant firewall fleet.
// Signal-teal accent, instrument-grade typography (IBM Plex), monospace for technical data.

const signal: MantineColorsTuple = [
  "#dbfaf3", "#b2f1e5", "#85e7d4", "#54ddc2", "#30d0b2",
  "#19c0a3", "#10a78c", "#0b8572", "#076357", "#03443d",
];

// Custom dark canvas: desaturated navy/charcoal. Index 7 is the body background,
// 6 the panel/card surface, 4 the borders, 0–2 the text ramp.
const dark: MantineColorsTuple = [
  "#e7eef4", "#c7d3df", "#9fb1c1", "#6f8395", "#3a4a59",
  "#283544", "#141c25", "#0e151d", "#0a1016", "#05090d",
];

// Warm amber for warnings (kept distinct from the teal primary).
const amber: MantineColorsTuple = [
  "#fff4e0", "#ffe3b3", "#ffd083", "#ffbc52", "#ffab2e",
  "#f59518", "#d4790f", "#a85b0a", "#7d4207", "#522a03",
];

export const theme = createTheme({
  primaryColor: "signal",
  primaryShade: { light: 6, dark: 4 },
  colors: { signal, dark, amber },
  defaultRadius: "md",
  fontFamily: "'IBM Plex Sans', system-ui, -apple-system, sans-serif",
  fontFamilyMonospace: "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace",
  headings: {
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
    fontWeight: "650",
    sizes: {
      h1: { fontWeight: "700", lineHeight: "1.15" },
      h2: { fontWeight: "700", lineHeight: "1.2" },
      h3: { fontWeight: "650", lineHeight: "1.25" },
      h4: { fontWeight: "600" },
    },
  },
  defaultGradient: { from: "signal.5", to: "signal.7", deg: 135 },
  other: {
    accent: "#30d0b2",
    canvas: "#0e151d",
    panel: "#141c25",
    border: "#283544",
  },
});
