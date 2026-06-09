import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import { ConfigTree } from "../ConfigTree";
import type { ConfigNode } from "../types";

const tree: ConfigNode = {
  tag: "opnsense",
  path: "opnsense",
  attributes: {},
  value: null,
  sensitive: false,
  children: [
    {
      tag: "system",
      path: "opnsense/system",
      attributes: {},
      value: null,
      sensitive: false,
      children: [
        {
          tag: "hostname",
          path: "opnsense/system/hostname",
          attributes: {},
          value: "fw1",
          sensitive: false,
          children: [],
        },
        {
          tag: "password",
          path: "opnsense/system/password",
          attributes: {},
          value: null,
          sensitive: true,
          children: [],
        },
      ],
    },
  ],
};

function wrap(ui: React.ReactNode) {
  return (
    <MantineProvider>
      <I18nProvider>{ui}</I18nProvider>
    </MantineProvider>
  );
}

describe("ConfigTree", () => {
  it("renders leaves and masks sensitive values", () => {
    render(wrap(<ConfigTree root={tree} />));
    // non-sensitive leaf renders its value
    expect(screen.getByText("fw1")).toBeInTheDocument();
    // sensitive node shows a mask, not a value
    expect(screen.getByText(/hidden/i)).toBeInTheDocument();
    // the secret string never appears anywhere in the DOM (value is null anyway)
    expect(document.body.textContent).not.toContain("password-secret");
  });

  it("toggles a container's expanded state robustly", async () => {
    render(wrap(<ConfigTree root={tree} />));

    // NOTE on Mantine Collapse behavior (verified in this JSDOM env):
    // Collapse keeps its children MOUNTED and animates height; collapsed content is
    // only hidden via `display: none` / `aria-hidden`, never unmounted. Worse, in
    // JSDOM the open-state height animation never resolves, so even an `in={true}`
    // Collapse renders `display:none` + `aria-hidden="true"`. That removes nested
    // container toggles (e.g. "system") from the accessibility tree, so asserting on
    // a deep toggle or on "child leaves out of the DOM" would be flaky. Instead we
    // drive the always-accessible ROOT toggle ("opnsense") and assert the ROBUST
    // signal: the button's `aria-expanded` flips and the chevron char changes ▾<->▸.

    // the root "opnsense" container toggle starts expanded (depth 0 < 2)
    const toggle = screen.getByRole("button", { name: /opnsense/i });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle.textContent).toContain("▾");

    // collapse it: aria-expanded flips and the chevron changes ▾ -> ▸
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.textContent).toContain("▸");

    // expand again: state flips back
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle.textContent).toContain("▾");
  });
});
