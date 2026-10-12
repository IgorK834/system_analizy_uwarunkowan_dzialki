import { verifiedHttpsHref } from "@/lib/safeLink";
import type {
  MpzpActLegalStatus,
  MpzpActLinkField,
  MpzpActPrecedence,
  MpzpActResult,
  MpzpChannelStatusValue,
  MpzpRejectedActReason,
  MpzpStatus,
  MpzpStatusValue,
} from "@/lib/types";

/**
 * Prezentacja aktów MPZP z Rejestru Urbanistycznego i KIMPZP oraz rozłącznego stanu ustalenia MPZP
 * (AU-101, AU-103). Etykieta i komunikat stanu pochodzą z backendu (ten sam tekst w UI, PDF i paczce
 * audytowej); tu są tylko zasady prezentacji. Zapis sprzed AU-103 nie ma stanu i dostaje osobny
 * komunikat — nigdy „nie znaleziono planu”.
 */
export const MPZP_STATUS_MISSING_MESSAGE =
  "Ten zapis analizy powstał przed wprowadzeniem stanów MPZP — nie ustalono, czy plan obowiązuje. Odśwież analizę, aby uzyskać stan.";

export const MPZP_STATUS_MISSING_LABEL = "Brak stanu w zapisie";

/** Ton wizualny stanu; znaczenie niesie też tekst i znak, nie sam kolor. */
export type MpzpStatusTone = "ok" | "warn" | "info" | "bad" | "muted";

export const MPZP_STATUS_TONES: Record<MpzpStatusValue, MpzpStatusTone> = {
  available: "ok",
  ambiguous: "warn",
  no_match: "muted",
  no_coverage: "info",
  unavailable: "bad",
  unknown: "muted",
};

export const MPZP_STATUS_MARKS: Record<MpzpStatusValue, string> = {
  available: "✓",
  ambiguous: "⚠",
  no_match: "∅",
  no_coverage: "∅",
  unavailable: "✕",
  unknown: "?",
};

const CHANNEL_STATUS_LABELS: Record<MpzpChannelStatusValue, string> = {
  available: "wskazał akt",
  no_match: "nie wskazał aktu",
  no_coverage: "brak usługi dla obszaru",
  unavailable: "błąd lub brak odpowiedzi",
  unknown: "odpowiedź nierozpoznana",
};

const LEGAL_STATUS_LABELS: Record<MpzpActLegalStatus, string> = {
  binding: "obowiązujący",
  not_binding: "utracił moc / nieobowiązujący",
  not_yet_binding: "projekt lub jeszcze nieobowiązujący",
  unknown: "status nieustalony",
};

const PRECEDENCE_LABELS: Record<MpzpActPrecedence, string> = {
  applies: "obowiązuje na działce",
  complementary: "obejmuje inną część działki niż pozostałe akty",
  superseded: "wyparty przez inny akt",
  unresolved: "kilka aktów bez rozstrzygnięcia",
  not_in_force: "nie obowiązuje",
};

const DOCUMENT_KIND_LABELS: Record<NonNullable<MpzpActResult["document_kind"]>, string> = {
  adopting: "Tekst uchwały",
  commencement: "Uchwała o przystąpieniu (nie tekst planu)",
  other: "Dokument aktu",
};

const LINK_LABELS: Record<MpzpActLinkField, string> = {
  document_url: "Dokument",
  legend_url: "Legenda",
  drawing_url: "Rysunek planu",
};

const LINK_ORDER: MpzpActLinkField[] = ["document_url", "legend_url", "drawing_url"];

const REJECTION_LABELS: Record<MpzpRejectedActReason, string> = {
  no_overlap: "granica aktu leży poza działką",
  boundary_touch_only: "akt tylko styka się z działką",
  below_min_area: "przecięcie poniżej progu (szum granicy)",
  geometry_unusable: "brak użytecznej granicy aktu",
};

const AGREEMENT_LABELS: Record<MpzpStatus["channel_agreement"], string> = {
  agree: "Kanały wskazują te same akty.",
  mismatch: "Kanały wskazują różne akty (MPZP_CHANNEL_MISMATCH) — rozstrzygnij w uchwałach.",
  single_channel: "Akt wskazał tylko jeden kanał.",
  none: "Żaden kanał nie wskazał aktu.",
};

export function channelStatusLabel(status: MpzpChannelStatusValue): string {
  return CHANNEL_STATUS_LABELS[status] ?? CHANNEL_STATUS_LABELS.unknown;
}

export function legalStatusLabel(status: MpzpActLegalStatus): string {
  return LEGAL_STATUS_LABELS[status] ?? LEGAL_STATUS_LABELS.unknown;
}

export function precedenceLabel(precedence: MpzpActPrecedence): string {
  return PRECEDENCE_LABELS[precedence] ?? precedence;
}

export function rejectionLabel(reason: MpzpRejectedActReason): string {
  return REJECTION_LABELS[reason] ?? reason;
}

export function agreementNote(agreement: MpzpStatus["channel_agreement"]): string {
  return AGREEMENT_LABELS[agreement] ?? AGREEMENT_LABELS.none;
}

export function documentKindLabel(kind: MpzpActResult["document_kind"]): string {
  return kind ? DOCUMENT_KIND_LABELS[kind] : LINK_LABELS.document_url;
}

export function sourceLabel(source: MpzpActResult["sources"][number]): string {
  return source === "kimpzp" ? "KIMPZP" : "Rejestr Urbanistyczny";
}

/**
 * Udział działki objęty aktem. `null` oznacza akt bez granicy w źródle (KIMPZP) — „nie ustalono”, nigdy 0%.
 */
export function shareLabel(act: Pick<MpzpActResult, "intersection_pct" | "intersection_area_sqm">): string {
  if (act.intersection_pct == null) return "nie ustalono (akt bez granicy w źródle)";
  const pct = act.intersection_pct.toLocaleString("pl-PL", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  if (act.intersection_area_sqm == null) return `${pct}%`;
  const area = act.intersection_area_sqm.toLocaleString("pl-PL", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
  return `${pct}% (${area} m²)`;
}

export type MpzpActLink = { field: MpzpActLinkField; label: string; url: string; href: string | null };

/** Linki aktu w stałej kolejności; `href` tylko dla adresów zweryfikowanych jako HTTPS. */
export function actLinks(act: MpzpActResult): MpzpActLink[] {
  const verified = new Set(act.verified_links ?? []);
  return LINK_ORDER.flatMap((field) => {
    const url = act[field];
    if (!url) return [];
    const label = field === "document_url" ? documentKindLabel(act.document_kind) : LINK_LABELS[field];
    return [{ field, label, url, href: verifiedHttpsHref(url, verified.has(field)) }];
  });
}

/** Tytuł pozycji listy: numer uchwały albo, dla projektu, jawna informacja o braku numeru planu. */
export function actHeading(act: MpzpActResult): string {
  if (act.resolution_number) return `Uchwała ${act.resolution_number}`;
  if (act.document_kind === "commencement") return "Projekt planu (bez uchwały uchwalającej)";
  return "Akt bez numeru uchwały";
}

/** Czy „obowiązuje od” jest datą wejścia w życie; dla projektu to data wersji rekordu. */
export function showsEffectiveDates(act: MpzpActResult): boolean {
  return act.legal_status === "binding" || act.legal_status === "not_binding";
}

/** Komunikat stanu: z odpowiedzi API albo zapasowy dla zapisu sprzed AU-103. */
export function statusPresentation(status: MpzpStatus | null | undefined): {
  value: MpzpStatusValue | "missing";
  label: string;
  message: string;
  tone: MpzpStatusTone;
  mark: string;
} {
  if (!status) {
    return {
      value: "missing",
      label: MPZP_STATUS_MISSING_LABEL,
      message: MPZP_STATUS_MISSING_MESSAGE,
      tone: "muted",
      mark: "?",
    };
  }
  return {
    value: status.status,
    label: status.label,
    message: status.message,
    tone: MPZP_STATUS_TONES[status.status] ?? "muted",
    mark: MPZP_STATUS_MARKS[status.status] ?? "?",
  };
}

/**
 * Obiekty GeoJSON aktów do narysowania na mapie: część działki objęta aktem i granica w oknie wokół niej.
 * Rysowane są tylko akty, które mogą obowiązywać — projekt i akt bez mocy są na liście, ale nie na mapie.
 */
export function actMapLayers(acts: MpzpActResult[]): {
  intersections: Record<string, unknown>[];
  boundaries: Record<string, unknown>[];
} {
  const intersections: Record<string, unknown>[] = [];
  const boundaries: Record<string, unknown>[] = [];
  for (const act of acts) {
    if (act.legal_status !== "binding" && act.legal_status !== "unknown") continue;
    if (act.intersection_geojson) intersections.push(act.intersection_geojson);
    if (act.boundary_geojson) boundaries.push(act.boundary_geojson);
  }
  return { intersections, boundaries };
}
