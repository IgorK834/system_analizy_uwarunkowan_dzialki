"use client";

import { useEffect, useState, type FormEvent } from "react";

import { MpzpRasterPreview } from "@/components/MpzpRasterPreview";
import { getPreviewSources } from "@/lib/api";
import { getApiResourceUrl } from "@/lib/config";
import { formatPlDate } from "@/lib/pogStatus";
import { shortSha, verifiedHttpsHref } from "@/lib/safeLink";
import type { AnalyzeResponse, PreviewSource } from "@/lib/types";
import {
  DEFAULT_ZONE_SYMBOL_MAX_LENGTH,
  ZONE_SYMBOL_MAX_RAW_LENGTH,
  canonicalizeZoneSymbol,
  sameZoneSymbol,
  validateZoneSymbol,
} from "@/lib/zoneSymbol";

export type ManualZonePanelProps = {
  result: AnalyzeResponse;
  loading: boolean;
  error: string | null;
  onSubmit: (zoneSymbol: string) => Promise<boolean>;
};

const DOCUMENT_STATUS_NOTE = {
  unavailable:
    "Dokumentu uchwały nie udało się pobrać przy wstrzymaniu analizy — po podaniu symbolu parametry strefy pozostaną nieustalone.",
  not_provided:
    "Discovery nie wskazało dokumentu uchwały — po podaniu symbolu parametry strefy pozostaną nieustalone.",
} as const;

/**
 * Tryb ręcznego wskazania strefy MPZP (BK-204). Użytkownik najpierw widzi
 * obraz źródłowy, identyfikator planu, przypięty dokument i kandydatów, a
 * dopiero potem formularz. Wysłanie wymaga potwierdzenia porównania, a symbol
 * jest walidowany tymi samymi regułami co API.
 */
export function ManualZonePanel({ result, loading, error, onSubmit }: ManualZonePanelProps) {
  const context = result.manual_zone_context ?? null;
  const [symbol, setSymbol] = useState("");
  const [symbolError, setSymbolError] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [sources, setSources] = useState<PreviewSource[]>([]);
  const [sourcesState, setSourcesState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    const controller = new AbortController();
    void getPreviewSources({ signal: controller.signal })
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setSources(loaded);
        setSourcesState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setSourcesState("error");
      });
    return () => controller.abort();
  }, []);

  const maxLength = context?.symbol_max_length ?? DEFAULT_ZONE_SYMBOL_MAX_LENGTH;
  const candidates = context?.candidate_zone_symbols ?? [];
  const previewKey = context?.raster_preview_source_key ?? "mpzp";
  const rasterSource = sources.find((source) => source.source_key === previewKey);
  const document = context?.document ?? null;
  // Porównanie z kandydatami jest tolerancyjne na odstępy i wielkość liter (`146MN` = `146 MN`).
  const canonical = canonicalizeZoneSymbol(symbol);
  const outsideCandidates =
    canonical !== "" &&
    candidates.length > 0 &&
    !candidates.some((candidate) => sameZoneSymbol(candidate, canonical));
  const disabled = loading || !result.analysis_id;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!result.analysis_id) {
      setSymbolError("Brak identyfikatora analizy — nie można wznowić analizy. Uruchom analizę ponownie.");
      return;
    }
    const validation = validateZoneSymbol(symbol, {
      maxLength,
      pattern: context?.symbol_allowed_pattern,
    });
    if (!validation.ok) {
      setSymbolError(validation.error);
      return;
    }
    if (!reviewed) {
      setSymbolError("Potwierdź porównanie symbolu z obrazem źródłowym i dokumentem planu.");
      return;
    }
    setSymbolError(null);
    if (await onSubmit(validation.value)) setSymbol("");
  };

  return (
    <form
      className="analysis-card manual-zone-form"
      onSubmit={handleSubmit}
      aria-label="Ręczne podanie symbolu strefy MPZP"
      noValidate
    >
      <h3>Ręczne podanie symbolu strefy MPZP</h3>
      <p className="instruction">
        {context?.notice ??
          "Gmina nie udostępnia wektorowych danych MPZP. Odczytaj symbol strefy z podglądu rastrowego."}
      </p>

      <section aria-label="Obraz źródłowy planu">
        <h4>1. Obraz źródłowy</h4>
        <MpzpRasterPreview
          parcelGeojson={result.parcel?.geometry_geojson}
          source={rasterSource}
          sourcesState={sourcesState}
        />
      </section>

      <section aria-label="Plan i dokument źródłowy">
        <h4>2. Plan i dokument</h4>
        <dl className="result-summary">
          <div>
            <dt>Identyfikator planu</dt>
            <dd data-testid="manual-zone-plan-id">{context?.plan_id ?? "nieustalony"}</dd>
          </div>
          <div>
            <dt>Dokument uchwały</dt>
            <dd>
              {document ? (
                <>
                  <a
                    href={getApiResourceUrl(document.preview_path)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Otwórz przypiętą kopię ({document.filename ?? "dokument"})
                  </a>
                  <span className="field-hint mono" title={document.sha256}>
                    {" "}SHA-256 {shortSha(document.sha256)}
                    {document.fetched_at ? ` · pobrano ${formatPlDate(document.fetched_at)}` : ""}
                  </span>
                </>
              ) : (
                <span className="manual-review">
                  {DOCUMENT_STATUS_NOTE[context?.document_status === "not_provided" ? "not_provided" : "unavailable"]}
                </span>
              )}
            </dd>
          </div>
          {document?.requested_url && (
            <div>
              <dt>Adres źródłowy</dt>
              <dd className="mono">
                <SourceUrl url={document.requested_url} verified={document.requested_url_verified} />
              </dd>
            </div>
          )}
        </dl>
        {document && (
          <p className="field-hint">
            Parametry zostaną odczytane z tej przypiętej kopii — system nie pobierze
            dokumentu ponownie, nawet jeśli pod tym adresem pojawi się inna uchwała.
          </p>
        )}
      </section>

      <section aria-label="Kandydaci symboli">
        <h4>3. Kandydaci symboli</h4>
        {candidates.length > 0 ? (
          <ul className="candidate-list">
            {candidates.map((candidate) => (
              <li key={candidate}>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={disabled}
                  aria-pressed={canonical !== "" && sameZoneSymbol(canonical, candidate)}
                  onClick={() => {
                    setSymbol(candidate);
                    setSymbolError(null);
                  }}
                >
                  {candidate}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="field-hint">Discovery nie wskazało kandydatów symboli dla tego planu.</p>
        )}
      </section>

      <label htmlFor="manual-zone-symbol">Symbol strefy</label>
      <input
        id="manual-zone-symbol"
        value={symbol}
        maxLength={ZONE_SYMBOL_MAX_RAW_LENGTH}
        disabled={disabled}
        aria-invalid={Boolean(symbolError)}
        aria-describedby="manual-zone-symbol-hint manual-zone-symbol-error"
        placeholder={candidates[0] ? `Np. ${candidates[0]}` : "Np. 230_U"}
        onChange={(event) => {
          setSymbol(event.target.value);
          setSymbolError(null);
        }}
      />
      <p id="manual-zone-symbol-hint" className="field-hint">
        Ręcznie podany symbol nie ustala udziału strefy w powierzchni działki.
        Wynik pozostanie częściowy, a każdy zależny parametr będzie wymagał weryfikacji.
      </p>
      {outsideCandidates && (
        <p className="manual-review" role="note">
          Symbol spoza kandydatów discovery — sprawdź go z rysunkiem planu.
        </p>
      )}
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={reviewed}
          disabled={disabled}
          onChange={(event) => {
            setReviewed(event.target.checked);
            setSymbolError(null);
          }}
        />
        Potwierdzam porównanie symbolu z obrazem źródłowym, planem i dokumentem.
      </label>
      {symbolError && (
        <p id="manual-zone-symbol-error" className="field-error">
          {symbolError}
        </p>
      )}
      {error && <p className="field-error">{error}</p>}
      <button type="submit" className="primary-button" disabled={disabled || !reviewed}>
        {loading ? "Wznawiam analizę…" : "Wznów analizę"}
      </button>
    </form>
  );
}

function SourceUrl({ url, verified }: { url: string; verified: boolean }) {
  const href = verifiedHttpsHref(url, verified);
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {url}
    </a>
  ) : (
    <>{url}</>
  );
}
