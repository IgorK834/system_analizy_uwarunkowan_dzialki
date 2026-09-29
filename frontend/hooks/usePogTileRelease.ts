"use client";

import { useCallback, useEffect, useState } from "react";

import { getActivePogTileRelease } from "@/lib/api";
import type { PogStatusFilter } from "@/lib/pogLayers";
import { POG_STATUS_FILTERS } from "@/lib/pogLayers";
import { DEFAULT_POG_THEME, isPogThemeId, type PogThemeId } from "@/lib/pogThemes";
import type { PogTileRelease } from "@/lib/types";

export type PogReleaseState =
  | { status: "loading"; release: null }
  | { status: "available"; release: PogTileRelease }
  | { status: "no_release"; release: null }
  | { status: "error"; release: null };

/**
 * Pobiera metadane aktywnego wydania POG dokładnie raz na sesję mapy (BK-401).
 *
 * URL kafli jest przypięty do `release_id` z tej odpowiedzi; aktywowanie nowego
 * wydania w tle nie podmienia źródła w trakcie sesji — inaczej część kafli
 * pochodziłaby z innego stanu danych niż reszta mapy.
 */
export function usePogTileRelease(): PogReleaseState {
  const [state, setState] = useState<PogReleaseState>({ status: "loading", release: null });

  useEffect(() => {
    const controller = new AbortController();
    void getActivePogTileRelease({ signal: controller.signal })
      .then((release) => {
        if (controller.signal.aborted) return;
        setState(
          release ? { status: "available", release } : { status: "no_release", release: null },
        );
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setState({ status: "error", release: null });
      });
    return () => controller.abort();
  }, []);

  return state;
}

export const POG_MAP_PREFERENCES_KEY = "dzialki:pog-map:v1";

type StoredPreferences = { theme: PogThemeId; statusFilter: PogStatusFilter };

function readPreferences(): StoredPreferences {
  const fallback: StoredPreferences = { theme: DEFAULT_POG_THEME, statusFilter: "all" };
  try {
    const raw = window.localStorage.getItem(POG_MAP_PREFERENCES_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<StoredPreferences>;
    return {
      theme: isPogThemeId(parsed.theme) ? parsed.theme : fallback.theme,
      statusFilter: POG_STATUS_FILTERS.includes(parsed.statusFilter as PogStatusFilter)
        ? (parsed.statusFilter as PogStatusFilter)
        : fallback.statusFilter,
    };
  } catch {
    return fallback;
  }
}

/** Tryb i filtr statusu POG zachowywane między remontami mapy i odświeżeniami. */
export function usePogMapPreferences() {
  const [preferences, setPreferences] = useState<StoredPreferences>({
    theme: DEFAULT_POG_THEME,
    statusFilter: "all",
  });

  useEffect(() => {
    setPreferences(readPreferences());
  }, []);

  const update = useCallback((patch: Partial<StoredPreferences>) => {
    setPreferences((current) => {
      const next = { ...current, ...patch };
      try {
        window.localStorage.setItem(POG_MAP_PREFERENCES_KEY, JSON.stringify(next));
      } catch {
        // Wybór nadal działa w pamięci bieżącej sesji.
      }
      return next;
    });
  }, []);

  const setTheme = useCallback((theme: PogThemeId) => update({ theme }), [update]);
  const setStatusFilter = useCallback(
    (statusFilter: PogStatusFilter) => update({ statusFilter }),
    [update],
  );

  return {
    theme: preferences.theme,
    statusFilter: preferences.statusFilter,
    setTheme,
    setStatusFilter,
  };
}
