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
  pogLayerStatusMessage,
  pointInCoverage,
  releaseDescription,
} from "@/lib/pogLayerState";
import { POG_NON_BINDING_BADGE } from "@/lib/pogZones";
import type { LayerState, PogLegalStatus, PogReleaseState, PogTileRelease } from "@/lib/types";
import { buildCoverageArea, buildPogRelease } from "@/test/pogFixtures";

const CHECKED_AT = "2026-09-28T10:15:00Z";
const LOADED: PogTileActivity = { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, anyTileLoaded: true };
const SOPOT_VIEW: LonLatBounds = [18.52, 54.42, 18.58, 54.46];
const ATLANTIC_VIEW: LonLatBounds = [-30, 40, -29, 41];
// „Brak planu” wolno napisać wyłącznie przy urzędowym potwierdzeniu; warstwa
// mapy go nie ma, więc fraza nie może paść w żadnym stanie. Dozwolone jest
// wymagane przez ADR-002 zastrzeżenie „nie oznacza braku planu”.
const FORBIDDEN = /(^|[^a-ząćęłńóśźż])brak planu/i;

function releaseWith(status: PogLegalStatus, overrides: Partial<PogTileRelease> = {}): PogTileRelease {
  return buildPogRelease({
    acts_by_legal_status: { [status]: 1 },
    coverage_areas: [buildCoverageArea({ legal_status: status })],
    ...overrides,
  });
}

/** Wejście, które daje dokładnie wskazany stan warstwy. */
function scenario(
  state: LayerState,
  release: PogTileRelease,
): Parameters<typeof derivePogLayerStatus>[0] {
  const available: PogReleaseState = { status: "available", release, checkedAt: CHECKED_AT };
  switch (state) {
    case "loading":
      return { release: available, tiles: INITIAL_TILE_ACTIVITY, viewport: SOPOT_VIEW };
    case "available":
      return { release: available, tiles: LOADED, viewport: SOPOT_VIEW };
    case "partial":
      return { release: available, tiles: { ...LOADED, tileErrors: 2 }, viewport: SOPOT_VIEW };
    case "no_coverage":
      return { release: available, tiles: LOADED, viewport: ATLANTIC_VIEW };
    case "error":
      return {
        release: available,
        tiles: { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, tileErrors: 3 },
        viewport: SOPOT_VIEW,
      };
    default:
      return {
        release: { status: "stale", release, checkedAt: CHECKED_AT, reason: "refresh_failed" },
        tiles: { ...LOADED, tileErrors: 1 },
        viewport: SOPOT_VIEW,
      };
  }
}

function allText(status: PogLayerStatus): string {
  const { title, detail } = pogLayerStatusMessage(status);
  const release = status.release;
  return [title, detail, release ? releaseDescription(release) : ""].join(" ");
}

describe("pogLayerState — 6 stanów × status prawny", () => {
  const statuses: PogLegalStatus[] = ["binding", "project", "unknown"];

  for (const legal of statuses) {
    for (const state of LAYER_STATES) {
      it(`${state} przy wydaniu z aktem ${legal}: odrębny komunikat, plakietka niezależna od stanu, bez „brak planu”`, () => {
        const release = releaseWith(legal);
        const status = derivePogLayerStatus(scenario(state, release));
        expect(status.state).toBe(state);
        expect(status.release).toBe(release);

        const text = allText(status);
        expect(text).not.toMatch(FORBIDDEN);
        expect(text).toContain(`#${release.release_id}`);

        const badges = status.badges.map((item) => item.badge);
        if (legal === "project") expect(badges).toEqual([POG_NON_BINDING_BADGE]);
        if (legal === "unknown") expect(badges).toEqual(["status nieustalony"]);
        if (legal === "binding") expect(badges).toEqual([]);
      });
    }
  }

  it("każdy stan ma inny tytuł komunikatu, etykietę i opis w legendzie", () => {
    const release = releaseWith("project");
    const titles = LAYER_STATES.map(
      (state) => pogLayerStatusMessage(derivePogLayerStatus(scenario(state, release))).title,
    );
    expect(new Set(titles).size).toBe(LAYER_STATES.length);
    expect(new Set(Object.values(LAYER_STATE_LABELS)).size).toBe(6);
    for (const description of Object.values(LAYER_STATE_DESCRIPTIONS)) {
      expect(description).not.toMatch(FORBIDDEN);
    }
  });

  it("stan wydania bez danych: ładowanie, brak wydania (brak pokrycia) i awaria", () => {
    const base = { tiles: LOADED, viewport: SOPOT_VIEW };
    const loading = derivePogLayerStatus({ ...base, release: { status: "loading", release: null } });
    const missing = derivePogLayerStatus({ ...base, release: { status: "no_release", release: null } });
    const failed = derivePogLayerStatus({ ...base, release: { status: "error", release: null } });
    expect([loading.state, missing.state, failed.state]).toEqual(["loading", "no_coverage", "error"]);
    expect([loading.reason, missing.reason, failed.reason]).toEqual([
      "release_loading",
      "no_release",
      "release_error",
    ]);
    expect(pogLayerStatusMessage(missing).detail).toMatch(/Nie oznacza to braku planu ogólnego/);
    for (const status of [loading, missing, failed]) {
      expect(status.badges).toEqual([]);
      expect(allText(status)).not.toMatch(FORBIDDEN);
    }
  });

  it("stale zachowuje wydanie i datę ostatniego potwierdzenia; nieaktywne wydanie też jest stale", () => {
    const release = releaseWith("project");
    const stale = derivePogLayerStatus(scenario("stale", release));
    expect(stale.checkedAt).toBe(CHECKED_AT);
    expect(pogLayerStatusMessage(stale).detail).toContain(formatCheckedAt(CHECKED_AT) as string);
    expect(formatCheckedAt(CHECKED_AT)).toBe("28.09.2026, 12:15");

    const inactive = derivePogLayerStatus({
      release: { status: "available", release: { ...release, is_active: false }, checkedAt: CHECKED_AT },
      tiles: LOADED,
      viewport: SOPOT_VIEW,
    });
    expect([inactive.state, inactive.reason]).toEqual(["stale", "release_not_active"]);
    expect(pogLayerStatusMessage(inactive).title).toContain("nie jest już aktywne");
  });

  it("pokrycie z metadanych obszaru: pusty kafel w zasięgu to nie no_coverage", () => {
    const release = releaseWith("binding");
    // Źródło wczytane, żaden kafel nie przyniósł cech — ale widok jest w zasięgu.
    const emptyTile = derivePogLayerStatus({
      release: { status: "available", release, checkedAt: CHECKED_AT },
      tiles: { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true },
      viewport: SOPOT_VIEW,
    });
    expect(emptyTile.state).toBe("available");

    // Starsze wydanie bez coverage_areas: zasięg z bounds wydania.
    const legacy = buildPogRelease({ coverage_areas: undefined });
    expect(
      derivePogLayerStatus({
        release: { status: "available", release: legacy, checkedAt: CHECKED_AT },
        tiles: LOADED,
        viewport: ATLANTIC_VIEW,
      }).state,
    ).toBe("no_coverage");
    // Bez żadnych metadanych zasięgu i bez okna mapy nie zgadujemy braku pokrycia.
    expect(
      derivePogLayerStatus({
        release: {
          status: "available",
          release: buildPogRelease({ coverage_areas: [], bounds: null }),
          checkedAt: CHECKED_AT,
        },
        tiles: LOADED,
        viewport: null,
      }).state,
    ).toBe("available");
  });

  it("niepełne dane aktu w widoku i limit kafla dają partial z przyczyną", () => {
    const incomplete = releaseWith("binding", {
      coverage_areas: [
        buildCoverageArea({ is_complete: false, incomplete_reasons: ["missing_area"] }),
        buildCoverageArea({ act_id: "B", has_boundary: false, is_complete: null }),
        buildCoverageArea({ act_id: "poza", bounds: ATLANTIC_VIEW, is_complete: false }),
      ],
    });
    const status = derivePogLayerStatus({
      release: { status: "available", release: incomplete, checkedAt: CHECKED_AT },
      tiles: LOADED,
      viewport: SOPOT_VIEW,
    });
    expect([status.state, status.reason]).toEqual(["partial", "incomplete_data"]);
    expect(status.incompleteActs).toEqual(["PL.ZIPPZP.10011/226401-POG/1POG", "B"]);
    expect(pogLayerStatusMessage(status).detail).toContain("B");

    const limit = derivePogLayerStatus({
      release: { status: "available", release: incomplete, checkedAt: CHECKED_AT },
      tiles: { ...INITIAL_TILE_ACTIVITY, sourceLoaded: true, tileErrors: 1, tileLimitHit: true },
      viewport: SOPOT_VIEW,
    });
    expect([limit.state, limit.reason]).toEqual(["partial", "tile_limit"]);
    expect(pogLayerStatusMessage(limit).detail).toMatch(/Przybliż/);
  });

  it("geometria zasięgów", () => {
    expect(boundsIntersect([0, 0, 1, 1], [1, 1, 2, 2])).toBe(true);
    expect(boundsIntersect([0, 0, 1, 1], [1.1, 0, 2, 1])).toBe(false);
    const release = buildPogRelease();
    expect(pointInCoverage(release, 18.55, 54.45)).toBe(true);
    expect(pointInCoverage(release, 21.0, 52.2)).toBe(false);
    expect(pointInCoverage(buildPogRelease({ coverage_areas: [], bounds: null }), 0, 0)).toBe(true);
    expect(formatCheckedAt(null)).toBeNull();
    expect(formatCheckedAt("nie-data")).toBe("nie-data");
    expect(releaseDescription(buildPogRelease({ published_at: null }))).toBe(
      "Wydanie pog-0123456789ab (#42)",
    );
  });
});

describe("inspectorEmptyMessage — brak cech w kontekście stanu warstwy", () => {
  const release = releaseWith("binding");
  const inside = { lon: 18.55, lat: 54.45, queried: true };

  function empty(state: LayerState, query = inside, filter: string | null = null) {
    return inspectorEmptyMessage(derivePogLayerStatus(scenario(state, release)), query, filter);
  }

  it("odróżnia ładowanie, niedostępność, brak pokrycia, niepewność, filtr i brak obiektu", () => {
    const loadingRelease = inspectorEmptyMessage(
      derivePogLayerStatus({ release: { status: "loading", release: null }, tiles: LOADED, viewport: null }),
      inside,
      null,
    );
    const noRelease = inspectorEmptyMessage(
      derivePogLayerStatus({ release: { status: "no_release", release: null }, tiles: LOADED, viewport: null }),
      inside,
      null,
    );
    const results = {
      loadingRelease,
      noRelease,
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
      loadingRelease: "loading",
      noRelease: "unavailable",
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
  });
});
