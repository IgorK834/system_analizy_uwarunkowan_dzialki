/**
 * Pokrycie krajowe importu planów ogólnych (issue 301, ADR-026): etykiety i filtrowanie stanów gmin.
 *
 * Cztery rozłączne stany gminy. Żaden z nich nie brzmi „brak planu”: „brak aktu w wykazie RU” wynika z wykazu z podanej
 * daty i nie dowodzi braku planu w gminie, a gmina niezaimportowana nie ma jeszcze lokalnych danych (brak obiektów na
 * mapie nie jest wtedy informacją o planie).
 */
import { formatCheckedAt } from "@/lib/pogLayerState";
import { formatPlDate, legalStatusShort } from "@/lib/pogStatus";
import type { PogCommuneCoverage, PogCommuneStatus, PogCoverage } from "@/lib/types";

export const COMMUNE_STATUS_LABELS: Record<PogCommuneStatus, string> = {
  imported: "Zaimportowana",
  in_ru_not_imported: "Brak lokalnych danych (≠ brak planu)",
  no_act_in_ru: "Brak aktu POG w wykazie RU",
  unknown: "Źródło niedostępne — stan nieustalony",
};

export const COMMUNE_STATUS_EXPLANATIONS: Record<PogCommuneStatus, string> = {
  imported: "Lokalne dane planu ogólnego tej gminy są w stanie krajowym i widoczne na mapie.",
  in_ru_not_imported:
    "Rejestr Urbanistyczny publikuje plan ogólny tej gminy, ale lokalnych danych jeszcze nie ma. " +
    "Brak obiektów na mapie nie oznacza braku planu.",
  no_act_in_ru:
    "Zsynchronizowany wykaz Rejestru Urbanistycznego nie zawiera aktu POG tej gminy — to nie dowodzi braku planu w gminie.",
  unknown:
    "Wykaz Rejestru Urbanistycznego nie jest zsynchronizowany albo jest przeterminowany — nie można stwierdzić, czy gmina ma plan.",
};

export type CoverageFilter = "all" | "imported" | "in_ru_not_imported" | "outdated";

export const COVERAGE_FILTER_LABELS: Record<CoverageFilter, string> = {
  all: "Wszystkie gminy z planem w RU",
  imported: "Zaimportowane",
  in_ru_not_imported: "Czekają na import",
  outdated: "Z nowszą wersją w RU",
};

/** Czy któraś zaimportowana wersja jest starsza niż wykaz RU (RU ma nowszą). */
export function hasOutdatedVersion(commune: PogCommuneCoverage): boolean {
  return commune.imported_acts.some((act) => act.up_to_date === false);
}

export function filterCommunes(
  communes: readonly PogCommuneCoverage[],
  filter: CoverageFilter,
  query: string,
): PogCommuneCoverage[] {
  const needle = query.trim().toLocaleLowerCase("pl");
  return communes.filter((commune) => {
    if (filter === "imported" && commune.status !== "imported") return false;
    if (filter === "in_ru_not_imported" && commune.status !== "in_ru_not_imported") return false;
    if (filter === "outdated" && !hasOutdatedVersion(commune)) return false;
    if (!needle) return true;
    return [commune.name, commune.district, commune.voivodeship, commune.terc]
      .filter((value): value is string => Boolean(value))
      .some((value) => value.toLocaleLowerCase("pl").includes(needle));
  });
}

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: "zlecenie importu w kolejce",
  running: "import w toku",
  failed: "ostatni import nie powiódł się",
  succeeded: "ostatni import zakończony",
  cancelled: "zlecenie anulowane",
};

const FAILURE_CODE_LABELS: Record<string, string> = {
  qa_rejected: "odrzucona przez kontrolę jakości — wada danych w źródle",
  rate_limited: "Rejestr Urbanistyczny ograniczył liczbę żądań",
  not_xml: "źródło nie zwróciło dokumentu GML",
  too_large: "plik aktu przekroczył limit rozmiaru",
  transport: "źródło było niedostępne",
};

/** Ostatnia nieudana próba importu: powód po polsku i, jeśli jest, komunikat (np. obiekt i wartość z wadą danych). */
export function importFailureText(commune: PogCommuneCoverage): string | null {
  const failure = commune.last_import_failure;
  if (!failure) return null;
  const reason = FAILURE_CODE_LABELS[failure.code] ?? `kod ${failure.code}`;
  return `Ostatnia próba importu: ${reason}${failure.message ? ` (${failure.message})` : ""}.`;
}

/** Jednolinijkowy opis gminy: dla zaimportowanej — wersja, stan i data; dla pozostałych — zlecenie importu i ostatnia próba. */
export function communeDetail(commune: PogCommuneCoverage): string {
  if (commune.status === "imported") {
    return commune.imported_acts
      .map((act) => {
        const imported = formatPlDate(act.imported_at);
        const version = act.act_version ? `wersja ${act.act_version}` : "wersja nieustalona";
        const freshness =
          act.up_to_date === false
            ? "; RU ma nowszą wersję (czeka na import)"
            : act.up_to_date === true
              ? "; zgodna z wykazem RU"
              : "";
        return `${legalStatusShort(act.legal_status)}, wydanie ${act.release_label}, ${version}, stan #${act.state_id}${
          imported ? `, import ${imported}` : ""
        }${freshness}`;
      })
      .join(" | ");
  }
  const parts: string[] = [];
  if (commune.import_job) {
    const label = JOB_STATUS_LABELS[commune.import_job.status] ?? commune.import_job.status;
    parts.push(`${label} (próby: ${commune.import_job.attempts})`);
  }
  const failure = importFailureText(commune);
  if (failure) parts.push(failure);
  return parts.join(" ");
}

export function coverageSummaryText(coverage: PogCoverage): string {
  const { summary } = coverage;
  const parts = [
    `Gminy z planem ogólnym w Rejestrze Urbanistycznym: ${summary.ru_communes}`,
    `zaimportowane: ${summary.imported_communes}`,
    `czekają na import: ${summary.in_ru_not_imported}`,
  ];
  if (summary.outdated_versions > 0) parts.push(`z nowszą wersją w RU: ${summary.outdated_versions}`);
  if (summary.imported_without_ru_entry > 0) {
    parts.push(`zaimportowane spoza wykazu RU: ${summary.imported_without_ru_entry}`);
  }
  return parts.join("; ");
}

export function catalogFreshnessText(coverage: PogCoverage): string {
  const synced = formatCheckedAt(coverage.catalog_synced_at);
  if (!synced) return "Wykaz Rejestru Urbanistycznego nie został jeszcze zsynchronizowany — stan gmin jest nieustalony.";
  return coverage.catalog_fresh
    ? `Wykaz RU zsynchronizowany ${synced}.`
    : `Wykaz RU jest nieaktualny (ostatnia synchronizacja ${synced}) — stan „brak aktu w RU” nie jest ustalany.`;
}
