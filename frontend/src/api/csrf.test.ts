import { describe, expect, it, beforeEach } from "vitest";
import { csrfToken } from "./csrf";

describe("csrfToken", () => {
  beforeEach(() => {
    document.cookie = "opngms_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });
  it("reads the opngms_csrf cookie", () => {
    document.cookie = "opngms_csrf=abc123";
    expect(csrfToken()).toBe("abc123");
  });
  it("returns empty string when absent", () => {
    expect(csrfToken()).toBe("");
  });
});
