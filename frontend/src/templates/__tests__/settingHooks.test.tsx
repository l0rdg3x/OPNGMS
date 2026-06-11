import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { useSettingEndpoints, useIntrospectSetting, useTenantDevices } from "../settingHooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <TenantContext.Provider
        value={{
          tenants: [{ id: "t1", name: "Tenant A", slug: "a", role: "tenant_admin" }],
          activeId: "t1",
          setActiveId: () => {},
          loading: false,
        }}
      >
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </TenantContext.Provider>
    </I18nProvider>
  );
}

const ENDPOINTS = [
  { key: "ids_general", label: "IDS — General settings" },
  { key: "fw_rules", label: "Firewall rules" },
];

const DEVICES = [
  { id: "d1", name: "fw1", base_url: "https://fw1.local", status: "reachable", firmware_version: "24.1" },
];

const INTROSPECT_RESULT = {
  endpoint_key: "ids_general",
  label: "IDS — General settings",
  fields: [
    { path: "general.enabled", label: "Enabled", control: "switch", value: "0" },
    {
      path: "general.mode",
      label: "Mode",
      control: "select",
      options: [
        { value: "pcap", label: "PCAP" },
        { value: "netmap", label: "Netmap" },
      ],
      value: "pcap",
    },
  ],
};

describe("settingHooks", () => {
  it("useSettingEndpoints GETs /api/opnsense/setting-endpoints", async () => {
    server.use(http.get("/api/opnsense/setting-endpoints", () => HttpResponse.json(ENDPOINTS)));
    const { result } = renderHook(() => useSettingEndpoints(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data?.[0].key).toBe("ids_general");
  });

  it("useTenantDevices GETs /api/tenants/t1/devices", async () => {
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
    );
    const { result } = renderHook(() => useTenantDevices(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0]?.id).toBe("d1");
  });

  it("useIntrospectSetting('d1').mutateAsync('ids_general') GETs the introspect endpoint and returns {fields,label}", async () => {
    server.use(
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/settings/ids_general",
        () => HttpResponse.json(INTROSPECT_RESULT),
      ),
    );
    const { result } = renderHook(() => useIntrospectSetting("d1"), { wrapper });
    const data = await result.current.mutateAsync("ids_general");
    expect(data.label).toBe("IDS — General settings");
    expect(data.fields).toHaveLength(2);
    expect(data.fields[0].path).toBe("general.enabled");
    expect(data.fields[0].control).toBe("switch");
  });
});
