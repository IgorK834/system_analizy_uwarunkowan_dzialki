import { describe, expect, it } from "vitest";

import {
  actLegalStatusLabel,
  actLinks,
  amendmentKindLabel,
  discoveryStatusLabel,
  discoveryStatusNote,
  multipleActsNote,
} from "@/lib/mpzpDiscovery";
import type { MpzpDiscoveryStatus } from "@/lib/types";
import { buildDiscovery, GORA_KALWARIA_DISCOVERY } from "@/test/mpzpDiscoveryFixtures";

describe("mpzpDiscovery", () => {
  it("ma rozłączne etykiety statusów; brak serwisu i błąd nie są „nie znaleziono”", () => {
    const statuses: MpzpDiscoveryStatus[] = ["available", "no_match", "no_coverage", "unavailable", "unknown"];
    const labels = statuses.map(discoveryStatusLabel);
    expect(new Set(labels).size).toBe(statuses.length);
    expect(discoveryStatusLabel("no_coverage")).toBe("Brak usługi gminnej w KIMPZP");
    expect(discoveryStatusNote("no_coverage")).toContain("brak danych, a nie potwierdzenie braku planu");
    expect(discoveryStatusNote("unavailable")).toContain("nie oznacza braku ograniczeń");
    expect(discoveryStatusNote("no_match")).toContain("nie dowodzi braku planu");
    expect(discoveryStatusNote("available")).toBeNull();
    expect(discoveryStatusLabel("nowy" as MpzpDiscoveryStatus)).toBe("Nie ustalono");
    expect(discoveryStatusNote("nowy" as MpzpDiscoveryStatus)).toContain("Wymagana ręczna weryfikacja");
  });

  it("opisuje wiele aktów w punkcie i na działce bez wskazywania aktu", () => {
    expect(multipleActsNote(GORA_KALWARIA_DISCOVERY)).toContain("nie wybiera aktu automatycznie");
    expect(
      multipleActsNote(buildDiscovery({ multiple_acts_at_point: false, multiple_acts_on_parcel: true })),
    ).toContain("może leżeć w kilku planach");
    expect(
      multipleActsNote(buildDiscovery({ multiple_acts_at_point: false, multiple_acts_on_parcel: false })),
    ).toBeNull();
  });

  it("czyni klikalnymi wyłącznie linki zweryfikowane jako HTTPS", () => {
    const links = actLinks(GORA_KALWARIA_DISCOVERY.acts[0]);

    expect(links.map((link) => link.label)).toEqual(["Tekst uchwały", "Legenda", "Strona BIP"]);
    expect(links[0]).toMatchObject({
      url: "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf",
      href: null,
    });
    expect(links[2].href).toBe("https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/757992");
    const forged = { ...GORA_KALWARIA_DISCOVERY.acts[0], text_url: "javascript:alert(1)", verified_links: ["text_url" as const] };
    expect(actLinks(forged)[0].href).toBeNull();
  });

  it("mapuje statusy aktu i rodzaje zmian", () => {
    expect(actLegalStatusLabel("binding")).toBe("obowiązujący");
    expect(actLegalStatusLabel("not_binding")).toBe("nieobowiązujący");
    expect(actLegalStatusLabel("x" as "unknown")).toBe("status nieustalony");
    expect(amendmentKindLabel("text_change")).toBe("Zmiana tekstowa");
    expect(amendmentKindLabel("note")).toBe("Opis zmian");
    expect(amendmentKindLabel("x" as "change")).toBe("Zmiana");
  });
});
