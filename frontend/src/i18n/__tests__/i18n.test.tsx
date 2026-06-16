import { DirectionProvider, MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectionSync } from "../../components/DirectionSync";
import { LanguageSwitcher } from "../../components/LanguageSwitcher";
import { ar } from "../ar";
import { de } from "../de";
import { en } from "../en";
import { es } from "../es";
import { fr } from "../fr";
import { I18nProvider, type Locale, useLocale, useT } from "../index";
import { it as itDict } from "../it";
import { ja } from "../ja";
import { detectInitialLocale, SUPPORTED_LOCALES } from "../locale";
import { nl } from "../nl";
import { pt } from "../pt";
import { ru } from "../ru";
import { zh } from "../zh";
import { zhTW } from "../zhTW";

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
  const allDicts: Record<string, Tree> = {
    en, it: itDict, es, fr, de, pt, nl, ru, ar, zh, "zh-TW": zhTW, ja,
  };

  it("registers a dictionary for every supported locale", () => {
    expect(Object.keys(allDicts).sort()).toEqual([...SUPPORTED_LOCALES].sort());
  });

  for (const [name, dict] of Object.entries(allDicts)) {
    if (name === "en") continue;
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
    setBrowserLanguages(["xh-ZA"]);
    expect(detectInitialLocale()).toBe("en");
  });

  it("matches an exact regional tag (zh-TW)", () => {
    setBrowserLanguages(["zh-TW"]);
    expect(detectInitialLocale()).toBe("zh-TW");
  });

  it("maps generic/mainland Chinese to Simplified", () => {
    setBrowserLanguages(["zh-CN"]);
    expect(detectInitialLocale()).toBe("zh");
  });

  it("maps Traditional Chinese regions/scripts to zh-TW", () => {
    setBrowserLanguages(["zh-Hant-HK"]);
    expect(detectInitialLocale()).toBe("zh-TW");
  });

  it("detects Arabic", () => {
    setBrowserLanguages(["ar-EG"]);
    expect(detectInitialLocale()).toBe("ar");
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

  it("serves the dictionary for its locale prop", async () => {
    renderProbe("it");
    // Non-en dictionaries load on demand, so the Italian string arrives asynchronously.
    await waitFor(() => expect(screen.getByTestId("ov").textContent).toBe(itDict.nav.overview));
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
    // The French dictionary loads on demand after the switch.
    await waitFor(() => expect(screen.getByTestId("ov").textContent).toBe(fr.nav.overview));
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

describe("DirectionSync (RTL)", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("dir");
  });

  function renderWithDirection(locale: Locale) {
    return render(
      <I18nProvider locale={locale}>
        <DirectionProvider>
          <MantineProvider>
            <DirectionSync />
          </MantineProvider>
        </DirectionProvider>
      </I18nProvider>,
    );
  }

  it("sets <html dir> to rtl for Arabic", () => {
    renderWithDirection("ar");
    expect(document.documentElement.getAttribute("dir")).toBe("rtl");
  });

  it("sets <html dir> to ltr for a left-to-right locale", () => {
    renderWithDirection("ja");
    expect(document.documentElement.getAttribute("dir")).toBe("ltr");
  });
});
