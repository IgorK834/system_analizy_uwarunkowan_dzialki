import { describe, expect, it } from "vitest";

import {
  MPZP_STATUS_MARKS,
  MPZP_STATUS_MISSING_MESSAGE,
  MPZP_STATUS_TONES,
  actHeading,
  actLinks,
  actMapLayers,
  agreementNote,
  channelStatusLabel,
  documentKindLabel,
  legalStatusLabel,
  precedenceLabel,
  rejectionLabel,
  shareLabel,
  showsEffectiveDates,
  sourceLabel,
  statusPresentation,
} from "@/lib/mpzpActs";
import { MPZP_ACTS_CASES } from "@/test/mpzpActsFixtures";
import type { MpzpActResult, MpzpStatusValue } from "@/lib/types";

const SIX: MpzpStatusValue[] = ["available", "ambiguous", "no_match", "no_coverage", "unavailable", "unknown"];

describe("stan ustalenia MPZP (AU-103)", () => {
  it("ma sześć rozłącznych stanów, każdy z własną etykietą i komunikatem z backendu", () => {
    const labels = new Set<string>();
    const messages = new Set<string>();
    for (const [key, value] of Object.entries(MPZP_ACTS_CASES)) {
      const view = statusPresentation(value.mpzp_status);
      expect(view.value).toBe(value.mpzp_status.status);
      expect(view.value).toBe(key);
      labels.add(view.label);
      messages.add(view.message);
      expect(MPZP_STATUS_TONES[view.value as MpzpStatusValue]).toBe(view.tone);
      expect(MPZP_STATUS_MARKS[view.value as MpzpStatusValue]).toBe(view.mark);
    }
    expect(labels.size).toBe(6);
    expect(messages.size).toBe(6);
    expect(Object.keys(MPZP_ACTS_CASES).sort()).toEqual([...SIX].sort());
  });

  it("zapis sprzed AU-103 dostaje osobny komunikat, a nie 'nie znaleziono planu'", () => {
    const view = statusPresentation(null);
    expect(view.value).toBe("missing");
    expect(view.message).toBe(MPZP_STATUS_MISSING_MESSAGE);
    expect(view.message).not.toMatch(/nie znaleziono/i);
    expect(statusPresentation(undefined).value).toBe("missing");
  });

  it("żaden komunikat stanu nie twierdzi, że brak planu lub brak ograniczeń", () => {
    for (const value of Object.values(MPZP_ACTS_CASES)) {
      expect(value.mpzp_status.message).not.toMatch(/brak ograniczeń jest|nie ma planu|plan nie obowiązuje/i);
    }
    expect(MPZP_ACTS_CASES.unavailable.mpzp_status.message).toMatch(/błąd źródła nie oznacza braku ograniczeń/i);
  });

  it("etykiety kanałów, zgodności, pierwszeństwa i powodów odrzucenia", () => {
    expect(channelStatusLabel("no_coverage")).toBe("brak usługi dla obszaru");
    expect(channelStatusLabel("zly" as never)).toBe("odpowiedź nierozpoznana");
    expect(agreementNote("mismatch")).toContain("MPZP_CHANNEL_MISMATCH");
    expect(agreementNote("zly" as never)).toBe("Żaden kanał nie wskazał aktu.");
    expect(precedenceLabel("unresolved")).toBe("kilka aktów bez rozstrzygnięcia");
    expect(precedenceLabel("zly" as never)).toBe("zly");
    expect(rejectionLabel("no_overlap")).toContain("poza działką");
    expect(rejectionLabel("zly" as never)).toBe("zly");
    expect(legalStatusLabel("not_yet_binding")).toContain("projekt");
    expect(legalStatusLabel("zly" as never)).toBe("status nieustalony");
    expect(sourceLabel("kimpzp")).toBe("KIMPZP");
    expect(sourceLabel("mpzp_ru")).toBe("Rejestr Urbanistyczny");
  });
});

describe("akty MPZP (AU-101)", () => {
  const [iv, older] = MPZP_ACTS_CASES.ambiguous.mpzp_acts;
  const [bielsko] = MPZP_ACTS_CASES.available.mpzp_acts;

  it("udział działki: null to 'nie ustalono', nigdy 0%", () => {
    expect(iv.intersection_pct).toBeNull();
    expect(shareLabel(iv)).toBe("nie ustalono (akt bez granicy w źródle)");
    expect(shareLabel({ intersection_pct: 11.84, intersection_area_sqm: 167.44 })).toBe("11,84% (167,44 m²)");
    expect(shareLabel({ intersection_pct: 100, intersection_area_sqm: null })).toBe("100,00%");
    expect(shareLabel({ intersection_pct: 0, intersection_area_sqm: 0 })).toBe("0,00% (0,00 m²)");
  });

  it("nagłówek: numer uchwały, projekt bez numeru planu albo brak numeru", () => {
    expect(actHeading(iv)).toBe("Uchwała IV/30/2024");
    expect(actHeading({ ...iv, resolution_number: null, document_kind: "commencement" })).toBe(
      "Projekt planu (bez uchwały uchwalającej)",
    );
    expect(actHeading({ ...iv, resolution_number: null, document_kind: null })).toBe("Akt bez numeru uchwały");
  });

  it("linki: HTTPS klikalne, HTTP jako tekst", () => {
    const links = actLinks(iv);
    expect(links.map((link) => link.field)).toEqual(["document_url", "legend_url"]);
    expect(links.every((link) => link.href === null)).toBe(true); // KIMPZP Góry Kalwarii to HTTP
    const bielskoLinks = actLinks(bielsko);
    expect(bielskoLinks.map((link) => link.label)).toEqual(["Tekst uchwały", "Legenda", "Rysunek planu"]);
    expect(bielskoLinks.every((link) => link.href?.startsWith("https://"))).toBe(true);
    expect(actLinks({ ...bielsko, document_url: null, legend_url: null, drawing_url: null })).toEqual([]);
    expect(actLinks({ ...bielsko, verified_links: [] }).every((link) => link.href === null)).toBe(true);
    expect(documentKindLabel(null)).toBe("Dokument");
    expect(documentKindLabel("commencement")).toContain("nie tekst planu");
  });

  it("daty 'obowiązuje od' tylko dla aktów z mocą prawną lub utraconą", () => {
    expect(showsEffectiveDates(bielsko)).toBe(true);
    expect(showsEffectiveDates({ ...bielsko, legal_status: "not_binding" })).toBe(true);
    expect(showsEffectiveDates({ ...bielsko, legal_status: "not_yet_binding" })).toBe(false);
    expect(showsEffectiveDates({ ...bielsko, legal_status: "unknown" })).toBe(false);
  });

  it("mapa: tylko akty, które mogą obowiązywać, i tylko z geometrią", () => {
    expect(actMapLayers(MPZP_ACTS_CASES.ambiguous.mpzp_acts)).toEqual({ intersections: [], boundaries: [] });
    const layers = actMapLayers([bielsko, { ...bielsko, legal_status: "not_yet_binding" }, { ...bielsko, legal_status: "not_binding" }]);
    expect(layers.intersections).toHaveLength(1);
    expect(layers.boundaries).toHaveLength(1);
    const unknown: MpzpActResult = { ...bielsko, legal_status: "unknown", boundary_geojson: null };
    expect(actMapLayers([unknown])).toEqual({ intersections: [unknown.intersection_geojson], boundaries: [] });
    expect(older.sources).toEqual(["kimpzp"]);
  });
});
