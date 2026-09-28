import { describe, expect, it } from "vitest";

import { validateZoneSymbol } from "@/lib/zoneSymbol";

describe("validateZoneSymbol (lustro walidacji API)", () => {
  it.each(["230_U", "230_UMW", "MN.1", "1UZ", "6.8.MW/U", "2.UP", "ŁŻ-1"])(
    "akceptuje realny symbol %s",
    (symbol) => {
      expect(validateZoneSymbol(symbol)).toEqual({ ok: true, value: symbol });
    },
  );

  it("obcina spacje z brzegów tak jak API", () => {
    expect(validateZoneSymbol("  230_U ")).toEqual({ ok: true, value: "230_U" });
  });

  it.each([
    ["", "nie może być pusty"],
    ["   ", "nie może być pusty"],
    ["A".repeat(21), "od 1 do 20"],
    ["230\u0007U", "znaków kontrolnych"],
    ["230 U", "niedozwolone znaki"],
    ["<b>U</b>", "niedozwolone znaki"],
  ])("odrzuca %j", (symbol, message) => {
    const result = validateZoneSymbol(symbol);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain(message);
  });

  it("stosuje długość i wzorzec przekazane z API", () => {
    expect(validateZoneSymbol("ABCD", { maxLength: 3 }).ok).toBe(false);
    expect(validateZoneSymbol("MN", { pattern: "^[0-9]+$" }).ok).toBe(false);
  });

  it("wraca do domyślnego wzorca, gdy wzorzec z API jest niepoprawny", () => {
    expect(validateZoneSymbol("MN", { pattern: "[" }).ok).toBe(true);
  });
});
