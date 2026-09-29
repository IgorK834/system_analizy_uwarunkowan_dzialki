import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  POG_MAP_PREFERENCES_KEY,
  usePogMapPreferences,
  usePogTileRelease,
} from "@/hooks/usePogTileRelease";
import { getActivePogTileRelease } from "@/lib/api";
import { buildPogRelease } from "@/test/pogFixtures";

vi.mock("@/lib/api", () => ({ getActivePogTileRelease: vi.fn() }));

const getReleaseMock = vi.mocked(getActivePogTileRelease);

describe("usePogTileRelease", () => {
  beforeEach(() => getReleaseMock.mockReset());

  it("pobiera wydanie raz i nie odświeża go w trakcie sesji", async () => {
    const release = buildPogRelease();
    getReleaseMock.mockResolvedValue(release);
    const { result, rerender } = renderHook(() => usePogTileRelease());
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current).toMatchObject({ status: "available", release }));
    expect(result.current.status === "available" && result.current.checkedAt).toBeTruthy();
    rerender();
    expect(getReleaseMock).toHaveBeenCalledOnce();
  });

  it("BK-406: ponowienie bez zmiany wydania zachowuje obiekt; nieudane daje stale z datą", async () => {
    const release = buildPogRelease();
    getReleaseMock.mockResolvedValueOnce(release);
    const { result } = renderHook(() => usePogTileRelease());
    await waitFor(() => expect(result.current.status).toBe("available"));
    const firstCheck = result.current.status === "available" ? result.current.checkedAt : null;

    // To samo wydanie (nowy obiekt JSON) — źródło mapy nie może się przeładować.
    getReleaseMock.mockResolvedValueOnce(buildPogRelease());
    act(() => result.current.retry());
    expect(result.current.retrying).toBe(true);
    // Podczas ponowienia dotychczasowe dane pozostają.
    expect(result.current.release).toBe(release);
    await waitFor(() => expect(result.current.retrying).toBe(false));
    expect(result.current.release).toBe(release);

    getReleaseMock.mockRejectedValueOnce(new Error("503"));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("stale"));
    expect(result.current).toMatchObject({ status: "stale", release, reason: "refresh_failed" });
    expect(result.current.status === "stale" && result.current.checkedAt >= (firstCheck ?? "")).toBe(true);

    // Jawne ponowienie może przełączyć na nowsze wydanie.
    const newer = buildPogRelease({ release_id: 43, version_label: "pog-new" });
    getReleaseMock.mockResolvedValueOnce(newer);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.release).toBe(newer));
    expect(result.current.status).toBe("available");

    getReleaseMock.mockResolvedValueOnce(null);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("no_release"));
    getReleaseMock.mockRejectedValueOnce(new Error("503"));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("error"));
  });

  it("odróżnia brak lokalnego wydania od błędu warstwy", async () => {
    getReleaseMock.mockResolvedValueOnce(null);
    const missing = renderHook(() => usePogTileRelease());
    await waitFor(() => expect(missing.result.current.status).toBe("no_release"));

    getReleaseMock.mockRejectedValueOnce(new Error("503"));
    const failed = renderHook(() => usePogTileRelease());
    await waitFor(() => expect(failed.result.current.status).toBe("error"));
  });

  it("ignoruje odpowiedź po odmontowaniu", async () => {
    let resolve: (value: null) => void = () => undefined;
    getReleaseMock.mockReturnValueOnce(new Promise((done) => (resolve = done)));
    const { result, unmount } = renderHook(() => usePogTileRelease());
    unmount();
    resolve(null);
    await Promise.resolve();
    expect(result.current.status).toBe("loading");
  });

  it("ignoruje błąd po odmontowaniu", async () => {
    let reject: (error: Error) => void = () => undefined;
    getReleaseMock.mockReturnValueOnce(new Promise((_, fail) => (reject = fail)));
    const { result, unmount } = renderHook(() => usePogTileRelease());
    unmount();
    reject(new Error("x"));
    await Promise.resolve();
    expect(result.current.status).toBe("loading");
  });
});

describe("usePogMapPreferences", () => {
  it("zapisuje i odtwarza tryb oraz filtr statusu", async () => {
    const first = renderHook(() => usePogMapPreferences());
    act(() => first.result.current.setTheme("height"));
    act(() => first.result.current.setStatusFilter("non_binding"));
    expect(JSON.parse(window.localStorage.getItem(POG_MAP_PREFERENCES_KEY) ?? "{}")).toEqual({
      theme: "height",
      statusFilter: "non_binding",
    });
    first.unmount();

    const second = renderHook(() => usePogMapPreferences());
    await waitFor(() => expect(second.result.current.theme).toBe("height"));
    expect(second.result.current.statusFilter).toBe("non_binding");
  });

  it("odrzuca nieznane i uszkodzone wartości", async () => {
    window.localStorage.setItem(
      POG_MAP_PREFERENCES_KEY,
      JSON.stringify({ theme: "area", statusFilter: "draft" }),
    );
    const invalid = renderHook(() => usePogMapPreferences());
    await waitFor(() => expect(invalid.result.current.theme).toBe("zones"));
    expect(invalid.result.current.statusFilter).toBe("all");

    window.localStorage.setItem(POG_MAP_PREFERENCES_KEY, "{uszkodzony");
    const broken = renderHook(() => usePogMapPreferences());
    await waitFor(() => expect(broken.result.current.theme).toBe("zones"));
  });

  it("działa w pamięci, gdy localStorage odmawia zapisu", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    const { result } = renderHook(() => usePogMapPreferences());
    act(() => result.current.setTheme("intensity"));
    expect(result.current.theme).toBe("intensity");
    setItem.mockRestore();
  });
});
