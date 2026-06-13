import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageSwitcher } from "../../components/LanguageSwitcher";
import { de } from "../de";
import { en } from "../en";
import { es } from "../es";
import { fr } from "../fr";
import { I18nProvider, type Locale, useLocale, useT } from "../index";
import { it as itDict } from "../it";
import { detectInitialLocale, SUPPORTED_LOCALES } from "../locale";
import { nl } from "../nl";
import { pt } from "../pt";

type Tree = Record<string, unknown>;

// The test environment does not provide a Web Storage implementation, so back it with a
// simple in-memory stub for the persistence assertions.
function makeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      store = {};
    },
    key: (i) => Object.keys(store)[i] ?? null,
    get length() {
      return Object.keys(store).length;
    },
  } as Storage;
}

beforeEach(() => {
  vi.stubGlobal("localStorage", makeStorage());
});

/** Flatten a nested dictionary into dotted leaf-key paths. */
function flatten(obj: Tree, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object"
      ? flatten(v as Tree, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  );
}

describe("dictionary key parity", () => {
  const enKeys = flatten(en).sort();
  const others: Record<string, Tree> = { it: itDict, es, fr, de, pt, nl };

  it("registers a dictionary for every supported locale", () => {
    expect([...SUPPORTED_LOCALES].sort()).toEqual(
      ["de", "en", "es", "fr", "it", "nl", "pt"],
    );
  });

  for (const [name, dict] of Object.entries(others)) {
    it(`${name} has exactly the same keys as en`, () => {
      expect(flatten(dict).sort()).toEqual(enKeys);
    });
  }
});

describe("detectInitialLocale", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  function setBrowserLanguages(langs: string[]) {
    Object.defineProperty(window.navigator, "languages", {
      value: langs,
      configurable: true,
    });
    Object.defineProperty(window.navigator, "language", {
      value: langs[0],
      configurable: true,
    });
  }

  it("prefers a supported persisted choice", () => {
    localStorage.setItem("opngms.locale", "fr");
    setBrowserLanguages(["de-DE"]);
    expect(detectInitialLocale()).toBe("fr");
  });

  it("ignores an unsupported persisted value and uses the browser language", () => {
    localStorage.setItem("opngms.locale", "xx");
    setBrowserLanguages(["de-DE", "en-US"]);
    expect(detectInitialLocale()).toBe("de");
  });

  it("falls back to en for an unsupported browser language", () => {
    setBrowserLanguages(["zh-CN"]);
    expect(detectInitialLocale()).toBe("en");
  });
});

describe("I18nProvider", () => {
  function Probe() {
    const t = useT();
    const { locale, setLocale } = useLocale();
    return (
      <div>
        <span data-testid="ov">{t.nav.overview}</span>
        <span data-testid="loc">{locale}</span>
        <button type="button" onClick={() => setLocale("fr")}>switch</button>
      </div>
    );
  }

  function renderProbe(locale?: Locale) {
    return render(
      <I18nProvider locale={locale}>
        <MantineProvider>
          <Probe />
        </MantineProvider>
      </I18nProvider>,
    );
  }

  it("serves the dictionary for its locale prop", () => {
    renderProbe("it");
    expect(screen.getByTestId("ov").textContent).toBe(itDict.nav.overview);
    expect(screen.getByTestId("ov").textContent).not.toBe(en.nav.overview);
  });

  it("setLocale switches the active dictionary and persists the choice", async () => {
    Object.defineProperty(window.navigator, "languages", {
      value: ["en-US"],
      configurable: true,
    });
    Object.defineProperty(window.navigator, "language", {
      value: "en-US",
      configurable: true,
    });
    renderProbe(); // no override → self-detect → en
    expect(screen.getByTestId("loc").textContent).toBe("en");
    expect(screen.getByTestId("ov").textContent).toBe(en.nav.overview);

    await userEvent.click(screen.getByText("switch"));

    expect(screen.getByTestId("loc").textContent).toBe("fr");
    expect(screen.getByTestId("ov").textContent).toBe(fr.nav.overview);
    expect(localStorage.getItem("opngms.locale")).toBe("fr");
  });
});

describe("LanguageSwitcher", () => {
  it("shows the active locale's native label", () => {
    render(
      <I18nProvider locale="de">
        <MantineProvider>
          <LanguageSwitcher />
        </MantineProvider>
      </I18nProvider>,
    );
    expect(screen.getByRole("combobox")).toHaveValue("Deutsch");
  });
});
