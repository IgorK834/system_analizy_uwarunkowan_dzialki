import { describe, expect, it } from "vitest";

import {
  COMMUNE_STATUS_EXPLANATIONS,
  COMMUNE_STATUS_LABELS,
  catalogFreshnessText,
  communeDetail,
  coverageSummaryText,
  filterCommunes,
  hasOutdatedVersion,
  importFailureText,
} from "@/lib/pogCoverage";
import type { PogCommuneStatus } from "@/lib/types";
import { buildCommuneCoverage, buildPogCoverage } from "@/test/pogFixtures";

// „Brak planu” jako samodzielne stwierdzenie wolno napisać tylko przy urzędowym potwierdzeniu — którego tu nie ma.
const FORBIDDEN = /(^|[^a-ząćęłńóśźż])brak planu(?! ogólnego| w gminie| —)/i;

describe("pogCoverage", () => {
  it("cztery rozłączne stany gminy mają odrębne etykiety i wyjaśnienia, żaden nie mówi „brak planu”", () => {
    const statuses = Object.keys(COMMUNE_STATUS_LABELS) as PogCommuneStatus[];
    expect(statuses.sort()).toEqual(["imported", "in_ru_not_imported", "no_act_in_ru", "unknown"]);
    expect(new Set(Object.values(COMMUNE_STATUS_LABELS)).size).toBe(4);
    expect(new Set(Object.values(COMMUNE_STATUS_EXPLANATIONS)).size).toBe(4);
    for (const status of statuses) {
      expect(COMMUNE_STATUS_LABELS[status]).not.toMatch(FORBIDDEN);
      expect(COMMUNE_STATUS_EXPLANATIONS[status]).not.toMatch(FORBIDDEN);
    }
    expect(COMMUNE_STATUS_EXPLANATIONS.no_act_in_ru).toMatch(/nie dowodzi braku planu/);
    expect(COMMUNE_STATUS_EXPLANATIONS.in_ru_not_imported).toMatch(/nie oznacza braku planu/);
    expect(COMMUNE_STATUS_LABELS.unknown).toMatch(/nieustalony/);
  });

  it("filtruje listę po zakresie i po nazwie, powiecie, województwie lub kodzie TERC (bez rozróżniania wielkości liter)", () => {
    const coverage = buildPogCoverage();
    expect(filterCommunes(coverage.communes, "all", "")).toHaveLength(3);
    expect(filterCommunes(coverage.communes, "imported", "").map((item) => item.terc)).toEqual(["226401"]);
    expect(filterCommunes(coverage.communes, "in_ru_not_imported", "").map((item) => item.terc)).toEqual([
      "246101",
      "126101",
    ]);
    expect(filterCommunes(coverage.communes, "all", "  KRAKÓW ").map((item) => item.terc)).toEqual(["126101"]);
    expect(filterCommunes(coverage.communes, "all", "śląskie").map((item) => item.terc)).toEqual(["246101"]);
    expect(filterCommunes(coverage.communes, "all", "2264").map((item) => item.name)).toEqual(["Sopot"]);
    expect(filterCommunes(coverage.communes, "all", "nie ma takiej")).toEqual([]);
  });

  it("wykrywa nieaktualne wersje (RU ma nowszą) i opisuje je w szczegółach gminy", () => {
    const fresh = buildCommuneCoverage();
    expect(hasOutdatedVersion(fresh)).toBe(false);
    const outdated = buildCommuneCoverage({
      imported_acts: [{ ...fresh.imported_acts[0], up_to_date: false }],
    });
    expect(hasOutdatedVersion(outdated)).toBe(true);
    expect(filterCommunes([fresh, outdated], "outdated", "")).toEqual([outdated]);
    expect(communeDetail(outdated)).toContain("RU ma nowszą wersję (czeka na import)");
    expect(communeDetail(fresh)).toContain("zgodna z wykazem RU");
    expect(communeDetail(fresh)).toContain("stan #42");
    expect(communeDetail(fresh)).toContain("import 28.09.2026");
    const unknownToken = buildCommuneCoverage({
      imported_acts: [{ ...fresh.imported_acts[0], up_to_date: null, act_version: null }],
    });
    expect(communeDetail(unknownToken)).toContain("wersja nieustalona");
    expect(communeDetail(unknownToken)).not.toContain("wykazem RU");
  });

  it("gmina niezaimportowana pokazuje zlecenie importu, a bez zlecenia nic nie zmyśla", () => {
    const coverage = buildPogCoverage();
    const queued = coverage.communes[1];
    expect(communeDetail(queued)).toBe("zlecenie importu w kolejce (próby: 0)");
    expect(communeDetail(coverage.communes[2])).toBe("");
    expect(
      communeDetail({ ...queued, import_job: { ...queued.import_job!, status: "failed", attempts: 3 } }),
    ).toBe("ostatni import nie powiódł się (próby: 3)");
    expect(
      communeDetail({ ...queued, import_job: { ...queued.import_job!, status: "inna", attempts: 1 } }),
    ).toBe("inna (próby: 1)");
  });

  it("podsumowanie i świeżość wykazu RU", () => {
    const coverage = buildPogCoverage();
    expect(coverageSummaryText(coverage)).toBe(
      "Gminy z planem ogólnym w Rejestrze Urbanistycznym: 3; zaimportowane: 1; czekają na import: 2",
    );
    expect(
      coverageSummaryText({
        ...coverage,
        summary: { ...coverage.summary, outdated_versions: 2, imported_without_ru_entry: 1 },
      }),
    ).toMatch(/z nowszą wersją w RU: 2; zaimportowane spoza wykazu RU: 1$/);

    expect(catalogFreshnessText(coverage)).toBe("Wykaz RU zsynchronizowany 13.10.2026, 13:00.");
    expect(catalogFreshnessText({ ...coverage, catalog_fresh: false })).toMatch(
      /Wykaz RU jest nieaktualny.*„brak aktu w RU” nie jest ustalany/,
    );
    expect(catalogFreshnessText({ ...coverage, catalog_synced_at: null })).toMatch(
      /nie został jeszcze zsynchronizowany — stan gmin jest nieustalony/,
    );
  });

  it("ostatnia nieudana próba importu jest opisana po polsku z komunikatem źródła, a gmina nie udaje „braku planu”", () => {
    const waiting = buildCommuneCoverage({
      terc: "020502",
      name: "Bolków",
      status: "in_ru_not_imported",
      imported_acts: [],
      import_job: null,
      last_import_failure: {
        published_act_id: 3438,
        code: "qa_rejected",
        message: "PL.ZIPPZP.11945/020502-POG/1POG-51SU: Parametr procentowy POG musi należeć do 0–100 (wartość: 150).",
        run_id: 32,
      },
    });
    const text = importFailureText(waiting) as string;
    expect(text).toContain("odrzucona przez kontrolę jakości — wada danych w źródle");
    expect(text).toContain("wartość: 150");
    expect(communeDetail(waiting)).toBe(text);
    expect(text).not.toMatch(FORBIDDEN);

    // Zlecenie importu i ostatnia próba razem; nieznany kod i brak komunikatu; brak próby.
    const both = { ...waiting, import_job: { job_id: 1, status: "failed", attempts: 3, requested_at: null, finished_at: null, last_error: null } };
    expect(communeDetail(both)).toContain("ostatni import nie powiódł się (próby: 3)");
    expect(communeDetail(both)).toContain("Ostatnia próba importu: odrzucona");
    expect(importFailureText({ ...waiting, last_import_failure: { published_act_id: 1, code: "dziwny", message: null, run_id: null } })).toBe(
      "Ostatnia próba importu: kod dziwny.",
    );
    expect(importFailureText({ ...waiting, last_import_failure: null })).toBeNull();
    expect(importFailureText(buildCommuneCoverage())).toBeNull();
  });
});
