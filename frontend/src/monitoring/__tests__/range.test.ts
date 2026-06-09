import { describe, expect, it } from "vitest";
import { rangeToParams } from "../range";

describe("rangeToParams", () => {
  const now = new Date("2026-06-09T12:00:00.000Z");

  it("1h → finestra 1h, bucket 60s", () => {
    const p = rangeToParams("1h", now);
    expect(p.to).toBe("2026-06-09T12:00:00.000Z");
    expect(p.from).toBe("2026-06-09T11:00:00.000Z");
    expect(p.bucket).toBe(60);
  });

  it("24h → finestra 24h, bucket 300s", () => {
    const p = rangeToParams("24h", now);
    expect(p.from).toBe("2026-06-08T12:00:00.000Z");
    expect(p.bucket).toBe(300);
  });

  it("7d → finestra 7g, bucket 3600s", () => {
    const p = rangeToParams("7d", now);
    expect(p.from).toBe("2026-06-02T12:00:00.000Z");
    expect(p.bucket).toBe(3600);
  });
});
