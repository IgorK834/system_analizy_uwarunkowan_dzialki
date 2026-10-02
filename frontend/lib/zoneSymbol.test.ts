import { describe, expect, it } from "vitest";

import rules from "../../shared/zone-symbol-rules.json";
import {
  DEFAULT_ZONE_SYMBOL_MAX_LENGTH,
  DEFAULT_ZONE_SYMBOL_PATTERN,
  ZONE_SYMBOL_MAX_RAW_LENGTH,
  ZONE_SYMBOL_RULES_VERSION,
  canonicalizeZoneSymbol,
  sameZoneSymbol,
  validateZoneSymbol,
  zoneSymbolKey,
} from "@/lib/zoneSymbol";

type Case = { input: string; valid: boolean; canonical?: string; code?: string };

describe("reguły symbolu strefy — wspólne z backendem", () => {
  it("używają jednego pliku reguł i jego wzorca", () => {
    expect(DEFAULT_ZONE_SYMBOL_PATTERN).toBe(rules.pattern);
    expect(DEFAULT_ZONE_SYMBOL_MAX_LENGTH).toBe(rules.max_length);
    expect(ZONE_SYMBOL_MAX_RAW_LENGTH).toBe(rules.max_raw_length);
    expect(ZONE_SYMBOL_RULES_VERSION).toBe(rules.rules_version);
  });

  // Te same przypadki sprawdza test backendu (`test_zone_symbol.py`): rozjazd
  // kanonizacji albo walidacji między Pythonem a TypeScriptem łamie jeden z nich.
  it.each(rules.cases as Case[])("przypadek zgodności %j", (item) => {
    const result = validateZoneSymbol(item.input);
    if (item.valid) {
      expect(result).toEqual({ ok: true, value: item.canonical });
      expect(canonicalizeZoneSymbol(item.input)).toBe(item.canonical);
    } else {
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.code).toBe(item.code);
    }
  });
});

describe("validateZoneSymbol", () => {
  it.each(["230_U", "230_UMW", "MN.1", "1UZ", "6.8.MW/U", "2.UP", "ŁŻ-1"])(
    "akceptuje realny symbol %s",
    (symbol) => {
      expect(validateZoneSymbol(symbol)).toEqual({ ok: true, value: symbol });
    },
  );

  it.each(["22 KD G1/2(Z1/4)", "6KD L", "7 UC,U,M", "KL 1/2", "146 MN", "1 PK", "1.4U,MN"])(
    "akceptuje symbol ze spacją, przecinkiem lub nawiasem: %s",
    (symbol) => {
      expect(validateZoneSymbol(symbol)).toEqual({ ok: true, value: symbol });
    },
  );

  it("sprowadza wpis do formy kanonicznej", () => {
    expect(validateZoneSymbol("  230_U ")).toEqual({ ok: true, value: "230_U" });
    expect(validateZoneSymbol("146   MN")).toEqual({ ok: true, value: "146 MN" });
    expect(validateZoneSymbol("146 MN")).toEqual({ ok: true, value: "146 MN" });
  });

  it.each([
    ["", "nie może być pusty"],
    ["   ", "nie może być pusty"],
    ["A".repeat(41), "od 1 do 40"],
    ["230\u0007U", "znaków kontrolnych"],
    ["230\nU", "znaków kontrolnych"],
    ["230;U", "niedozwolone znaki"],
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

describe("porównanie symboli", () => {
  it("pomija odstępy i wielkość liter, ale nie scala symboli podobnych", () => {
    expect(zoneSymbolKey(" 146  mn ")).toBe("146mn");
    expect(sameZoneSymbol("146 MN", "146MN")).toBe(true);
    expect(sameZoneSymbol("MN", "MN.1")).toBe(false);
    expect(sameZoneSymbol("MN", "146 MN")).toBe(false);
  });
});
