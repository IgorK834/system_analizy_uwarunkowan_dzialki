/**
 * Symbol strefy MPZP w formularzu ręcznego wskazania (BK-204, PV3-04).
 *
 * Reguły (forma kanoniczna, długość, wzorzec) pochodzą z `shared/zone-symbol-rules.json` —
 * tego samego pliku, który zawiera przypadki zgodności sprawdzane testem backendu
 * (`app.shared.zone_symbol`) i testem UI, więc API i formularz nie mogą się rozjechać.
 * Wzorzec i limit mogą dodatkowo przyjść z API (`manual_zone_context`).
 *
 * Forma kanoniczna: NFKC, przycięcie odstępów brzegowych, odrzucenie znaków
 * kontrolnych, zwinięcie białych znaków do jednej spacji. Dozwolone litery, cyfry,
 * `. _ / - , + ( )` i pojedyncze spacje wewnętrzne, do 40 znaków.
 */

import rawRules from "../../shared/zone-symbol-rules.json";

export const ZONE_SYMBOL_RULES_VERSION: string = rawRules.rules_version;
export const DEFAULT_ZONE_SYMBOL_MAX_LENGTH: number = rawRules.max_length;
export const ZONE_SYMBOL_MAX_RAW_LENGTH: number = rawRules.max_raw_length;
export const DEFAULT_ZONE_SYMBOL_PATTERN: string = rawRules.pattern;

export type ZoneSymbolRules = {
  maxLength?: number;
  pattern?: string;
};

export type ZoneSymbolErrorCode =
  | "empty"
  | "too_long"
  | "control_characters"
  | "disallowed_characters";

export type ZoneSymbolValidation =
  | { ok: true; value: string }
  | { ok: false; error: string; code: ZoneSymbolErrorCode };

// Jawny, przenośny zestaw odstępów (identyczny jak w backendzie): po NFKC zwykłe i
// twarde spacje są już spacją; nie używamy `trim()`/`\s`, bo różnią się od Pythona.
const EDGE_WHITESPACE = new Set([" ", "\t", "\n", "\v", "\f", "\r", "\u1680", "\u2028", "\u2029"]);
const WHITESPACE_RUN = /[ \t\n\v\f\r\u1680\u2028\u2029]+/g;

function isControl(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return code <= 0x1f || (code >= 0x7f && code <= 0x9f);
}

function trimEdges(value: string): string {
  const chars = [...value];
  let start = 0;
  let end = chars.length;
  while (start < end && EDGE_WHITESPACE.has(chars[start])) start += 1;
  while (end > start && EDGE_WHITESPACE.has(chars[end - 1])) end -= 1;
  return chars.slice(start, end).join("");
}

function compilePattern(pattern: string | undefined): RegExp {
  try {
    return new RegExp(pattern ?? DEFAULT_ZONE_SYMBOL_PATTERN, "u");
  } catch {
    return new RegExp(DEFAULT_ZONE_SYMBOL_PATTERN, "u");
  }
}

/** NFKC, przycięcie i zwinięcie białych znaków do jednej spacji (bez walidacji). */
export function canonicalizeZoneSymbol(raw: string): string {
  return trimEdges(raw.normalize("NFKC")).replace(WHITESPACE_RUN, " ");
}

/** Klucz porównania niewrażliwy na odstępy i wielkość liter (`146 MN` = `146mn`). */
export function zoneSymbolKey(symbol: string): string {
  return canonicalizeZoneSymbol(symbol).replace(WHITESPACE_RUN, "").toLowerCase();
}

export function sameZoneSymbol(left: string, right: string): boolean {
  return zoneSymbolKey(left) === zoneSymbolKey(right);
}

export function validateZoneSymbol(
  raw: string,
  rules: ZoneSymbolRules = {},
): ZoneSymbolValidation {
  const maxLength = rules.maxLength ?? DEFAULT_ZONE_SYMBOL_MAX_LENGTH;
  const normalized = trimEdges(raw.normalize("NFKC"));
  if (!normalized) {
    return { ok: false, error: "Symbol strefy nie może być pusty.", code: "empty" };
  }
  if ([...normalized].some(isControl)) {
    return {
      ok: false,
      error: "Symbol strefy nie może zawierać znaków kontrolnych.",
      code: "control_characters",
    };
  }
  const value = normalized.replace(WHITESPACE_RUN, " ");
  if ([...value].length > maxLength) {
    return {
      ok: false,
      error: `Symbol strefy musi mieć od 1 do ${maxLength} znaków.`,
      code: "too_long",
    };
  }
  if (!compilePattern(rules.pattern).test(value)) {
    return {
      ok: false,
      error:
        "Symbol strefy zawiera niedozwolone znaki (dozwolone są litery, cyfry, '.', '_', '/', '-', ',', '+', '(', ')' oraz pojedyncze spacje).",
      code: "disallowed_characters",
    };
  }
  return { ok: true, value };
}
