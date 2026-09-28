import { getApiResourceUrl } from "@/lib/config";
import type { PreviewSource } from "@/lib/types";

/**
 * Obraz źródłowy trybu ręcznego MPZP (BK-204): mozaika kafli rastrowego
 * podglądu KIMPZP z backendowego proxy (`/api/v1/map/tiles/...`) z obrysem
 * działki. Przeglądarka nie dostaje adresu usługi zewnętrznej, a obraz jest
 * wyłącznie podglądem — nie geometrią obliczeniową.
 */

const GRID = 3;

type Position = [number, number];

export type TileMosaic = {
  z: number;
  tileSize: number;
  width: number;
  height: number;
  tiles: { x: number; y: number; left: number; top: number }[];
  /** Pierścienie obrysu działki w pikselach mozaiki. */
  rings: Position[][];
};

function collectRings(geometry: unknown): Position[][] {
  if (!geometry || typeof geometry !== "object") return [];
  const node = geometry as { type?: string; geometry?: unknown; coordinates?: unknown };
  if (node.type === "Feature") return collectRings(node.geometry);
  if (node.type === "Polygon") return (node.coordinates as Position[][]) ?? [];
  if (node.type === "MultiPolygon") {
    return ((node.coordinates as Position[][][]) ?? []).flat();
  }
  return [];
}

function project(lon: number, lat: number, z: number, tileSize: number): Position {
  const scale = tileSize * 2 ** z;
  const sin = Math.sin((lat * Math.PI) / 180);
  const x = ((lon + 180) / 360) * scale;
  const y = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale;
  return [x, y];
}

/** Wyznacza największy zoom, przy którym cała działka mieści się w siatce 3×3. */
export function computeTileMosaic(
  parcelGeojson: unknown,
  { minZoom, maxZoom, tileSize }: { minZoom: number; maxZoom: number; tileSize: number },
): TileMosaic | null {
  const rings = collectRings(parcelGeojson).filter((ring) => ring.length > 0);
  if (rings.length === 0) return null;

  const build = (z: number) => {
    const projected = rings.map((ring) => ring.map(([lon, lat]) => project(lon, lat, z, tileSize)));
    const xs = projected.flat().map(([x]) => x);
    const ys = projected.flat().map(([, y]) => y);
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const originX = Math.floor((minX + maxX) / 2 / tileSize) - 1;
    const originY = Math.floor((minY + maxY) / 2 / tileSize) - 1;
    const left = originX * tileSize;
    const top = originY * tileSize;
    const fits =
      minX >= left && maxX <= left + GRID * tileSize && minY >= top && maxY <= top + GRID * tileSize;
    return { z, originX, originY, left, top, projected, fits };
  };

  let chosen = build(minZoom);
  for (let z = maxZoom; z >= minZoom; z -= 1) {
    const candidate = build(z);
    if (candidate.fits) {
      chosen = candidate;
      break;
    }
  }
  const tiles = [];
  const limit = 2 ** chosen.z;
  for (let row = 0; row < GRID; row += 1) {
    for (let col = 0; col < GRID; col += 1) {
      const x = chosen.originX + col;
      const y = chosen.originY + row;
      if (y < 0 || y >= limit || x < 0 || x >= limit) continue;
      tiles.push({ x, y, left: col * tileSize, top: row * tileSize });
    }
  }
  return {
    z: chosen.z,
    tileSize,
    width: GRID * tileSize,
    height: GRID * tileSize,
    tiles,
    rings: chosen.projected.map((ring) =>
      ring.map(([x, y]) => [x - chosen.left, y - chosen.top] as Position),
    ),
  };
}

export type MpzpRasterPreviewProps = {
  parcelGeojson: unknown;
  source: PreviewSource | undefined;
  sourcesState: "loading" | "ready" | "error";
};

export function MpzpRasterPreview({ parcelGeojson, source, sourcesState }: MpzpRasterPreviewProps) {
  if (sourcesState === "loading") {
    return <p className="field-hint" role="status">Trwa ładowanie podglądu rastrowego planu…</p>;
  }
  if (!source) {
    return (
      <p className="manual-review" role="note">
        Podgląd rastrowy MPZP jest chwilowo niedostępny. Nie podawaj symbolu bez
        porównania z rysunkiem planu w dokumencie źródłowym.
      </p>
    );
  }
  const mosaic = computeTileMosaic(parcelGeojson, {
    minZoom: source.min_zoom,
    maxZoom: source.max_zoom,
    tileSize: source.tile_size,
  });
  if (!mosaic) {
    return <p className="manual-review">Brak geometrii działki do pokazania na podglądzie.</p>;
  }
  const tileUrl = (x: number, y: number) =>
    getApiResourceUrl(
      source.tile_url_template
        .replace("{z}", String(mosaic.z))
        .replace("{x}", String(x))
        .replace("{y}", String(y)),
    );
  return (
    <figure className="mpzp-raster-preview">
      <svg
        viewBox={`0 0 ${mosaic.width} ${mosaic.height}`}
        role="img"
        aria-label={`Podgląd rastrowy MPZP (zoom ${mosaic.z}) z obrysem działki`}
      >
        <rect width={mosaic.width} height={mosaic.height} className="mpzp-raster-backdrop" />
        {mosaic.tiles.map((tile) => (
          <image
            key={`${tile.x}-${tile.y}`}
            href={tileUrl(tile.x, tile.y)}
            x={tile.left}
            y={tile.top}
            width={mosaic.tileSize}
            height={mosaic.tileSize}
            preserveAspectRatio="none"
            data-testid="mpzp-raster-tile"
          />
        ))}
        {mosaic.rings.map((ring, index) => (
          <polygon
            key={index}
            points={ring.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}
            className="mpzp-raster-parcel"
          />
        ))}
      </svg>
      <figcaption className="field-hint">
        {source.label} — {source.attribution}. Obraz jest podglądem WMS, a nie
        geometrią obliczeniową. {source.legal_note}
      </figcaption>
    </figure>
  );
}
