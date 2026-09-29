import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReportDownloadButton } from "@/components/ReportDownloadButton";

const { getAnalysisReportMock } = vi.hoisted(() => ({
  getAnalysisReportMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getAnalysisReport: getAnalysisReportMock,
}));

describe("ReportDownloadButton", () => {
  const createObjectUrlMock = vi.fn(() => "blob:report-test");
  const revokeObjectUrlMock = vi.fn();
  let downloadedFilename: string | null;

  beforeEach(() => {
    vi.restoreAllMocks();
    getAnalysisReportMock.mockReset();
    createObjectUrlMock.mockClear();
    revokeObjectUrlMock.mockClear();
    downloadedFilename = null;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectUrlMock,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectUrlMock,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloadedFilename = this.download;
    });
  });

  it("wyłącza pobieranie, gdy analiza nie została zapisana", () => {
    render(<ReportDownloadButton analysisId={null} parcelIdentifier={null} />);

    expect(screen.getByRole("button", { name: "Pobierz raport PDF" })).toBeDisabled();
    expect(screen.getByText(/po zapisaniu analizy działki/)).toBeVisible();
  });

  it("pobiera raport bieżącego analysis_id i nadaje mu bezpieczną nazwę", async () => {
    const user = userEvent.setup();
    const pdf = new Blob(["%PDF-1.7 test"], { type: "application/pdf" });
    getAnalysisReportMock.mockResolvedValue(pdf);
    render(
      <ReportDownloadButton
        analysisId={77}
        accessToken="token-77"
        parcelIdentifier="122101_1.0001.77"
      />,
    );

    expect(screen.getByText(/122101_1\.0001\.77, analiza #77/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Pobierz raport PDF" }));

    await waitFor(() => expect(getAnalysisReportMock).toHaveBeenCalledOnce());
    expect(getAnalysisReportMock).toHaveBeenCalledWith(
      77,
      expect.objectContaining({
        accessToken: "token-77",
        signal: expect.any(AbortSignal),
      }),
    );
    expect(createObjectUrlMock).toHaveBeenCalledWith(pdf);
    expect(downloadedFilename).toBe("raport_analizy_77.pdf");
    expect(revokeObjectUrlMock).toHaveBeenCalledWith("blob:report-test");
  });

  it("pokazuje bezpieczny błąd i pozwala spróbować ponownie", async () => {
    const user = userEvent.setup();
    getAnalysisReportMock.mockRejectedValue(
      new Error("Nie udało się wygenerować raportu PDF."),
    );
    render(<ReportDownloadButton analysisId={18} parcelIdentifier={null} />);

    await user.click(screen.getByRole("button", { name: "Pobierz raport PDF" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nie udało się wygenerować raportu PDF.");
    expect(screen.getByRole("button", { name: "Pobierz raport PDF" })).toBeEnabled();
  });

  it("anuluje generowanie po zmianie zaznaczonej analizy", async () => {
    const user = userEvent.setup();
    let observedSignal: AbortSignal | undefined;
    getAnalysisReportMock.mockImplementation(
      (_analysisId: number, options: { signal?: AbortSignal }) => {
        observedSignal = options.signal;
        return new Promise<Blob>(() => undefined);
      },
    );
    const { unmount } = render(
      <ReportDownloadButton analysisId={9} parcelIdentifier="TEST.9" />,
    );

    await user.click(screen.getByRole("button", { name: "Pobierz raport PDF" }));
    expect(observedSignal?.aborted).toBe(false);
    unmount();

    expect(observedSignal?.aborted).toBe(true);
  });
});
