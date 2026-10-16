import { describe, expect, it } from "vitest";

import { LAYER_STATES, LAYER_STATE_DESCRIPTIONS, LAYER_STATE_LABELS } from "@/lib/layerState";
import {
  INITIAL_TILE_ACTIVITY,
  type LonLatBounds,
  type PogLayerStatus,
  type PogTileActivity,
  boundsIntersect,
  derivePogLayerStatus,
  formatCheckedAt,
  inspectorEmptyMessage,
  layerMinZoom,
  layersLoadedAtZoom,
  pogLayerStatusMessage,
  pointInCoverage,
  stateDescription,
} from "@/lib/pogLayerState";
import { POG_NON_BINDING_BADGE } from "@/lib/pogZones";
import type { LayerState, PogLegalStatus, PogStateStatus, PogTileState } from "@/lib/types";
import { buildCoverageArea, buildPogState } from "@/test/pogFixtures";

const CHECKED_AT = "2026-09-28T10:15:00Z";
const LOADED: PogTileActivity = { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, anyTileLoaded: true };
const SOPOT_VIEW: LonLatBounds = [18.52, 54.42, 18.58, 54.46];
const ATLANTIC_VIEW: LonLatBounds = [-30, 40, -29, 41];
// „Brak planu” wolno napisać wyłącznie przy urzędowym potwierdzeniu; warstwa
// mapy go nie ma, więc fraza nie może paść w żadnym stanie. Dozwolone jest
// wymagane przez ADR-002 zastrzeżenie „nie oznacza braku planu”.
const FORBIDDEN = /(^|[^a-ząćęłńóśźż])brak planu/i;

function stateWith(status: PogLegalStatus, overrides: Partial<PogTileState> = {}): PogTileState {
  return buildPogState({
    acts_by_legal_status: { [status]: 1 },
    coverage_areas: [buildCoverageArea({ legal_status: status })],
    ...overrides,
  });
}

/** Wejście, które daje dokładnie wskazany stan warstwy. */
function scenario(
  state: LayerState,
  registry: PogTileState,
): Parameters<typeof derivePogLayerStatus>[0] {
  const available: PogStateStatus = { status: "available", state: registry, checkedAt: CHECKED_AT };
  switch (state) {
    case "loading":
      return { state: available, tiles: INITIAL_TILE_ACTIVITY, viewport: SOPOT_VIEW };
    case "available":
      return { state: available, tiles: LOADED, viewport: SOPOT_VIEW };
    case "partial":
      return { state: available, tiles: { ...LOADED, tileErrors: 2 }, viewport: SOPOT_VIEW };
    case "no_coverage":
      return { state: available, tiles: LOADED, viewport: ATLANTIC_VIEW };
    case "error":
      return {
        state: available,
        tiles: { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, tileErrors: 3 },
        viewport: SOPOT_VIEW,
      };
    default:
      return {
        state: { status: "stale", state: registry, checkedAt: CHECKED_AT, reason: "refresh_failed" },
        tiles: { ...LOADED, tileErrors: 1 },
        viewport: SOPOT_VIEW,
      };
  }
}

function allText(status: PogLayerStatus): string {
  const { title, detail } = pogLayerStatusMessage(status);
  const registry = status.registry;
  return [title, detail, registry ? stateDescription(registry) : ""].join(" ");
}

describe("pogLayerState — 6 stanów × status prawny", () => {
  const statuses: PogLegalStatus[] = ["binding", "project", "unknown"];

  for (const legal of statuses) {
    for (const state of LAYER_STATES) {
      it(`${state} przy stanie z aktem ${legal}: odrębny komunikat, plakietka niezależna od stanu, bez „brak planu”`, () => {
        const registry = stateWith(legal);
        const status = derivePogLayerStatus(scenario(state, registry));
        expect(status.state).toBe(state);
        expect(status.registry).toBe(registry);

        const text = allText(status);
        expect(text).not.toMatch(FORBIDDEN);
        expect(text).toContain(`#${registry.state_id}`);

        const badges = status.badges.map((item) => item.badge);
        if (legal === "project") expect(badges).toEqual([POG_NON_BINDING_BADGE]);
        if (legal === "unknown") expect(badges).toEqual(["status nieustalony"]);
        if (legal === "binding") expect(badges).toEqual([]);
      });
    }
  }

  it("każdy stan ma inny tytuł komunikatu, etykietę i opis w legendzie", () => {
    const registry = stateWith("project");
    const titles = LAYER_STATES.map(
      (state) => pogLayerStatusMessage(derivePogLayerStatus(scenario(state, registry))).title,
    );
    expect(new Set(titles).size).toBe(LAYER_STATES.length);
    expect(new Set(Object.values(LAYER_STATE_LABELS)).size).toBe(6);
    for (const description of Object.values(LAYER_STATE_DESCRIPTIONS)) {
      expect(description).not.toMatch(FORBIDDEN);
    }
  });

  it("stan krajowy bez danych: ładowanie, brak zaimportowanych gmin (brak pokrycia) i awaria", () => {
    const base = { tiles: LOADED, viewport: SOPOT_VIEW };
    const loading = derivePogLayerStatus({ ...base, state: { status: "loading", state: null } });
    const missing = derivePogLayerStatus({ ...base, state: { status: "no_data", state: null } });
    const failed = derivePogLayerStatus({ ...base, state: { status: "error", state: null } });
    expect([loading.state, missing.state, failed.state]).toEqual(["loading", "no_coverage", "error"]);
    expect([loading.reason, missing.reason, failed.reason]).toEqual(["state_loading", "no_data", "state_error"]);
    expect(pogLayerStatusMessage(missing).detail).toMatch(/Nie oznacza to braku planu ogólnego/);
    for (const status of [loading, missing, failed]) {
      expect(status.badges).toEqual([]);
      expect(allText(status)).not.toMatch(FORBIDDEN);
    }
  });

  it("stale zachowuje stan i datę ostatniego potwierdzenia; stan niebieżący też jest stale", () => {
    const registry = stateWith("project");
    const stale = derivePogLayerStatus(scenario("stale", registry));
    expect(stale.checkedAt).toBe(CHECKED_AT);
    expect(pogLayerStatusMessage(stale).detail).toContain(formatCheckedAt(CHECKED_AT) as string);
    expect(formatCheckedAt(CHECKED_AT)).toBe("28.09.2026, 12:15");

    const outdated = derivePogLayerStatus({
      state: { status: "available", state: { ...registry, is_current: false }, checkedAt: CHECKED_AT },
      tiles: LOADED,
      viewport: SOPOT_VIEW,
    });
    expect([outdated.state, outdated.reason]).toEqual(["stale", "state_not_current"]);
    expect(pogLayerStatusMessage(outdated).title).toContain("nie jest już bieżący");
  });

  it("pokrycie z metadanych obszaru: pusty kafel w zasięgu to nie no_coverage", () => {
    const registry = stateWith("binding");
    // Źródło wczytane, żaden kafel nie przyniósł cech — ale widok jest w zasięgu.
    const emptyTile = derivePogLayerStatus({
      state: { status: "available", state: registry, checkedAt: CHECKED_AT },
      tiles: { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true },
      viewport: SOPOT_VIEW,
    });
    expect(emptyTile.state).toBe("available");

    // Stan bez coverage_areas: zasięg z obwiedni całości.
    const legacy = buildPogState({ coverage_areas: undefined });
    expect(
      derivePogLayerStatus({
        state: { status: "available", state: legacy, checkedAt: CHECKED_AT },
        tiles: LOADED,
        viewport: ATLANTIC_VIEW,
      }).state,
    ).toBe("no_coverage");
    // Bez żadnych metadanych zasięgu i bez okna mapy nie zgadujemy braku pokrycia.
    expect(
      derivePogLayerStatus({
        state: {
          status: "available",
          state: buildPogState({ coverage_areas: [], bounds: null }),
          checkedAt: CHECKED_AT,
        },
        tiles: LOADED,
        viewport: null,
      }).state,
    ).toBe("available");
  });

  it("niepełne dane aktu w widoku i limit kafla dają partial z przyczyną", () => {
    const incomplete = stateWith("binding", {
      coverage_areas: [
        buildCoverageArea({ is_complete: false, incomplete_reasons: ["missing_area"] }),
        buildCoverageArea({ act_id: "B", has_boundary: false, is_complete: null }),
        buildCoverageArea({ act_id: "poza", bounds: ATLANTIC_VIEW, is_complete: false }),
      ],
    });
    const status = derivePogLayerStatus({
      state: { status: "available", state: incomplete, checkedAt: CHECKED_AT },
      tiles: LOADED,
      viewport: SOPOT_VIEW,
    });
    expect([status.state, status.reason]).toEqual(["partial", "incomplete_data"]);
    expect(status.incompleteActs).toEqual(["PL.ZIPPZP.10011/226401-POG/1POG", "B"]);
    // „poza” leży poza oknem mapy, więc nie wchodzi ani do licznika, ani do mianownika.
    expect(status.visibleActs).toBe(2);
    expect(pogLayerStatusMessage(status).detail).toContain("2 z 2 aktów w widoku");
    expect(pogLayerStatusMessage(status).detail).toContain("B");

    const limit = derivePogLayerStatus({
      state: { status: "available", state: incomplete, checkedAt: CHECKED_AT },
      tiles: { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, tileErrors: 1, tileLimitHit: true },
      viewport: SOPOT_VIEW,
    });
    expect([limit.state, limit.reason]).toEqual(["partial", "tile_limit"]);
    expect(pogLayerStatusMessage(limit).detail).toMatch(/Przybliż/);
  });

  it("mapa niepełna (tabele mapy w uzupełnianiu) to partial z liczbą wersji, a nie available", () => {
    const registry = stateWith("binding", { map_ready: false, versions_without_map_rows: 3 });
    const status = derivePogLayerStatus({
      state: { status: "available", state: registry, checkedAt: CHECKED_AT },
      tiles: LOADED,
      viewport: SOPOT_VIEW,
    });
    expect([status.state, status.reason]).toEqual(["partial", "map_incomplete"]);
    expect(pogLayerStatusMessage(status).detail).toContain("3");
    expect(allText(status)).not.toMatch(FORBIDDEN);
  });

  it("poza zasięgiem zaimportowanych gmin komunikat wskazuje niezaimportowaną gminę, nie brak planu", () => {
    const status = derivePogLayerStatus(scenario("no_coverage", stateWith("binding")));
    expect(status.reason).toBe("outside_coverage");
    const { detail } = pogLayerStatusMessage(status);
    expect(detail).toMatch(/nie został jeszcze zaimportowany/);
    expect(detail).toMatch(/Pokrycie importu/);
  });

  it("wiele niepełnych aktów jest skracane na liście", () => {
    const many = stateWith("binding", {
      coverage_areas: Array.from({ length: 8 }, (_, index) =>
        buildCoverageArea({ act_id: `A${index}`, is_complete: false }),
      ),
    });
    const status = derivePogLayerStatus({
      state: { status: "available", state: many, checkedAt: CHECKED_AT },
      tiles: LOADED,
      viewport: SOPOT_VIEW,
    });
    expect(status.incompleteActs).toHaveLength(8);
    expect(pogLayerStatusMessage(status).detail).toContain("8 z 8 aktów w widoku");
    expect(pogLayerStatusMessage(status).detail).toContain("i 3 kolejnych");
  });

  it("geometria zasięgów", () => {
    expect(boundsIntersect([0, 0, 1, 1], [1, 1, 2, 2])).toBe(true);
    expect(boundsIntersect([0, 0, 1, 1], [1.1, 0, 2, 1])).toBe(false);
    const registry = buildPogState();
    expect(pointInCoverage(registry, 18.55, 54.45)).toBe(true);
    expect(pointInCoverage(registry, 21.0, 52.2)).toBe(false);
    expect(pointInCoverage(buildPogState({ coverage_areas: [], bounds: null }), 0, 0)).toBe(true);
    expect(formatCheckedAt(null)).toBeNull();
    expect(formatCheckedAt("nie-data")).toBe("nie-data");
    expect(stateDescription(buildPogState({ created_at: null, act_count: 1 }))).toBe(
      "Stan krajowy #42 (import), 1 akt",
    );
    expect(stateDescription(buildPogState())).toBe("Stan krajowy #42 (import, 28.09.2026), 2 aktów");
  });
});

describe("inspectorEmptyMessage — brak cech w kontekście stanu warstwy", () => {
  const registry = stateWith("binding");
  const inside = { lon: 18.55, lat: 54.45, queried: true };

  function empty(state: LayerState, query = inside, filter: string | null = null) {
    return inspectorEmptyMessage(derivePogLayerStatus(scenario(state, registry)), query, filter);
  }

  it("odróżnia ładowanie, niedostępność, brak pokrycia, niepewność, filtr i brak obiektu", () => {
    const loadingState = inspectorEmptyMessage(
      derivePogLayerStatus({ state: { status: "loading", state: null }, tiles: LOADED, viewport: null }),
      inside,
      null,
    );
    const noData = inspectorEmptyMessage(
      derivePogLayerStatus({ state: { status: "no_data", state: null }, tiles: LOADED, viewport: null }),
      inside,
      null,
    );
    const results = {
      loadingState,
      noData,
      tilesLoading: empty("loading"),
      failed: empty("error"),
      notQueried: empty("available", { ...inside, queried: false }),
      outside: empty("available", { lon: 21, lat: 52.2, queried: true }),
      partial: empty("partial"),
      filtered: empty("available", inside, "Tylko akty obowiązujące"),
      none: empty("available"),
      stale: empty("stale"),
    };
    expect(Object.fromEntries(Object.entries(results).map(([key, value]) => [key, value.kind]))).toEqual({
      loadingState: "loading",
      noData: "unavailable",
      tilesLoading: "loading",
      failed: "unavailable",
      notQueried: "unavailable",
      outside: "no_coverage",
      partial: "partial",
      filtered: "filtered",
      none: "no_object",
      stale: "no_object",
    });
    // Ten sam tekst nigdy nie opisuje dwóch różnych rodzajów braku cech.
    const kindsByText = new Map<string, Set<string>>();
    for (const value of Object.values(results)) {
      kindsByText.set(value.text, (kindsByText.get(value.text) ?? new Set()).add(value.kind));
    }
    expect([...kindsByText.values()].every((kinds) => kinds.size === 1)).toBe(true);
    expect(new Set(Object.values(results).map((value) => value.kind)).size).toBe(6);
    for (const value of Object.values(results)) expect(value.text).not.toMatch(FORBIDDEN);
    expect(results.filtered.text).toContain("Tylko akty obowiązujące");
    expect(results.none.text).toContain("#42");
    expect(results.outside.text).toMatch(/nie został jeszcze zaimportowany/);
  });
});

describe("progi zoomu warstw (z serwera)", () => {
  const registry = buildPogState();

  it("najmniejszy zoom grupy warstw i ocena „czy kafle już zawierają warstwę”", () => {
    expect(layerMinZoom(registry, ["zones"])).toBe(11);
    expect(layerMinZoom(registry, ["ouz", "downtown", "social_infrastructure_standard"])).toBe(12);
    expect(layerMinZoom(registry, ["act_boundary", "zones"])).toBe(0);
    expect(layerMinZoom(null, ["zones"])).toBeNull();

    expect(layersLoadedAtZoom(registry, ["zones"], 10.9)).toBe(false);
    expect(layersLoadedAtZoom(registry, ["zones"], 11)).toBe(true);
    expect(layersLoadedAtZoom(registry, ["ouz"], 11.5)).toBe(false);
    // Nieznany zoom albo brak progów: nie twierdzimy, że warstwy nie ma w kaflach.
    expect(layersLoadedAtZoom(registry, ["ouz"], null)).toBe(true);
    expect(layersLoadedAtZoom(registry, ["ouz"], undefined)).toBe(true);
    expect(layersLoadedAtZoom(null, ["ouz"], 3)).toBe(true);
  });
});
