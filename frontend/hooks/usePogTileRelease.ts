"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getActivePogTileRelease } from "@/lib/api";
import type { PogStatusFilter } from "@/lib/pogLayers";
import { POG_STATUS_FILTERS } from "@/lib/pogLayers";
import { DEFAULT_POG_THEME, isPogThemeId, type PogThemeId } from "@/lib/pogThemes";
import type { PogReleaseState, PogTileRelease } from "@/lib/types";

export type { PogReleaseState } from "@/lib/types";

export type PogReleaseController = PogReleaseState & {
  /** Jawne ponowienie pobrania metadanych (przycisk „Ponów”), nie odświeżanie w tle. */
  retry: () => void;
  retrying: boolean;
};

/**
 * Pobiera metadane aktywnego wydania POG raz na sesję mapy (BK-401).
 *
 * URL kafli jest przypięty do `release_id` z tej odpowiedzi; aktywowanie nowego
 * wydania w tle nie podmienia źródła w trakcie sesji — inaczej część kafli
 * pochodziłaby z innego stanu danych niż reszta mapy. Nowe wydanie pojawia się
 * wyłącznie po jawnym ponowieniu (BK-406).
 *
 * Ponowienie nie usuwa ostatnich danych bez informacji: jeśli się nie uda, a
 * wcześniej wczytano wydanie, stan przechodzi w `stale` z tym samym wydaniem i
 * datą ostatniego potwierdzenia; ponowne potwierdzenie tego samego wydania
 * zachowuje ten sam obiekt (źródło mapy nie jest odtwarzane).
 */
export function usePogTileRelease(): PogReleaseController {
  const [state, setState] = useState<PogReleaseState>({ status: "loading", release: null });
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const lastGood = useRef<{ release: PogTileRelease; checkedAt: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getActivePogTileRelease({ signal: controller.signal })
      .then((fetched) => {
        if (controller.signal.aborted) return;
        setRetrying(false);
        if (!fetched) {
          lastGood.current = null;
          setState({ status: "no_release", release: null });
          return;
        }
        const previous = lastGood.current?.release;
        const release =
          previous && previous.release_id === fetched.release_id ? previous : fetched;
        const checkedAt = new Date().toISOString();
        lastGood.current = { release, checkedAt };
        setState({ status: "available", release, checkedAt });
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setRetrying(false);
        const previous = lastGood.current;
        setState(
          previous
            ? {
                status: "stale",
                release: previous.release,
                checkedAt: previous.checkedAt,
                reason: "refresh_failed",
              }
            : { status: "error", release: null },
        );
      });
    return () => controller.abort();
  }, [attempt]);

  const retry = useCallback(() => {
    setRetrying(true);
    setAttempt((value) => value + 1);
  }, []);

  return useMemo(() => ({ ...state, retry, retrying }), [state, retry, retrying]);
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
