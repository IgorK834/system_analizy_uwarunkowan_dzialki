"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getCurrentPogTileState } from "@/lib/api";
import type { PogStatusFilter } from "@/lib/pogLayers";
import { POG_STATUS_FILTERS } from "@/lib/pogLayers";
import { DEFAULT_POG_THEME, isPogThemeId, type PogThemeId } from "@/lib/pogThemes";
import type { PogStateStatus, PogTileState } from "@/lib/types";

export type { PogStateStatus } from "@/lib/types";

export type PogStateController = PogStateStatus & {
  /** Jawne ponowienie pobrania metadanych (przycisk „Ponów”), nie odświeżanie w tle. */
  retry: () => void;
  retrying: boolean;
};

/**
 * Pobiera metadane bieżącego stanu krajowego POG raz na sesję mapy (BK-401, ADR-026).
 *
 * URL-e kafli są przypięte do `state_id` z tej odpowiedzi; import kolejnych gmin w tle nie podmienia źródeł w
 * trakcie sesji — inaczej część kafli pochodziłaby z innego stanu danych niż reszta mapy. Nowszy stan pojawia się
 * wyłącznie po jawnym ponowieniu (BK-406).
 *
 * Ponowienie nie usuwa ostatnich danych bez informacji: jeśli się nie uda, a wcześniej wczytano stan, wynik przechodzi
 * w `stale` z tym samym stanem i datą ostatniego potwierdzenia; ponowne potwierdzenie tego samego stanu zachowuje ten
 * sam obiekt (źródła mapy nie są odtwarzane).
 */
export function usePogTileState(): PogStateController {
  const [state, setState] = useState<PogStateStatus>({ status: "loading", state: null });
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const lastGood = useRef<{ state: PogTileState; checkedAt: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getCurrentPogTileState({ signal: controller.signal })
      .then((fetched) => {
        if (controller.signal.aborted) return;
        setRetrying(false);
        if (!fetched) {
          lastGood.current = null;
          setState({ status: "no_data", state: null });
          return;
        }
        const previous = lastGood.current?.state;
        const pinned = previous && previous.state_id === fetched.state_id ? previous : fetched;
        const checkedAt = new Date().toISOString();
        lastGood.current = { state: pinned, checkedAt };
        setState({ status: "available", state: pinned, checkedAt });
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setRetrying(false);
        const previous = lastGood.current;
        setState(
          previous
            ? {
                status: "stale",
                state: previous.state,
                checkedAt: previous.checkedAt,
                reason: "refresh_failed",
              }
            : { status: "error", state: null },
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
