import { describe, expect, it } from "vitest";

import {
  COMPATIBILITY_STATUS_LABELS,
  compatibilityStatusLabel,
  formatShare,
} from "@/lib/compatibility";
import type { CompatibilityStatus } from "@/lib/types";

describe("etykiety oceny relacji MPZP–POG", () => {
  it("obejmują pięć statusów i nie stwierdzają możliwości zabudowy", () => {
    expect(Object.keys(COMPATIBILITY_STATUS_LABELS).sort()).toEqual(
      ["compatible", "incompatible", "not_applicable", "uncertain", "unknown"],
    );
    for (const label of Object.values(COMPATIBILITY_STATUS_LABELS)) {
      expect(label.toLowerCase()).not.toMatch(/dopuszczal|można zabudować|zgodność potwierdzona/);
    }
  });

  it("nieznany status traktuje jak unknown", () => {
    expect(compatibilityStatusLabel("x" as CompatibilityStatus)).toBe(
      COMPATIBILITY_STATUS_LABELS.unknown,
    );
  });

  it("brak pola lub udziału to wartość nieustalona, nie zero", () => {
    expect(formatShare(null, null)).toBe("nieustalone");
    expect(formatShare(600, null)).toBe("nieustalone");
    expect(formatShare(0, 0)).toBe("0 m² (0.0%)");
  });
});
