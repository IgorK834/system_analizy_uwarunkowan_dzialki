import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompatibilityAssessmentCard } from "@/components/CompatibilityAssessmentCard";
import { ResultPanel } from "@/components/ResultPanel";
import type { CompatibilityStatus } from "@/lib/types";
import { buildAnalyzeResponse, buildPogResult } from "@/test/fixtures";
import { assessment, pair } from "@/test/manualZoneFixtures";

const LEGAL_CLAIMS = /można zabudować|zabudowa jest dopuszczalna|zgodność potwierdzona|działka jest zgodna/i;

describe("CompatibilityAssessmentCard (BK-205)", () => {
  it("pokazuje wynik, datę stanu prawnego, reguły i pary stref", () => {
    render(<CompatibilityAssessmentCard assessment={assessment()} />);

    expect(screen.getByTestId("compatibility-status")).toHaveTextContent(
      "potencjalna rozbieżność funkcji — wymaga weryfikacji",
    );
    expect(screen.getByText("20.09.2026")).toBeVisible();
    expect(screen.getByText("mpzp-pog-function-table v1.0")).toBeVisible();
    const table = screen.getByRole("table", { name: "Pary stref MPZP × POG" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("1MN × SJ");
    expect(rows[0]).toHaveTextContent("600 m² (60.0%)");
    expect(rows[0]).toHaveTextContent("mpzp-pog-function-table:single_family_housing:SJ v1.0");
    expect(rows[1]).toHaveAttribute("data-status", "incompatible");
    expect(screen.getByText(/nie przesądza o prawnej możliwości zabudowy/)).toBeVisible();
    expect(screen.getByText("Ocena relacji wymaga ręcznej weryfikacji.")).toBeVisible();
    expect(screen.getByRole("list", { name: "Źródła oceny" })).toHaveTextContent("Tabela reguł · wersja 1.0");
  });

  it.each(["compatible", "incompatible", "uncertain", "not_applicable", "unknown"] as CompatibilityStatus[])(
    "status %s nie stwierdza prawnej możliwości zabudowy",
    (status) => {
      render(
        <CompatibilityAssessmentCard
          assessment={assessment({
            status,
            manual_review_required: status !== "compatible",
            zone_pairs: status === "compatible" ? [pair()] : [],
          })}
        />,
      );
      const section = screen.getByRole("region", { name: "Relacja MPZP–POG" });
      expect(section.textContent ?? "").not.toMatch(LEGAL_CLAIMS);
      expect(section).toHaveTextContent("nie stwierdza prawnej możliwości zabudowy");
    },
  );

  it("para bez geometrii jest opisana jako niezidentyfikowana przestrzennie", () => {
    render(
      <CompatibilityAssessmentCard
        assessment={assessment({
          status: "uncertain",
          zone_pairs: [
            pair({
              mpzp_zone_symbol: "230_U",
              mpzp_assignment_method: "manual_user_input",
              spatially_identified: false,
              overlap_area_sqm: null,
              overlap_pct: null,
              status: "uncertain",
              as_of: null,
              rule_id: null,
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText(/para niezidentyfikowana przestrzennie/)).toBeVisible();
    expect(screen.getByText("nieustalone", { exact: false })).toBeVisible();
    expect(screen.getByText("brak reguły")).toBeVisible();
  });

  it.each([
    [true, "stwierdzono konflikt"],
    [false, "brak konfliktu"],
    [null, "brak rozstrzygnięcia"],
  ])("zapis legacy (%s) nie udaje pełnej oceny", (conflict, text) => {
    render(
      <CompatibilityAssessmentCard
        assessment={assessment({
          status: "unknown",
          reason_code: "LEGACY_BOOLEAN_ONLY",
          rule_id: null,
          rule_version: null,
          as_of: null,
          sources: [],
          zone_pairs: [],
          legacy_evidence: {
            origin: "pog_data.conflict_with_mpzp",
            conflict_with_mpzp: conflict,
            result: null,
            reasoning: null,
            confidence: null,
          },
        })}
      />,
    );

    expect(screen.getByRole("note")).toHaveTextContent(text);
    expect(screen.getByRole("note")).toHaveTextContent("nie jest pełną oceną");
    expect(screen.getByText("brak — zapis historyczny")).toBeVisible();
    expect(screen.getByText("nieustalony")).toBeVisible();
  });

  it("brak oceny ma jawny komunikat", () => {
    render(<CompatibilityAssessmentCard assessment={null} />);
    expect(screen.getByText("Nie wykonano oceny relacji MPZP–POG dla tej analizy.")).toBeVisible();
  });

  it("w panelu wyników ocena jest osobną sekcją po MPZP i POG", () => {
    render(
      <ResultPanel
        result={buildAnalyzeResponse({
          status: "partial",
          pog: buildPogResult({ legal_status: "binding", compatibility_assessment: assessment() }),
        })}
        map={null}
      />,
    );

    const mpzp = screen.getByRole("region", { name: "Strefy MPZP" });
    const pog = screen.getByRole("region", { name: "Plan Ogólny Gminy i OUZ" });
    const relation = screen.getByRole("region", { name: "Relacja MPZP–POG" });
    expect(mpzp.compareDocumentPosition(pog) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(pog.compareDocumentPosition(relation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(pog.textContent ?? "").not.toMatch(/rozbieżność|zgodność z dominującą/i);
    expect(document.body.textContent ?? "").not.toMatch(LEGAL_CLAIMS);
  });
});
