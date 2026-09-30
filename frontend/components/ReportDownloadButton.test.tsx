import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AUDIT_PACKAGE_NOTE,
  REPORT_SCOPE_NOTE,
  ReportDownloadButton,
} from "@/components/ReportDownloadButton";

const { getAnalysisReportMock, getAnalysisAuditPackageMock } = vi.hoisted(() => ({
  getAnalysisReportMock: vi.fn(),
  getAnalysisAuditPackageMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getAnalysisReport: getAnalysisReportMock,
  getAnalysisAuditPackage: getAnalysisAuditPackageMock,
}));

describe("ReportDownloadButton", () => {
  const createObjectUrlMock = vi.fn(() => "blob:report-test");
  const revokeObjectUrlMock = vi.fn();
  let downloadedFilename: string | null;

  beforeEach(() => {
    vi.restoreAllMocks();
    getAnalysisReportMock.mockReset();
    getAnalysisAuditPackageMock.mockReset();
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
    expect(screen.queryByTestId("report-scope-note")).toBeNull();
  });

  it("opisuje zakres raportu v2: 10 sekcji ze snapshotu, bez scoringu", () => {
    render(<ReportDownloadButton analysisId={5} parcelIdentifier="TEST.5" />);

    const note = screen.getByTestId("report-scope-note");
    expect(note).toHaveTextContent(REPORT_SCOPE_NOTE);
    expect(note).toHaveTextContent(/10 sekcji/);
    expect(note).toHaveTextContent(/zapisanego snapshotu analizy/);
    expect(note).toHaveTextContent(/nie zawiera oceny punktowej/);
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

  describe("pakiet audytowy (BK-505)", () => {
    it("wyłącza pobieranie pakietu, gdy analiza nie została zapisana", () => {
      render(<ReportDownloadButton analysisId={null} parcelIdentifier={null} />);

      expect(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" })).toBeDisabled();
      expect(screen.queryByTestId("audit-package-note")).toBeNull();
    });

    it("opisuje zawartość pakietu i regułę redystrybucji", () => {
      render(<ReportDownloadButton analysisId={5} parcelIdentifier="TEST.5" />);

      const note = screen.getByTestId("audit-package-note");
      expect(note).toHaveTextContent(AUDIT_PACKAGE_NOTE);
      expect(note).toHaveTextContent(/manifest z SHA-256 każdego pliku/);
      expect(note).toHaveTextContent(/nie są kopiowane/);
    });

    it("pobiera pakiet bieżącej analizy, zapisuje go pod bezpieczną nazwą i pokazuje hash", async () => {
      const user = userEvent.setup();
      const zip = new Blob(["PK\u0003\u0004"], { type: "application/zip" });
      getAnalysisAuditPackageMock.mockResolvedValue({
        blob: zip,
        sha256: "ab".repeat(32),
        exporterVersion: "audit-exporter/1.0.0",
      });
      render(
        <ReportDownloadButton analysisId={77} accessToken="token-77" parcelIdentifier="X.77" />,
      );

      await user.click(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" }));

      await waitFor(() => expect(getAnalysisAuditPackageMock).toHaveBeenCalledOnce());
      expect(getAnalysisAuditPackageMock).toHaveBeenCalledWith(
        77,
        expect.objectContaining({ accessToken: "token-77", signal: expect.any(AbortSignal) }),
      );
      expect(getAnalysisReportMock).not.toHaveBeenCalled();
      await waitFor(() => expect(downloadedFilename).toBe("analiza_77_pakiet_audytowy.zip"));
      expect(createObjectUrlMock).toHaveBeenCalledWith(zip);
      expect(revokeObjectUrlMock).toHaveBeenCalledWith("blob:report-test");
      expect(await screen.findByTestId("audit-package-hash")).toHaveTextContent("ab".repeat(32));
    });

    it("pokazuje błąd pakietu niezależnie od raportu i pozwala ponowić", async () => {
      const user = userEvent.setup();
      getAnalysisAuditPackageMock.mockRejectedValueOnce(
        new Error("Pakiet audytowy tej analizy przekracza dopuszczalny rozmiar."),
      );
      render(<ReportDownloadButton analysisId={18} parcelIdentifier={null} />);

      await user.click(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" }));

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(/przekracza dopuszczalny rozmiar/);
      expect(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Pobierz raport PDF" })).toBeEnabled();
      expect(screen.queryByTestId("audit-package-hash")).toBeNull();

      getAnalysisAuditPackageMock.mockResolvedValueOnce({
        blob: new Blob(["PK"]),
        sha256: null,
        exporterVersion: null,
      });
      await user.click(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" }));
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
      expect(screen.queryByTestId("audit-package-hash")).toBeNull();
    });

    it("używa komunikatu zastępczego dla błędu bez treści", async () => {
      const user = userEvent.setup();
      getAnalysisAuditPackageMock.mockRejectedValueOnce("coś poszło nie tak");
      render(<ReportDownloadButton analysisId={19} parcelIdentifier={null} />);

      await user.click(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Nie udało się pobrać pakietu audytowego.",
      );
    });

    it("anuluje przygotowanie pakietu przy odmontowaniu i nie miesza stanów przycisków", async () => {
      const user = userEvent.setup();
      let auditSignal: AbortSignal | undefined;
      getAnalysisAuditPackageMock.mockImplementation(
        (_id: number, options: { signal?: AbortSignal }) => {
          auditSignal = options.signal;
          return new Promise(() => undefined);
        },
      );
      const { unmount } = render(<ReportDownloadButton analysisId={9} parcelIdentifier="TEST.9" />);

      await user.click(screen.getByRole("button", { name: "Pobierz pakiet audytowy (ZIP)" }));
      expect(screen.getByRole("button", { name: "Przygotowanie pakietu…" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Pobierz raport PDF" })).toBeEnabled();
      expect(auditSignal?.aborted).toBe(false);
      unmount();

      expect(auditSignal?.aborted).toBe(true);
    });
  });
});
