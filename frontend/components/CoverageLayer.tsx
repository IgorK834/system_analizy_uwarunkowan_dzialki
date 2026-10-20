"use client";

import { useEffect, useState } from "react";
import type { Map } from "maplibre-gl";
import { getCoverage, requestPogImport } from "@/lib/api";
import type { CoverageResponse } from "@/lib/types";
import { BdotImportRequest } from "@/components/BdotImportRequest";

const SOURCE = "data-coverage";
const FILL = "data-coverage-fill";
const LINE = "data-coverage-line";
const COLORS = { binding: "#15803d", project: "#eab308", no_act_in_ru: "#cbd5e1", no_local_data: "#b45309", unknown: "#6b7280" };
const LABELS = { binding: "Uchwalony — dane lokalne", project: "Projekt — dane lokalne", no_act_in_ru: "Brak aktu w wykazie RU (≠ brak planu)", no_local_data: "Brak lokalnych danych (≠ brak planu)", unknown: "Status aktu nieustalony" };

/** PRG jest wyłącznie podglądem granic; kolor nie służy do analizy przecięć. */
export function CoverageLayer({ map }: { map: Map | null }) {
  const [enabled, setEnabled] = useState(false);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setCoverage(null);
    setError(false);
    void getCoverage({ signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setCoverage(value);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [enabled, attempt]);

  useEffect(() => {
    if (!enabled || !map || !coverage) return;
    const add = () => {
      if (!map.getSource(SOURCE)) map.addSource(SOURCE, { type: "geojson", data: coverage.communes });
      if (!map.getLayer(FILL)) map.addLayer({ id: FILL, type: "fill", source: SOURCE,
        paint: { "fill-color": ["match", ["get", "state"], ...Object.entries(COLORS).flat(), COLORS.unknown] as never, "fill-opacity": 0.3 } });
      if (!map.getLayer(LINE)) map.addLayer({ id: LINE, type: "line", source: SOURCE, paint: { "line-color": "#64748b", "line-width": 0.7 } });
    };
    if (map.isStyleLoaded()) add();
    map.on("style.load", add);
    return () => {
      map.off("style.load", add);
      if (map.getLayer(LINE)) map.removeLayer(LINE);
      if (map.getLayer(FILL)) map.removeLayer(FILL);
      if (map.getSource(SOURCE)) map.removeSource(SOURCE);
    };
  }, [enabled, map, coverage]);

  return <section aria-label="Pokrycie danych" className="pog-coverage-panel">
    <label><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Pokrycie</label>
    {enabled && <>
      {!coverage && !error && <p role="status">Wczytywanie pokrycia…</p>}
      {error && <p role="alert">Pokrycie niedostępne — stan nieustalony. <button type="button" onClick={() => setAttempt((n) => n + 1)}>Ponów</button></p>}
      <ul aria-label="Legenda pokrycia">{Object.entries(LABELS).map(([key, label]) => <li key={key}><span style={{ color: COLORS[key as keyof typeof COLORS] }}>■</span> {label}</li>)}</ul>
      {coverage && <>
        <p className="field-hint">{coverage.boundaries_note}</p>
        <p className="field-hint">Status prawny dotyczy lokalnego wydania. Data importu i „stan na” mają różne znaczenie.</p>
        <details><summary>Pokrycie BDOT10k (powiaty)</summary>
          {coverage.bdot10k.counties.length === 0 ? <p>Brak lokalnych wydań BDOT10k — nie oznacza to braku dróg lub budynków.</p> :
            <ul>{coverage.bdot10k.counties.map((county) => <li key={county.teryt_powiat}>{county.name ?? county.teryt_powiat}: {county.version_label}, stan na {county.data_as_of ?? "nieznany"}, import {county.imported_at.slice(0, 10)}
              <span> · drogi: {county.road_count ?? "nieustalone"}, budynki: {county.building_count ?? "nieustalone"}, pokrycie terenu: {county.landcover_count ?? "niezaimportowane"}, linie energetyczne: {county.powerline_count ?? "niezaimportowane"}</span>
            </li>)}</ul>}
          <BdotImportRequest />
        </details>
      </>}
    </>}
  </section>;
}

export function ImportRequestButton({ terc }: { terc: string }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [detail, setDetail] = useState("");
  if (!/^\d{6}$/.test(terc)) return null;
  return <span>
    <button type="button" className="secondary-button" disabled={state === "loading" || state === "done"} onClick={() => {
      setState("loading");
      void requestPogImport(terc).then((result) => { setDetail(result.detail); setState(["queued", "running"].includes(result.status) ? "done" : "error"); })
        .catch(() => { setDetail("Nie zlecono importu — sprawdź pokrycie i dostępność RU lub ponów próbę."); setState("error"); });
    }}>{state === "loading" ? "Zlecanie…" : state === "done" ? "Zlecenie przyjęte" : "poproś o import"}</button>
    {detail && <span role={state === "error" ? "alert" : "status"}>{detail}</span>}
  </span>;
}
