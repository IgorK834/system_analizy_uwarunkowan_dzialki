import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PogOfficialSources } from "@/components/PogOfficialSources";
import { ResultPanel } from "@/components/ResultPanel";
import { buildAnalyzeResponse, buildPogResult } from "@/test/fixtures";
import type { FormalDocumentSource, PogActResult } from "@/lib/types";

const SHA = "b".repeat(64);
const CARD =
  "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/csw?service=CSW&version=2.0.2&request=GetRecordById&id=9223a9d0-e8b7-453b-b7bc-000e5e4d4d87";
const GML =
  "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs?service=WFS&request=GetFeature&typeNames=app-pog%3AAktPlanowaniaPrzestrzennego";

function document(overrides: Partial<FormalDocumentSource> = {}): FormalDocumentSource {
  return {
    document_identifier: "PL.ZIPPZP.10011/226401-POG/1",
    document_version: null,
    publication_id: null,
    title: "UCHWAŁA NR LIV/923/2024 RADY MIASTA SOPOTU",
    short_name: "POG Sopot - przystąpienie",
    identification_number: null,
    relation: "przystapienie",
    document_date: "2024-04-23",
    effective_date: "2024-04-23",
    repeal_date: null,
    link: "https://bip.sopot.pl/m,287,plan-ogolny.html",
    link_verified: true,
    record_sha256: SHA,
    status: "current",
    warning: null,
    ...overrides,
  };
}

function act(overrides: Partial<PogActResult> = {}): PogActResult {
  return {
    id: "PL.ZIPPZP.10011/226401-POG/1POG",
    version: "20260819T010000",
    title: "Plan ogólny miasta Sopotu",
    resolution_number: null,
    resolution_date: null,
    act_identifier: "PL.ZIPPZP.10011/226401-POG/1POG",
    act_version: "20260819T010000",
    publication_id:
      "https://www.gov.pl/zagospodarowanieprzestrzenne/app/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011/226401-POG/1POG/20260819T010000",
    version_started_at: "2026-08-19T01:00:00Z",
    publication_date: "2026-08-12",
    valid_from: "2026-08-19",
    valid_to: null,
    gml_url: GML,
    gml_url_verified: true,
    card_url: CARD,
    card_url_verified: true,
    data_release_id: 7,
    release_label: "ru-sopot",
    artifact_sha256: "a".repeat(64),
    fetched_at: "2026-09-24T10:00:00Z",
    metadata: {
      record_id: "9223a9d0-e8b7-453b-b7bc-000e5e4d4d87",
      resource_identifier: null,
      title: null,
      publication_date: "2026-08-12",
      revision_date: null,
      creation_date: null,
      date_stamp: null,
      metadata_url: CARD,
      metadata_url_verified: true,
      references: [],
      record_sha256: null,
      response_sha256: null,
      fetched_at: null,
    },
    formal_documents: [document()],
    ...overrides,
  };
}

describe("PogOfficialSources", () => {
  it("pokazuje klikalne źródła, dokładną wersję, wydanie i SHA", () => {
    render(<PogOfficialSources act={act()} />);

    const region = screen.getByRole("region", { name: "Źródła urzędowe aktu POG" });
    expect(within(region).getByText("PL.ZIPPZP.10011/226401-POG/1POG")).toBeVisible();
    expect(within(region).getByText(/20260819T010000 \(od 19\.08\.2026\)/)).toBeVisible();
    expect(within(region).getByText(/#7 \(ru-sopot\)/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Rejestr Urbanistyczny — WFS APP" })).toHaveAttribute(
      "href",
      GML,
    );
    const card = screen.getByRole("link", { name: /rekord 9223a9d0/ });
    expect(card).toHaveAttribute("href", CARD);
    expect(card).toHaveAttribute("rel", "noopener noreferrer");
    const doc = screen.getByRole("link", { name: "UCHWAŁA NR LIV/923/2024 RADY MIASTA SOPOTU" });
    expect(doc).toHaveAttribute("href", "https://bip.sopot.pl/m,287,plan-ogolny.html");
    expect(screen.getByTitle(SHA)).toHaveTextContent(`${"b".repeat(12)}…`);
    expect(screen.getByText(/przystąpienie do sporządzenia · aktualny/)).toBeVisible();
  });

  it("dokument nieaktualny i niedostępny ma widoczne ostrzeżenie i brak linku", () => {
    render(
      <PogOfficialSources
        act={act({
          formal_documents: [
            document({
              status: "superseded",
              repeal_date: "2026-08-19",
              warning: "Dokument jest nieaktualny — został uchylony.",
            }),
            document({
              document_identifier: "PL.ZIPPZP.10011/226401-POG/XXIV.300.2026",
              title: null,
              short_name: null,
              link: null,
              link_verified: false,
              record_sha256: null,
              relation: null,
              status: "unavailable",
              warning: "Dokument niedostępny: akt go wskazuje, ale rekordu dokumentu nie ma.",
            }),
          ],
        })}
      />,
    );
    const notes = screen.getAllByRole("note");
    expect(notes.map((note) => note.textContent)).toEqual([
      "Dokument jest nieaktualny — został uchylony.",
      "Dokument niedostępny: akt go wskazuje, ale rekordu dokumentu nie ma.",
    ]);
    expect(screen.getByText(/nieaktualny · w życie 23\.04\.2024 · uchylony 19\.08\.2026/)).toBeVisible();
    expect(screen.getByText(/relacja nieznana · niedostępny/)).toBeVisible();
    expect(screen.getByText("PL.ZIPPZP.10011/226401-POG/XXIV.300.2026", { selector: "strong" })).toBeVisible();
  });

  it("nie linkuje niezweryfikowanych adresów i escapuje tytuły", () => {
    const { container } = render(
      <PogOfficialSources
        act={act({
          gml_url: "javascript:alert(1)",
          gml_url_verified: true,
          card_url: CARD,
          card_url_verified: false,
          metadata: null,
          version_started_at: null,
          publication_id: null,
          valid_from: null,
          publication_date: null,
          data_release_id: null,
          formal_documents: [
            document({
              title: '<img src=x onerror="alert(1)">',
              link: "http://bip.sopot.pl/stara.pdf",
              link_verified: false,
              document_version: "v2",
              relation: "inna",
              status: "unresolved",
              warning: "Nierozstrzygnięte powiązanie z tą wersją aktu.",
            }),
          ],
        })}
      />,
    );
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("brak zweryfikowanego odnośnika")).toBeVisible();
    expect(screen.getByText("metadane CSW niedostępne")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeVisible();
    expect(screen.getByText("http://bip.sopot.pl/stara.pdf")).toBeVisible();
    expect(screen.getByText(/inna · powiązanie nierozstrzygnięte/)).toBeVisible();
    expect(screen.getByText(/1 \/ v2/)).toBeVisible();
  });

  it("panel wyników prowadzi od parametru strefy do źródła GML", () => {
    const zoneGml = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs?typeNames=app-pog%3AStrefaPlanistyczna";
    render(
      <ResultPanel
        result={buildAnalyzeResponse({
          pog: buildPogResult({
            legal_status: "binding",
            coverage_status: "available",
            act: act({ formal_documents: undefined, valid_to: "2030-01-01" }),
            zones: [
              {
                id: "PL.ZIPPZP.10011/226401-POG/1POG-100SU",
                symbol: "100SU",
                type: "SU",
                label: "strefa usługowa",
                area_sqm: 16,
                area_pct: 100,
                max_overground_floor_area_ratio: 0.9,
                max_building_height_m: 4,
                max_building_coverage_pct: 90,
                min_biologically_active_pct: 5,
                primary_profile: [],
                additional_profiles: [],
                source: null,
                feature_version: "20260819T010000",
                gml_url: zoneGml,
                gml_url_verified: true,
              },
              {
                id: "bez-zrodla",
                symbol: "SN",
                type: "SN",
                label: null,
                area_sqm: 0,
                area_pct: 0,
                max_overground_floor_area_ratio: null,
                max_building_height_m: null,
                max_building_coverage_pct: null,
                min_biologically_active_pct: null,
                primary_profile: [],
                additional_profiles: [],
                source: null,
              },
            ],
          }),
        })}
        map={null}
      />,
    );
    const table = screen.getByRole("table", { name: "Strefy POG przecinające działkę" });
    expect(within(table).getByRole("link", { name: "GML" })).toHaveAttribute("href", zoneGml);
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText(/19\.08\.2026 do 01\.01\.2030/)).toBeVisible();
    expect(screen.getByRole("region", { name: "Źródła urzędowe aktu POG" })).toBeVisible();
  });
});
