/**
 * Wzory wypełnień POG jako obrazy RGBA dla `map.addImage` (BK-403).
 *
 * OUZ, OZS, OSDIS, „brak wartości”, „strefa nierozpoznana” i dane niewiążące
 * (projekt — BK-406) różnią się wzorem,
 * a nie wyłącznie barwą. Obrazy są generowane deterministycznie z pikseli (bez
 * canvas), więc działają w przeglądarce i w testach jsdom tak samo.
 */
import type { PogPatternId } from "@/lib/pogZones";

export const PATTERN_SIZE = 12;
// Krycie kreskowania: wzór ma być czytelny, ale nie może zasłaniać koloru
// trybu tematycznego pod spodem (nakładki OUZ/OZS/OSDIS przykrywają strefy).
export const PATTERN_ALPHA = 150;

export type PatternImage = {
  width: number;
  height: number;
  data: Uint8Array;
};

function hexToRgb(color: string): [number, number, number] {
  const value = color.replace("#", "");
  return [
    Number.parseInt(value.slice(0, 2), 16),
    Number.parseInt(value.slice(2, 4), 16),
    Number.parseInt(value.slice(4, 6), 16),
  ];
}

/** Czy piksel (x, y) kafla wzoru należy do rysunku. */
export function patternCovers(pattern: PogPatternId, x: number, y: number): boolean {
  const size = PATTERN_SIZE;
  switch (pattern) {
    case "diagonal-hatch":
    case "diagonal-lines":
      return (x + y) % (size / 2) === 0;
    case "cross-hatch":
      return (x + y) % (size / 2) === 0 || (x - y + size) % (size / 2) === 0;
    case "cross-lines":
      return x % (size / 2) === 0 || y % (size / 2) === 0;
    case "horizontal-lines":
      return y % (size / 3) === 0;
    case "dots": {
      const center = size / 4;
      const dx = (x % (size / 2)) - center;
      const dy = (y % (size / 2)) - center;
      return dx * dx + dy * dy <= 2;
    }
    default:
      return false;
  }
}

/** Kafel wzoru w kolorze linii na przezroczystym tle. */
export function buildPatternImage(pattern: PogPatternId, color: string): PatternImage {
  const [r, g, b] = hexToRgb(color);
  const data = new Uint8Array(PATTERN_SIZE * PATTERN_SIZE * 4);
  for (let y = 0; y < PATTERN_SIZE; y += 1) {
    for (let x = 0; x < PATTERN_SIZE; x += 1) {
      if (!patternCovers(pattern, x, y)) continue;
      const offset = (y * PATTERN_SIZE + x) * 4;
      data[offset] = r;
      data[offset + 1] = g;
      data[offset + 2] = b;
      data[offset + 3] = PATTERN_ALPHA;
    }
  }
  return { width: PATTERN_SIZE, height: PATTERN_SIZE, data };
}
