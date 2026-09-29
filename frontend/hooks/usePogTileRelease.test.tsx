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
    await waitFor(() => expect(result.current).toEqual({ status: "available", release }));
    rerender();
    expect(getReleaseMock).toHaveBeenCalledOnce();
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
