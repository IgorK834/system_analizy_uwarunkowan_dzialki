"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type * as maplibregl from "maplibre-gl";

import { ApiError, searchAddresses } from "@/lib/api";
import type { AddressSearchResult, AnalyzeRequest } from "@/lib/types";

type Tab = "map" | "address" | "parcel";

type SearchPanelProps = {
  loading: boolean;
  onAnalyze: (payload: AnalyzeRequest) => void | Promise<void>;
  map?: maplibregl.Map | null;
};

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "map", label: "Mapa" },
  { id: "address", label: "Adres" },
  { id: "parcel", label: "Identyfikator działki" },
];

const PARCEL_IDENTIFIER_PATTERN = /^[0-9._/]+$/;

const RESULT_TYPE_LABELS: Record<string, string> = {
  city: "Miejscowość",
  street: "Ulica",
  house_number: "Adres",
};

export function SearchPanel({ loading, onAnalyze, map = null }: SearchPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>("map");
  const [addressQuery, setAddressQuery] = useState("");
  const [suggestions, setSuggestions] = useState<AddressSearchResult[]>([]);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [suggestionsError, setSuggestionsError] = useState<string | null>(null);
  const [resolvedSuggestionQuery, setResolvedSuggestionQuery] = useState<
    string | null
  >(null);
  const [parcelIdentifier, setParcelIdentifier] = useState("");
  const [parcelError, setParcelError] = useState<string | null>(null);
  const committedAddressRef = useRef<string | null>(null);
  const addressInputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const suggestionsOpen = activeTab === "address" && suggestions.length > 0;

  // Lista jest popoverem (AU-008): zamykamy ją kliknięciem poza panelem, żeby nie zasłaniała
  // kontrolek mapy, gdy użytkownik przestał z niej korzystać.
  useEffect(() => {
    if (!suggestionsOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) {
        setSuggestions([]);
        setActiveSuggestion(-1);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [suggestionsOpen]);

  useEffect(() => {
    const query = addressQuery.trim();

    if (committedAddressRef.current === query) {
      committedAddressRef.current = null;
      setSuggestions([]);
      setSuggestionsLoading(false);
      setResolvedSuggestionQuery(null);
      return;
    }

    if (query.length < 3) {
      setSuggestions([]);
      setSuggestionsLoading(false);
      setSuggestionsError(null);
      setResolvedSuggestionQuery(null);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSuggestionsLoading(true);
      setSuggestionsError(null);
      setResolvedSuggestionQuery(null);

      try {
        const center = map?.getCenter();
        const bounds = map?.getBounds();
        const response = await searchAddresses(query, {
          signal: controller.signal,
          bias: center ? { lon: center.lng, lat: center.lat } : undefined,
          bbox: bounds
            ? [
                bounds.getWest(),
                bounds.getSouth(),
                bounds.getEast(),
                bounds.getNorth(),
              ]
            : undefined,
        });
        setSuggestions(response.results);
        setResolvedSuggestionQuery(query);
        setActiveSuggestion(-1);
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setSuggestions([]);
        setResolvedSuggestionQuery(null);
        setSuggestionsError(
          caught instanceof ApiError
            ? caught.message
            : "Nie udało się pobrać sugestii adresowych.",
        );
      } finally {
        if (!controller.signal.aborted) setSuggestionsLoading(false);
      }
    }, 300);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [addressQuery, map]);

  const chooseSuggestion = (suggestion: AddressSearchResult) => {
    const isCompleteAddress = suggestion.result_type === "house_number";
    const nextQuery = isCompleteAddress
      ? suggestion.label
      : suggestion.result_type === "city"
        ? `${suggestion.label}, `
        : `${suggestion.label} `;
    committedAddressRef.current = nextQuery.trim();
    setAddressQuery(nextQuery);
    setSuggestions([]);
    setResolvedSuggestionQuery(null);
    setActiveSuggestion(-1);
    if (!isCompleteAddress) {
      window.requestAnimationFrame(() => {
        addressInputRef.current?.focus();
        addressInputRef.current?.setSelectionRange(
          nextQuery.length,
          nextQuery.length,
        );
      });
      return;
    }
    // Wysyłamy DOKŁADNIE wybraną sugestię (współrzędne WGS84 + jej identyfikator),
    // dzięki czemu backend nie geokoduje ponownie i nie wybiera pierwszego wyniku.
    const [lon, lat] = suggestion.point.coordinates;
    void onAnalyze({
      method: "address",
      query: suggestion.label,
      selected_lon: lon,
      selected_lat: lat,
      selected_result_id: suggestion.id,
    });
  };

  const handleAddressKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape" && suggestions.length) {
      event.preventDefault();
      setSuggestions([]);
      setActiveSuggestion(-1);
      return;
    }

    if (event.key === "ArrowDown" && suggestions.length) {
      event.preventDefault();
      setActiveSuggestion((current) =>
        current >= suggestions.length - 1 ? 0 : current + 1,
      );
      return;
    }

    if (event.key === "ArrowUp" && suggestions.length) {
      event.preventDefault();
      setActiveSuggestion((current) =>
        current <= 0 ? suggestions.length - 1 : current - 1,
      );
      return;
    }

    if (event.key === "Enter" && activeSuggestion >= 0) {
      event.preventDefault();
      chooseSuggestion(suggestions[activeSuggestion]);
    }
  };

  const submitParcel = () => {
    const value = parcelIdentifier.trim();
    if (value.length < 5 || !PARCEL_IDENTIFIER_PATTERN.test(value)) {
      setParcelError(
        "Wpisz co najmniej 5 znaków. Dozwolone są cyfry oraz znaki: kropka, podkreślenie i ukośnik.",
      );
      return;
    }

    setParcelError(null);
    void onAnalyze({ method: "parcel_id", parcel_identifier: value });
  };

  return (
    <section
      ref={panelRef}
      className={suggestionsOpen ? "search-panel search-panel-suggesting" : "search-panel"}
      aria-label="Wybór działki do analizy"
    >
      <h2>Znajdź działkę</h2>
      <div className="tabs" role="tablist" aria-label="Metoda wyszukiwania">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`search-${tab.id}`}
            className={activeTab === tab.id ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "map" && (
        <div id="search-map" role="tabpanel" className="panel-content">
          <p className="instruction">
            Kliknij wybrane miejsce na mapie. Współrzędne WGS84 zostaną wysłane
            bezpośrednio do analizy.
          </p>
        </div>
      )}

      {activeTab === "address" && (
        <div id="search-address" role="tabpanel" className="panel-content">
          <label htmlFor="address-query">Adres</label>
          <div className="address-field">
            <input
              ref={addressInputRef}
              id="address-query"
              type="search"
              autoComplete="off"
              value={addressQuery}
              disabled={loading}
              aria-autocomplete="list"
              aria-controls="address-suggestions"
              aria-activedescendant={
                activeSuggestion >= 0
                  ? `address-suggestion-${activeSuggestion}`
                  : undefined
              }
              placeholder="Np. Warszawa, Marszałkowska 1"
              onChange={(event) => setAddressQuery(event.target.value)}
              onKeyDown={handleAddressKeyDown}
            />
            {suggestions.length > 0 && (
              <ul id="address-suggestions" className="suggestions" role="listbox">
                {suggestions.map((suggestion, index) => (
                  <li key={suggestion.id}>
                    <button
                      id={`address-suggestion-${index}`}
                      type="button"
                      role="option"
                      aria-selected={activeSuggestion === index}
                      className={
                        activeSuggestion === index
                          ? "suggestion suggestion-active"
                          : "suggestion"
                      }
                      onMouseEnter={() => setActiveSuggestion(index)}
                      onFocus={() => setActiveSuggestion(index)}
                      onClick={() => chooseSuggestion(suggestion)}
                    >
                      <span>{suggestion.label}</span>
                      <small>
                        {RESULT_TYPE_LABELS[suggestion.result_type] ?? "Lokalizacja"}
                        {suggestion.address_parts.voivodeship
                          ? ` · ${suggestion.address_parts.voivodeship}`
                          : ""}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="field-hint">
            Wybierz konkretną sugestię — samo wpisywanie nie uruchamia analizy.
          </p>
          {suggestionsLoading && <p className="inline-status">Szukam adresów…</p>}
          {suggestionsError && <p className="field-error">{suggestionsError}</p>}
          {!suggestionsLoading &&
            !suggestionsError &&
            suggestions.length === 0 &&
            resolvedSuggestionQuery === addressQuery.trim() && (
              <p className="inline-status" role="status">
                Brak podpowiedzi. Dopisz miejscowość, ulicę albo numer, aby
                zawęzić wyszukiwanie.
              </p>
            )}
        </div>
      )}

      {activeTab === "parcel" && (
        <form
          id="search-parcel"
          role="tabpanel"
          className="panel-content"
          onSubmit={(event) => {
            event.preventDefault();
            submitParcel();
          }}
        >
          <label htmlFor="parcel-identifier">Identyfikator działki</label>
          <input
            id="parcel-identifier"
            value={parcelIdentifier}
            disabled={loading}
            placeholder="Np. 122101_1.0001.1234/2"
            onChange={(event) => {
              setParcelIdentifier(event.target.value);
              setParcelError(null);
            }}
          />
          {parcelError && <p className="field-error">{parcelError}</p>}
          <button className="primary-button" type="submit" disabled={loading}>
            {loading ? "Analizuję…" : "Analizuj działkę"}
          </button>
        </form>
      )}
    </section>
  );
}
