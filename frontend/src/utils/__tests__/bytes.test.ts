import { describe, expect, it } from "vitest";
import { humanBytes } from "../bytes";

describe("humanBytes", () => {
  it("keeps bytes integer-formatted under 1 KB", () => {
    expect(humanBytes(0)).toBe("0 B");
    expect(humanBytes(512)).toBe("512 B");
  });

  it("scales to dynamic binary units with one decimal", () => {
    expect(humanBytes(1536)).toBe("1.5 KB");
    expect(humanBytes(1024 * 1024)).toBe("1.0 MB");
    expect(humanBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(humanBytes(3 * 1024 ** 4)).toBe("3.0 TB");
  });

  it("handles negatives and non-finite input", () => {
    expect(humanBytes(-2048)).toBe("-2.0 KB");
    expect(humanBytes(Number.NaN)).toBe("—");
    expect(humanBytes(Number.POSITIVE_INFINITY)).toBe("—");
  });
});
