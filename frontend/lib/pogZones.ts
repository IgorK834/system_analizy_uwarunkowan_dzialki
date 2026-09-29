/**
 * Typowany adapter stref POG nad wspólnym artefaktem prezentacji (BK-403).
 *
 * Jedynym źródłem kodów stref, polskich etykiet, kolejności, palety, wzorów
 * OUZ/OZS/OSDIS i stylów statusu prawnego jest `shared/pog-presentation.json`
 * — ten sam plik czyta backend (raport PDF, snapshot stylu analizy). Moduł nie
 * definiuje żadnego koloru samodzielnie; waliduje artefakt przy imporcie, więc
 * uszkodzony config zatrzymuje build i testy zamiast rysować błędną mapę.
 */
import rawPresentation from "../../shared/pog-presentation.json";

import type { PogLegalStatus } from "@/lib/types";

export type PogPatternId =
  | "diagonal-hatch"
  | "cross-hatch"
  | "diagonal-lines"
  | "dots"
  | "cross-lines"
  | "horizontal-lines";

export type PogZoneStyle = {
  code: string;
  codelist_id: string;
  label: string;
  order: number;
  fill: string;
  outline: string;
};

export type PogPatternStyle = {
  code?: string;
  label: string;
  fill: string;
  outline: string;
  pattern: PogPatternId;
  description: string;
};

export type PogOverlayId =
  | "ouz"
  | "downtown"
  | "social_infrastructure_standard"
  | "act_boundary";

export type PogOverlayStyle = {
  id: PogOverlayId;
  short_label: string;
  label: string;
  outline: string;
  line_width: number;
  line_dasharray: number[] | null;
  pattern: PogPatternId | null;
  description: string;
};

export type PogLegalStatusStyle = {
  status: PogLegalStatus;
  label: string;
  fill_opacity: number;
  line_dasharray: number[] | null;
  /** BK-406: wzór danych niewiążących — informacja nie zależy tylko od barwy. */
  pattern: PogPatternId | null;
  /** Stały tekst plakietki (np. „projekt / dane niewiążące”). */
  badge: string | null;
  description: string;
};

export type PogThemeClassConfig = {
  min: number;
  max: number | null;
  color: string;
  label: string;
};

export type PogThemeConfig = {
  id: string;
  label: string;
  kind: "categorical" | "numeric";
  property: string;
  unit: string | null;
  unit_label: string;
  direction: "categorical" | "ascending" | "descending";
  scale_description: string;
  interval_closure?: "left";
  classes?: PogThemeClassConfig[];
};

export type PogPresentationConfig = {
  schema: string;
  style_version: string;
  description: string;
  zone_dictionary: {
    codelist: string;
    url: string;
    legal_basis: string;
    verified_at: string;
    source_last_modified: string;
    sha256: string;
    fixture: string;
  };
  zones: PogZoneStyle[];
  unknown_zone: PogPatternStyle;
  null_style: PogPatternStyle;
  themes: PogThemeConfig[];
  overlays: PogOverlayStyle[];
  legal_statuses: PogLegalStatusStyle[];
};

const HEX_COLOR = /^#[0-9a-f]{6}$/;

/** Walidacja kontraktu artefaktu — te same reguły co `app/core/pog_presentation.py`. */
export function validatePresentation(config: PogPresentationConfig): PogPresentationConfig {
  const errors: string[] = [];
  if (config.schema !== "pog-presentation/1") errors.push(`schemat ${config.schema}`);
  const codes = config.zones.map((zone) => zone.code);
  if (new Set(codes).size !== codes.length) errors.push("zduplikowane kody stref");
  const orders = config.zones.map((zone) => zone.order).sort((a, b) => a - b);
  if (orders.some((order, index) => order !== index + 1)) {
    errors.push("kolejność stref nie jest permutacją 1..n");
  }
  const colors = [
    ...config.zones.flatMap((zone) => [zone.fill, zone.outline]),
    config.unknown_zone.fill,
    config.null_style.fill,
    ...config.overlays.map((overlay) => overlay.outline),
    ...config.themes.flatMap((theme) => (theme.classes ?? []).map((item) => item.color)),
  ];
  for (const color of colors) {
    if (!HEX_COLOR.test(color)) errors.push(`kolor ${color}`);
  }
  for (const theme of config.themes) {
    if (theme.kind === "categorical") continue;
    const classes = theme.classes ?? [];
    if (theme.interval_closure !== "left" || classes.length === 0) {
      errors.push(`temat ${theme.id} bez klas z domknięciem left`);
      continue;
    }
    if (classes[0].min !== 0) errors.push(`temat ${theme.id} nie zaczyna się od 0`);
    classes.slice(1).forEach((item, index) => {
      if (classes[index].max !== item.min) errors.push(`temat ${theme.id} ma lukę`);
    });
    if (classes[classes.length - 1].max !== null) {
      errors.push(`temat ${theme.id} ma zamkniętą ostatnią klasę`);
    }
  }
  for (const status of config.legal_statuses) {
    const nonBinding = status.status === "project" || status.status === "in_progress";
    if (nonBinding && !(status.pattern && status.badge)) {
      errors.push(`status ${status.status} bez wzoru i plakietki`);
    }
    if (status.status === "binding" && (status.pattern || status.badge)) {
      errors.push("akt obowiązujący ze wzorem lub plakietką projektu");
    }
  }
  if (errors.length > 0) {
    throw new Error(`Niepoprawny artefakt prezentacji POG: ${errors.join("; ")}.`);
  }
  return config;
}

export const POG_PRESENTATION: PogPresentationConfig = validatePresentation(
  rawPresentation as PogPresentationConfig,
);

export const POG_STYLE_VERSION = POG_PRESENTATION.style_version;
export const POG_ZONE_DICTIONARY = POG_PRESENTATION.zone_dictionary;

/** Strefy w ustawowej kolejności (art. 13c ust. 2 pkt 1–13). */
export const POG_ZONES: readonly PogZoneStyle[] = [...POG_PRESENTATION.zones].sort(
  (a, b) => a.order - b.order,
);
export const POG_ZONE_CODES: readonly string[] = POG_ZONES.map((zone) => zone.code);
export const POG_UNKNOWN_ZONE: PogPatternStyle = POG_PRESENTATION.unknown_zone;
export const POG_NULL_STYLE: PogPatternStyle = POG_PRESENTATION.null_style;
export const POG_OVERLAYS: readonly PogOverlayStyle[] = POG_PRESENTATION.overlays;
export const POG_LEGAL_STATUS_STYLES: readonly PogLegalStatusStyle[] =
  POG_PRESENTATION.legal_statuses;

const ZONES_BY_CODE = new Map(POG_ZONES.map((zone) => [zone.code, zone]));

export function isKnownZoneCode(code: string | null | undefined): boolean {
  return Boolean(code && ZONES_BY_CODE.has(code));
}

/** Styl strefy; kod spoza słownika dostaje jawny styl „nierozpoznana”. */
export function zoneStyle(code: string | null | undefined): PogZoneStyle | PogPatternStyle {
  return (code && ZONES_BY_CODE.get(code)) || POG_UNKNOWN_ZONE;
}

export function zoneLabel(code: string | null | undefined): string {
  const zone = code ? ZONES_BY_CODE.get(code) : undefined;
  return zone ? `${zone.code} — ${zone.label}` : POG_UNKNOWN_ZONE.label;
}

export function overlayStyle(id: PogOverlayId): PogOverlayStyle {
  const overlay = POG_OVERLAYS.find((item) => item.id === id);
  if (!overlay) throw new Error(`Brak stylu nakładki POG ${id}.`);
  return overlay;
}

export function legalStatusStyle(status: string | null | undefined): PogLegalStatusStyle {
  return (
    POG_LEGAL_STATUS_STYLES.find((item) => item.status === status) ??
    (POG_LEGAL_STATUS_STYLES.find((item) => item.status === "unknown") as PogLegalStatusStyle)
  );
}

/** Wyrażenie MapLibre koloru strefy (`match` po `zone_code`, fallback nierozpoznana). */
export function zoneFillColorExpression(): unknown[] {
  return [
    "match",
    ["get", "zone_code"],
    ...POG_ZONES.flatMap((zone) => [zone.code, zone.fill]),
    POG_UNKNOWN_ZONE.fill,
  ];
}

/** Krycie wypełnienia zależne od statusu prawnego aktu (projekt słabiej niż akt wiążący). */
export function legalStatusOpacityExpression(): unknown[] {
  const unknown = legalStatusStyle("unknown");
  return [
    "match",
    ["get", "legal_status"],
    ...POG_LEGAL_STATUS_STYLES.filter((item) => item.status !== "unknown").flatMap(
      (item) => [item.status, item.fill_opacity],
    ),
    unknown.fill_opacity,
  ];
}

/** Statusy prawne rysowane dodatkowym wzorem (projekt, akt w trakcie). */
export const POG_PATTERNED_LEGAL_STATUSES: readonly PogLegalStatus[] = POG_LEGAL_STATUS_STYLES.filter(
  (item) => item.pattern !== null,
).map((item) => item.status);

/** Wzór danych niewiążących (jeden dla wszystkich statusów z wzorem). */
export function legalStatusPattern(): { pattern: PogPatternId; outline: string } {
  const style = POG_LEGAL_STATUS_STYLES.find((item) => item.pattern !== null);
  if (!style?.pattern) throw new Error("Artefakt POG nie definiuje wzoru danych niewiążących.");
  return { pattern: style.pattern, outline: "#3d3d3d" };
}

/** Stała plakietka projektu: „projekt / dane niewiążące” z artefaktu. */
export const POG_NON_BINDING_BADGE: string = legalStatusStyle("project").badge ?? "";

/**
 * Plakietki statusów obecnych w wydaniu (projekt, nieustalony, nieaktualny).
 * Akt obowiązujący nie ma plakietki — ma osobny, czytelny opis w legendzie.
 */
export function releaseStatusBadges(
  actsByLegalStatus: Partial<Record<PogLegalStatus, number>>,
): Array<{ status: PogLegalStatus; badge: string }> {
  const seen = new Set<string>();
  const badges: Array<{ status: PogLegalStatus; badge: string }> = [];
  for (const style of POG_LEGAL_STATUS_STYLES) {
    if (!style.badge || !(actsByLegalStatus[style.status] ?? 0)) continue;
    if (seen.has(style.badge)) continue;
    seen.add(style.badge);
    badges.push({ status: style.status, badge: style.badge });
  }
  return badges;
}
