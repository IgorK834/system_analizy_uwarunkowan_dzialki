import { describe, expect, it } from "vitest";

import rawPresentation from "../../shared/pog-presentation.json";
import {
  POG_LEGAL_STATUS_STYLES,
  POG_NON_BINDING_BADGE,
  POG_NULL_STYLE,
  POG_OVERLAYS,
  POG_PATTERNED_LEGAL_STATUSES,
  POG_PRESENTATION,
  POG_STYLE_VERSION,
  POG_UNKNOWN_ZONE,
  POG_ZONE_CODES,
  POG_ZONE_DICTIONARY,
  POG_ZONES,
  type PogPresentationConfig,
  isKnownZoneCode,
  legalStatusOpacityExpression,
  legalStatusPattern,
  legalStatusStyle,
  overlayStyle,
  releaseStatusBadges,
  validatePresentation,
  zoneFillColorExpression,
  zoneLabel,
  zoneStyle,
} from "@/lib/pogZones";

// Kody z urzędowego słownika RodzajStrefyPlanistycznejKod (art. 13c ust. 2 upzp).
const OFFICIAL_CODES = ["SW", "SJ", "SZ", "SU", "SH", "SP", "SR", "SI", "SN", "SC", "SG", "SO", "SK"];

function clone(): PogPresentationConfig {
  return JSON.parse(JSON.stringify(rawPresentation)) as PogPresentationConfig;
}

describe("pogZones — kompletna lista stref z artefaktu", () => {
  it("zawiera 13 ustawowych stref w kolejności słownika i etykiety dla każdej", () => {
    expect(POG_ZONE_CODES).toEqual(OFFICIAL_CODES);
    expect(POG_ZONES.map((zone) => zone.order)).toEqual(OFFICIAL_CODES.map((_, i) => i + 1));
    for (const zone of POG_ZONES) {
      expect(zone.label).toMatch(/^strefa /);
      expect(zoneLabel(zone.code)).toBe(`${zone.code} — ${zone.label}`);
      expect(isKnownZoneCode(zone.code)).toBe(true);
    }
    expect(new Set(POG_ZONES.map((zone) => zone.fill)).size).toBe(13);
    expect(POG_ZONE_DICTIONARY.codelist).toBe("RodzajStrefyPlanistycznejKod");
    expect(POG_ZONE_DICTIONARY.verified_at).toBe("2026-09-28");
    expect(POG_STYLE_VERSION).toBe(rawPresentation.style_version);
  });

  it("nierozpoznany kod i brak kodu mają jawną etykietę i wzór", () => {
    expect(zoneStyle("XX")).toBe(POG_UNKNOWN_ZONE);
    expect(zoneStyle(null)).toBe(POG_UNKNOWN_ZONE);
    expect(zoneLabel(undefined)).toBe(POG_UNKNOWN_ZONE.label);
    expect(isKnownZoneCode("unknown")).toBe(false);
    expect(POG_UNKNOWN_ZONE.pattern).toBe("cross-hatch");
  });

  it("wyrażenie match obejmuje wszystkie kody i ich kolory z JSON", () => {
    const expression = zoneFillColorExpression();
    const pairs = expression.slice(2, -1);
    expect(pairs).toEqual(rawPresentation.zones.flatMap((zone) => [zone.code, zone.fill]));
    expect(expression.at(-1)).toBe(POG_UNKNOWN_ZONE.fill);
  });

  it("OUZ, OZS i OSDIS różnią się wzorem, obrysem i etykietą — nie tylko barwą", () => {
    const overlays = ["ouz", "downtown", "social_infrastructure_standard"] as const;
    const styles = overlays.map((id) => overlayStyle(id));
    expect(new Set(styles.map((style) => style.pattern)).size).toBe(3);
    expect(new Set(styles.map((style) => JSON.stringify(style.line_dasharray))).size).toBe(3);
    expect(styles.map((style) => style.short_label)).toEqual(["OUZ", "OZS", "OSDIS"]);
    for (const style of styles) expect(style.description.length).toBeGreaterThan(20);
    expect(POG_OVERLAYS.map((overlay) => overlay.id)).toContain("act_boundary");
    expect(() => overlayStyle("nope" as never)).toThrow();
  });

  it("projekt ma słabsze krycie i obrys przerywany niż akt wiążący", () => {
    expect(legalStatusStyle("binding").line_dasharray).toBeNull();
    expect(legalStatusStyle("project").line_dasharray).not.toBeNull();
    expect(legalStatusStyle("project").fill_opacity).toBeLessThan(
      legalStatusStyle("binding").fill_opacity,
    );
    expect(legalStatusStyle("bogus").status).toBe("unknown");
    const expression = legalStatusOpacityExpression();
    expect(expression.slice(0, 2)).toEqual(["match", ["get", "legal_status"]]);
    expect(expression).toContain("binding");
    expect(expression.at(-1)).toBe(legalStatusStyle("unknown").fill_opacity);
    expect(POG_LEGAL_STATUS_STYLES).toHaveLength(5);
  });

  it("BK-406: projekt = kolor + wzór + tekst; plakietki statusów obecnych w wydaniu", () => {
    const project = legalStatusStyle("project");
    expect(project.pattern).toBe("horizontal-lines");
    expect(project.badge).toBe("projekt / dane niewiążące");
    expect(POG_NON_BINDING_BADGE).toBe("projekt / dane niewiążące");
    expect(legalStatusStyle("binding")).toMatchObject({ pattern: null, badge: null });
    expect(POG_PATTERNED_LEGAL_STATUSES).toEqual(["project", "in_progress"]);
    expect(legalStatusPattern()).toEqual({ pattern: "horizontal-lines", outline: "#3d3d3d" });
    // Wzór projektu nie udaje nakładki ani „brak wartości”.
    const taken = [POG_NULL_STYLE.pattern, POG_UNKNOWN_ZONE.pattern, ...POG_OVERLAYS.map((item) => item.pattern)];
    expect(taken).not.toContain(project.pattern);

    expect(releaseStatusBadges({ binding: 3 })).toEqual([]);
    expect(releaseStatusBadges({ binding: 1, project: 1, in_progress: 2, unknown: 1, superseded: 0 })).toEqual([
      { status: "project", badge: "projekt / dane niewiążące" },
      { status: "unknown", badge: "status nieustalony" },
    ]);
    for (const style of POG_LEGAL_STATUS_STYLES) {
      expect(`${style.badge ?? ""} ${style.description}`).not.toMatch(/(^|[^a-ząćęłńóśźż])brak planu/i);
    }
  });

  it("walidacja odrzuca rozjechany artefakt", () => {
    expect(validatePresentation(clone())).toEqual(POG_PRESENTATION);

    const gap = clone();
    gap.themes[1].classes![2].min = 0.7;
    expect(() => validatePresentation(gap)).toThrow(/lukę/);

    const duplicate = clone();
    duplicate.zones[1].code = "SW";
    expect(() => validatePresentation(duplicate)).toThrow(/zduplikowane/);

    const order = clone();
    order.zones[0].order = 99;
    expect(() => validatePresentation(order)).toThrow(/kolejność/);

    const color = clone();
    color.zones[0].fill = "red";
    expect(() => validatePresentation(color)).toThrow(/kolor/);

    const closed = clone();
    closed.themes[3].classes!.at(-1)!.max = 100;
    expect(() => validatePresentation(closed)).toThrow(/zamkniętą/);

    const start = clone();
    start.themes[2].classes![0].min = 1;
    expect(() => validatePresentation(start)).toThrow(/od 0/);

    const noClasses = clone();
    noClasses.themes[4].classes = [];
    expect(() => validatePresentation(noClasses)).toThrow(/left/);

    const schema = clone();
    schema.schema = "inny/2";
    expect(() => validatePresentation(schema)).toThrow(/schemat/);

    const unmarked = clone();
    unmarked.legal_statuses.find((item) => item.status === "project")!.pattern = null;
    expect(() => validatePresentation(unmarked)).toThrow(/bez wzoru i plakietki/);

    const bindingBadge = clone();
    bindingBadge.legal_statuses.find((item) => item.status === "binding")!.badge = "projekt";
    expect(() => validatePresentation(bindingBadge)).toThrow(/obowiązujący ze wzorem/);
  });
});
