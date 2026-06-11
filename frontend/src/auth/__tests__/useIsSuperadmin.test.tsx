import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthContext } from "../AuthProvider";
import { useIsSuperadmin } from "../useIsSuperadmin";

function wrap(is_superadmin: boolean) {
  return ({ children }: { children: ReactNode }) => (
    <AuthContext.Provider
      value={{
        me: { id: "1", email: "a@x.io", name: "A", is_superadmin },
        loading: false, refresh: vi.fn(), setMe: vi.fn(),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

describe("useIsSuperadmin", () => {
  it("is true for a superadmin", () => {
    expect(renderHook(() => useIsSuperadmin(), { wrapper: wrap(true) }).result.current).toBe(true);
  });
  it("is false otherwise", () => {
    expect(renderHook(() => useIsSuperadmin(), { wrapper: wrap(false) }).result.current).toBe(false);
  });
});
