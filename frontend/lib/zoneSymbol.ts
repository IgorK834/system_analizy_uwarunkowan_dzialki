/**
 * Walidacja ręcznie wpisanego symbolu strefy MPZP (BK-204).
 *
 * Reguły są lustrem `validate_zone_symbol_format` w backendzie (1–20 znaków po
 * obcięciu spacji, bez znaków kontrolnych i bez spacji wewnątrz, dozwolone
 * litery, cyfry oraz `. _ / -`). Wzorzec może przyjść z API
 * (`manual_zone_context.symbol_allowed_pattern`), więc UI i API nie rozjadą się
 * po zmianie reguły po stronie serwera.
 */

export const DEFAULT_ZONE_SYMBOL_MAX_LENGTH = 20;
export const DEFAULT_ZONE_SYMBOL_PATTERN = "^[A-Za-z0-9ĄąĆćĘęŁłŃńÓóŚśŹźŻż._/-]+$";

export type ZoneSymbolRules = {
  maxLength?: number;
  pattern?: string;
};

export type ZoneSymbolValidation =
  | { ok: true; value: string }
  | { ok: false; error: string };

function compilePattern(pattern: string | undefined): RegExp {
  try {
    return new RegExp(pattern ?? DEFAULT_ZONE_SYMBOL_PATTERN, "u");
  } catch {
    return new RegExp(DEFAULT_ZONE_SYMBOL_PATTERN, "u");
  }
}

export function validateZoneSymbol(
  raw: string,
  rules: ZoneSymbolRules = {},
): ZoneSymbolValidation {
  const maxLength = rules.maxLength ?? DEFAULT_ZONE_SYMBOL_MAX_LENGTH;
  const value = raw.trim();
  if (!value) return { ok: false, error: "Symbol strefy nie może być pusty." };
  if (value.length > maxLength) {
    return {
      ok: false,
      error: `Symbol strefy musi mieć od 1 do ${maxLength} znaków.`,
    };
  }
  if ([...value].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {
    return { ok: false, error: "Symbol strefy nie może zawierać znaków kontrolnych." };
  }
  if (!compilePattern(rules.pattern).test(value)) {
    return {
      ok: false,
      error:
        "Symbol strefy zawiera niedozwolone znaki (dozwolone są litery, cyfry oraz '.', '_', '/', '-').",
    };
  }
  return { ok: true, value };
}
