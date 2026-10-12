import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MpzpStatusBanner } from "@/components/MpzpStatusBanner";
import { MPZP_ACTS_CASES } from "@/test/mpzpActsFixtures";

describe("MpzpStatusBanner (AU-103)", () => {
  it.each(Object.entries(MPZP_ACTS_CASES))("stan %s ma własny kod, etykietę i komunikat", (key, value) => {
    render(<MpzpStatusBanner status={value.mpzp_status} />);

    const banner = screen.getByTestId("mpzp-status");
    expect(banner).toHaveAttribute("data-status", key);
    expect(banner).toHaveAttribute("data-reason", value.mpzp_status.reason_code);
    expect(screen.getByTestId("mpzp-status-label")).toHaveTextContent(value.mpzp_status.label);
    expect(screen.getByTestId("mpzp-status-message")).toHaveTextContent(value.mpzp_status.message);
    expect(banner).toHaveTextContent(value.mpzp_status.reason_code);
    expect(banner).not.toHaveTextContent("Nie sprawdzono albo nie znaleziono");
  });

  it("pokazuje stan obu kanałów, w tym błąd KIMPZP przy aktach z RU", () => {
    render(<MpzpStatusBanner status={MPZP_ACTS_CASES.available.mpzp_status} />);

    const channels = screen.getByTestId("mpzp-status-channels");
    expect(channels.querySelector('[data-channel="kimpzp"]')).toHaveAttribute("data-channel-status", "unavailable");
    expect(channels.querySelector('[data-channel="mpzp_ru"]')).toHaveAttribute("data-channel-status", "available");
    expect(channels).toHaveTextContent("Rejestr Urbanistyczny (MRiT): wskazał akt (akty: 1)");
  });

  it("niezgodność kanałów jest widoczna", () => {
    const base = MPZP_ACTS_CASES.available.mpzp_status;
    render(<MpzpStatusBanner status={{ ...base, channel_agreement: "mismatch" }} />);
    expect(screen.getByTestId("mpzp-status-agreement")).toHaveTextContent("MPZP_CHANNEL_MISMATCH");
  });

  it("zapis sprzed AU-103 pokazuje osobny komunikat i brak kodów", () => {
    render(<MpzpStatusBanner status={null} />);

    expect(screen.getByTestId("mpzp-status")).toHaveAttribute("data-status", "missing");
    expect(screen.getByTestId("mpzp-status-message")).toHaveTextContent("przed wprowadzeniem stanów MPZP");
    expect(screen.queryByTestId("mpzp-status-channels")).toBeNull();
  });

  it("stan bez kanałów nie pokazuje pustej listy", () => {
    render(<MpzpStatusBanner status={{ ...MPZP_ACTS_CASES.unknown.mpzp_status, channels: [] }} />);
    expect(screen.queryByTestId("mpzp-status-channels")).toBeNull();
  });
});
