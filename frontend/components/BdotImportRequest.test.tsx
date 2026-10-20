import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { requestBdotImport } from "@/lib/api";
import { BdotImportRequest } from "@/components/BdotImportRequest";

vi.mock("@/lib/api", () => ({ requestBdotImport: vi.fn() }));
beforeEach(() => { vi.mocked(requestBdotImport).mockReset(); });

it("validates a county and confirms the queued request", async () => {
  vi.mocked(requestBdotImport).mockResolvedValue({ job_id: 1, status: "queued", detail: "Import w kolejce" });
  render(<BdotImportRequest />);
  const button = screen.getByRole("button", { name: "Poproś o import BDOT10k" });
  expect(button).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/Powiat do importu/), "1261");
  await userEvent.click(button);
  expect(await screen.findByRole("status")).toHaveTextContent("Import w kolejce");
  expect(requestBdotImport).toHaveBeenCalledWith("1261");
});

it("allows retry after an unavailable queue", async () => {
  vi.mocked(requestBdotImport).mockRejectedValue(new Error("offline"));
  render(<BdotImportRequest />);
  await userEvent.type(screen.getByLabelText(/Powiat do importu/), "1261");
  await userEvent.click(screen.getByRole("button"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Nie potwierdzono zlecenia");
  await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
});
