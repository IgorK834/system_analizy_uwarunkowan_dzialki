/**
 * Kontrakt układu listy podpowiedzi adresowych (AU-008, audyt B5).
 *
 * Vitest nie liczy układu (`css: false`), więc ten test czyta `globals.css` i pilnuje własności, które
 * naprawiają błąd: lista leży nad kontrolkami mapy, nie zajmuje miejsca w przepływie i przewija się.
 * Rzeczywiste położenie pozycji (`document.elementFromPoint`, 1440×900 i 375×812) weryfikuje przebieg
 * w przeglądarce opisany w `docs/evaluation/au-008-verification.md`.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(path.resolve(__dirname, "globals.css"), "utf-8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

function declarations(selector: string, scope = css): Record<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "m");
  // Pierwsza reguła selektora to reguła bazowa; późniejsze (media queries) ją nadpisują tylko na wąskich ekranach.
  const found: Record<string, string> = {};
  const match = pattern.exec(scope);
  for (const line of (match?.[1] ?? "").split(";")) {
    const [property, ...value] = line.split(":");
    if (property?.trim() && value.length) {
      found[property.trim()] = value.join(":").trim();
    }
  }
  return found;
}

const zIndex = (selector: string) => Number(declarations(selector)["z-index"]);

describe("układ listy podpowiedzi adresowych", () => {
  it("lista jest popoverem pod polem, a nie elementem przepływu", () => {
    const list = declarations(".suggestions");

    expect(list.position).toBe("absolute");
    expect(list.top).toMatch(/100%/);
    expect(list.left).toBe("0");
    expect(list.right).toBe("0");
    expect(declarations(".address-field").position).toBe("relative");
  });

  it("lista przewija się wewnątrz przy małej wysokości okna", () => {
    const list = declarations(".suggestions");

    expect(list["overflow-y"]).toBe("auto");
    expect(list["max-height"]).toMatch(/dvh/);
  });

  it("panel z otwartą listą leży nad kontrolkami mapy i stosem wyników", () => {
    const open = zIndex(".search-panel-suggesting");

    expect(open).toBeGreaterThan(zIndex(".map-controls"));
    expect(open).toBeGreaterThan(zIndex(".result-stack"));
  });

  it("popover nie zmienia położenia panelu POG (kontrolki mapy zakotwiczone przy dole)", () => {
    const controls = declarations(".map-controls");

    expect(controls.position).toBe("absolute");
    expect(controls.bottom).toBe("20px");
    expect(declarations(".suggestions").position).not.toBe("static");
  });
});
