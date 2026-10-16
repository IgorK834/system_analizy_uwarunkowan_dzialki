"use client";

import { type FormEvent, useCallback, useEffect, useId, useMemo, useState } from "react";

import { getPogCommuneState, getPogCoverage } from "@/lib/api";
import {
  COMMUNE_STATUS_EXPLANATIONS,
  COMMUNE_STATUS_LABELS,
  COVERAGE_FILTER_LABELS,
  type CoverageFilter,
  catalogFreshnessText,
  communeDetail,
  coverageSummaryText,
  filterCommunes,
} from "@/lib/pogCoverage";
import type { PogCommuneLookup, PogCoverage } from "@/lib/types";

const PAGE_SIZE = 25;

type LoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; coverage: PogCoverage }
  | { status: "error" };

type LookupState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; lookup: PogCommuneLookup }
  | { status: "error" };

/**
 * Pokrycie importu planów ogólnych (issue 301): ile gmin ma plan w Rejestrze Urbanistycznym, które są zaimportowane
 * (wersja, stan, data) i które czekają na import. Lista jest pobierana dopiero po rozwinięciu panelu. Sprawdzenie
 * gminy po kodzie TERC rozróżnia cztery rozłączne stany; żaden z nich nie jest komunikatem „brak planu”.
 */
export function PogCoveragePanel() {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<LoadState>({ status: "idle" });
  const [filter, setFilter] = useState<CoverageFilter>("all");
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [attempt, setAttempt] = useState(0);
  const [terc, setTerc] = useState("");
  const [lookup, setLookup] = useState<LookupState>({ status: "idle" });
  const searchId = useId();
  const tercId = useId();

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoad({ status: "loading" });
    void getPogCoverage({ signal: controller.signal })
      .then((coverage) => {
        if (!controller.signal.aborted) setLoad({ status: "ready", coverage });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoad({ status: "error" });
      });
    return () => controller.abort();
  }, [open, attempt]);

  const communes = useMemo(
    () => (load.status === "ready" ? filterCommunes(load.coverage.communes, filter, query) : []),
    [load, filter, query],
  );

  const submitLookup = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      const code = terc.trim();
      if (!/^\d{6}$/.test(code)) {
        setLookup({ status: "error" });
        return;
      }
      setLookup({ status: "loading" });
      void getPogCommuneState(code)
        .then((result) => setLookup({ status: "ready", lookup: result }))
        .catch(() => setLookup({ status: "error" }));
    },
    [terc],
  );

  return (
    <details
      className="pog-coverage-panel"
      data-testid="pog-coverage-panel"
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>Pokrycie importu planów ogólnych (gminy)</summary>

      {load.status === "loading" && <p className="layer-toggle-status">Wczytywanie pokrycia importu…</p>}
      {load.status === "error" && (
        <p className="layer-toggle-status" role="alert">
          Nie udało się pobrać pokrycia importu — stan gmin jest nieustalony (to nie oznacza braku planów).{" "}
          <button type="button" className="secondary-button" onClick={() => setAttempt((value) => value + 1)}>
            Ponów
          </button>
        </p>
      )}

      {load.status === "ready" && (
        <>
          <p className="pog-coverage-summary" data-testid="pog-coverage-summary">
            {coverageSummaryText(load.coverage)}.
          </p>
          <p className="layer-toggle-status" data-testid="pog-coverage-freshness">
            {catalogFreshnessText(load.coverage)}
          </p>

          <div className="pog-coverage-filters">
            <label htmlFor={searchId}>Szukaj gminy, powiatu lub kodu TERC</label>
            <input
              id={searchId}
              type="search"
              value={query}
              maxLength={60}
              onChange={(event) => {
                setQuery(event.target.value);
                setVisible(PAGE_SIZE);
              }}
            />
            <fieldset>
              <legend>Zakres listy</legend>
              {(Object.keys(COVERAGE_FILTER_LABELS) as CoverageFilter[]).map((item) => (
                <label key={item}>
                  <input
                    type="radio"
                    name="pog-coverage-filter"
                    value={item}
                    checked={filter === item}
                    onChange={() => {
                      setFilter(item);
                      setVisible(PAGE_SIZE);
                    }}
                  />
                  {COVERAGE_FILTER_LABELS[item]}
                </label>
              ))}
            </fieldset>
          </div>

          {communes.length === 0 ? (
            <p className="layer-toggle-status" data-testid="pog-coverage-empty">
              Brak gmin spełniających kryteria na liście wykazu RU. To nie jest informacja o braku planu — sprawdź gminę
              po kodzie TERC poniżej.
            </p>
          ) : (
            <ul className="pog-coverage-list" data-testid="pog-coverage-list">
              {communes.slice(0, visible).map((commune) => (
                <li
                  key={commune.terc}
                  className="pog-coverage-item"
                  data-testid="pog-coverage-item"
                  data-status={commune.status}
                >
                  <strong>{commune.name ?? `gmina ${commune.terc}`}</strong>
                  <span className="pog-coverage-place">
                    {" "}
                    {[commune.district, commune.voivodeship].filter(Boolean).join(", ")} · TERC {commune.terc}
                  </span>
                  <span className={`pog-coverage-chip pog-coverage-chip-${commune.status}`}>
                    {COMMUNE_STATUS_LABELS[commune.status]}
                  </span>
                  {communeDetail(commune) && <small className="pog-coverage-detail">{communeDetail(commune)}</small>}
                </li>
              ))}
            </ul>
          )}
          {communes.length > visible && (
            <button type="button" className="secondary-button" onClick={() => setVisible((value) => value + PAGE_SIZE)}>
              Pokaż więcej ({communes.length - visible})
            </button>
          )}
        </>
      )}

      <form className="pog-coverage-lookup" onSubmit={submitLookup} noValidate>
        <label htmlFor={tercId}>Sprawdź gminę po kodzie TERC (6 cyfr)</label>
        <input
          id={tercId}
          inputMode="numeric"
          pattern="\d{6}"
          maxLength={6}
          value={terc}
          onChange={(event) => setTerc(event.target.value)}
          aria-describedby={`${tercId}-result`}
        />
        <button type="submit" className="secondary-button" disabled={lookup.status === "loading"}>
          Sprawdź
        </button>
        <p id={`${tercId}-result`} className="layer-toggle-status" aria-live="polite" data-testid="pog-coverage-lookup">
          {lookup.status === "loading" && "Sprawdzanie…"}
          {lookup.status === "error" &&
            "Podaj sześciocyfrowy kod TERC gminy albo spróbuj ponownie — stan gminy jest nieustalony."}
          {lookup.status === "ready" && (
            <>
              <strong>{COMMUNE_STATUS_LABELS[lookup.lookup.status]}.</strong> {COMMUNE_STATUS_EXPLANATIONS[lookup.lookup.status]}
            </>
          )}
        </p>
      </form>
    </details>
  );
}
