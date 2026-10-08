import { verifiedHttpsHref } from "@/lib/safeLink";
import type {
  MpzpDiscoveryAct,
  MpzpDiscoveryAmendment,
  MpzpDiscoveryLinkField,
  MpzpDiscoverySection,
  MpzpDiscoveryStatus,
} from "@/lib/types";

/**
 * Etykiety i reguły prezentacji discovery MPZP z KIMPZP (AU-004).
 *
 * Statusy są rozłączne: „brak serwisu” i błąd usługi nigdy nie są pokazywane
 * jako „nie znaleziono planu”, a przy kilku aktach UI nie wskazuje żadnego.
 */
const STATUS_LABELS: Record<MpzpDiscoveryStatus, string> = {
  available: "KIMPZP wskazało akty planu",
  no_match: "Nie znaleziono planu w punktach działki",
  no_coverage: "Brak usługi gminnej w KIMPZP",
  unavailable: "Usługa MPZP gminy niedostępna",
  unknown: "Nie ustalono",
};

const STATUS_NOTES: Record<MpzpDiscoveryStatus, string | null> = {
  available: null,
  no_match:
    "Usługa gminna nie zwróciła planu dla punktów działki. To nie dowodzi braku planu — sprawdź w gminie.",
  no_coverage:
    "KIMPZP nie ma usługi gminnej dla tego obszaru („brak serwisu dla wskazanego obszaru”). To brak danych, a nie potwierdzenie braku planu.",
  unavailable:
    "Usługa MPZP gminy zwróciła błąd. Nie ustalono, czy obowiązuje plan — błąd źródła nie oznacza braku ograniczeń.",
  unknown: "Odpowiedź KIMPZP nie została rozpoznana albo rozpoznania nie wykonano. Wymagana ręczna weryfikacja.",
};

const LEGAL_STATUS_LABELS: Record<MpzpDiscoveryAct["legal_status"], string> = {
  binding: "obowiązujący",
  not_binding: "nieobowiązujący",
  unknown: "status nieustalony",
};

const AMENDMENT_KIND_LABELS: Record<MpzpDiscoveryAmendment["kind"], string> = {
  text_change: "Zmiana tekstowa",
  change: "Zmiana",
  note: "Opis zmian",
};

const LINK_LABELS: Record<MpzpDiscoveryLinkField, string> = {
  text_url: "Tekst uchwały",
  legend_url: "Legenda",
  drawing_url: "Rysunek planu",
  bip_url: "Strona BIP",
  www_url: "Strona aktu",
};

const LINK_ORDER: MpzpDiscoveryLinkField[] = ["text_url", "legend_url", "drawing_url", "bip_url", "www_url"];

export function discoveryStatusLabel(status: MpzpDiscoveryStatus): string {
  return STATUS_LABELS[status] ?? STATUS_LABELS.unknown;
}

export function discoveryStatusNote(status: MpzpDiscoveryStatus): string | null {
  return status in STATUS_NOTES ? STATUS_NOTES[status] : STATUS_NOTES.unknown;
}

export function actLegalStatusLabel(status: MpzpDiscoveryAct["legal_status"]): string {
  return LEGAL_STATUS_LABELS[status] ?? LEGAL_STATUS_LABELS.unknown;
}

export function amendmentKindLabel(kind: MpzpDiscoveryAmendment["kind"]): string {
  return AMENDMENT_KIND_LABELS[kind] ?? AMENDMENT_KIND_LABELS.change;
}

/** Komunikat o wielu aktach; null, gdy wskazano co najwyżej jeden. */
export function multipleActsNote(section: MpzpDiscoverySection): string | null {
  if (section.multiple_acts_at_point) {
    return "W punkcie działki KIMPZP wskazuje kilka aktów. System nie wybiera aktu automatycznie — ustal w uchwałach, który rozstrzyga o przeznaczeniu.";
  }
  if (section.multiple_acts_on_parcel) {
    return "Punkty działki wskazują różne akty — działka może leżeć w kilku planach. Aktu nie wybrano automatycznie.";
  }
  return null;
}

export type ActLink = { field: MpzpDiscoveryLinkField; label: string; url: string; href: string | null };

/** Linki aktu w stałej kolejności; `href` tylko dla adresów zweryfikowanych jako HTTPS. */
export function actLinks(act: MpzpDiscoveryAct): ActLink[] {
  const verified = new Set(act.verified_links ?? []);
  return LINK_ORDER.flatMap((field) => {
    const url = act[field];
    if (!url) return [];
    return [{ field, label: LINK_LABELS[field], url, href: verifiedHttpsHref(url, verified.has(field)) }];
  });
}
