import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Kontrast WCAG 2.x liczony z rzeczywistego arkusza `app/globals.css` (bez nowej zależności): reguła
 * klasy → deklaracje, `var(--x)` rozwiązywane z `:root`, kolor `#rgb`/`#rrggbb`. Służy testom
 * dostępności; nie jest częścią kodu aplikacji.
 */
const CSS = readFileSync(path.resolve(__dirname, "../app/globals.css"), "utf8");

export function declarations(selector: string): Record<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Reguła, której lista selektorów ZAWIERA ten selektor (np. `.a,\n.b { … }`); ostatnie wystąpienie wygrywa.
  const pattern = new RegExp(`(?:^|\\})\\s*([^{}]*?(?:^|[\\s,])${escaped}(?=[\\s,{:\\[])[^{}]*)\\{([^}]*)\\}`, "gm");
  let result: Record<string, string> = {};
  for (const match of CSS.matchAll(pattern)) {
    const selectors = match[1].split(",").map((item) => item.trim());
    if (!selectors.includes(selector)) continue;
    result = { ...result, ...parseBlock(match[2]) };
  }
  return result;
}

function parseBlock(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of block.split(";")) {
    const index = line.indexOf(":");
    if (index > 0) out[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return out;
}

function rootVariables(): Record<string, string> {
  const variables: Record<string, string> = {};
  for (const match of CSS.matchAll(/:root\s*\{([^}]*)\}/g)) Object.assign(variables, parseBlock(match[1]));
  return variables;
}

export function resolveColor(value: string): string {
  const variables = rootVariables();
  let current = value.trim();
  for (let depth = 0; depth < 5 && current.startsWith("var("); depth += 1) {
    const name = current.slice(4, -1).split(",")[0].trim();
    current = (variables[name] ?? "").trim();
  }
  return current.toLowerCase();
}

function channels(hex: string): [number, number, number] {
  const text = hex.replace("#", "");
  const full = text.length === 3 ? [...text].map((c) => c + c).join("") : text;
  if (!/^[0-9a-f]{6}$/.test(full)) throw new Error(`nieobsługiwany kolor: ${hex}`);
  return [0, 2, 4].map((offset) => parseInt(full.slice(offset, offset + 2), 16)) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((channel) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(foreground: string, background: string): number {
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

/** Kontrast tekstu klasy względem jej tła (albo `fallbackBackground`, gdy klasa tła nie ustawia). */
export function textContrast(selector: string, fallbackBackground = "#ffffff"): number {
  const rule = declarations(selector);
  const color = resolveColor(rule.color ?? "");
  const background = resolveColor(rule.background ?? rule["background-color"] ?? fallbackBackground);
  return contrastRatio(color, background);
}
