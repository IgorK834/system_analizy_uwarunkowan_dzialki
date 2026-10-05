import type {
  MpzpConditionKind,
  MpzpParameterEvidence,
  MpzpValueCondition,
  MpzpValueKind,
} from "@/lib/types";

/**
 * Prezentacja warunków wartości parametrów uchwały (PV3-08), spójna z API, raportem PDF i
 * pakietem audytowym. Uchwała podaje często kilka wartości tego samego parametru, każdą dla
 * innego przypadku (inna wysokość dla dachu płaskiego): to wartości WARUNKOWE, nie sprzeczność.
 * Sprzeczność oznacza kilka wartości tej samej przesłanki i wymaga ręcznej weryfikacji.
 */
export const CONDITION_KIND_LABELS: Record<MpzpConditionKind, string> = {
  building_type: "rodzaj zabudowy",
  roof_type: "rodzaj dachu",
  subzone: "podstrefa",
  location: "położenie",
  other: "inny warunek",
};

export const VALUE_KIND_LABELS: Record<MpzpValueKind, string> = {
  unconditional: "bezwarunkowa",
  conditional: "warunkowa",
  conflict: "sprzeczna kandydatura",
};

/**
 * Rodzaj wartości; odpowiedzi sprzed PV3-08 go nie mają: `conflict` wynika wtedy z grupy
 * konfliktu, a wartość bez warunków jest bezwarunkowa (stare snapshoty czytają się bez warunków).
 */
export function valueKindOf(parameter: MpzpParameterEvidence): MpzpValueKind {
  if (parameter.value_kind) return parameter.value_kind;
  if (parameter.conflict_group_id) return "conflict";
  return (parameter.conditions?.length ?? 0) > 0 ? "conditional" : "unconditional";
}

export function conditionsOf(parameter: MpzpParameterEvidence): MpzpValueCondition[] {
  return parameter.conditions ?? [];
}

/** Krótki opis warunków wartości do jednej linii (etykiety rozdzielone średnikiem); pusty = bezwarunkowa. */
export function conditionsText(parameter: MpzpParameterEvidence): string {
  return conditionsOf(parameter)
    .map((condition) => condition.label)
    .join("; ");
}

export type ParameterKindSummary = {
  conflict: number;
  conditional: number;
  unconditional: number;
};

export function summarizeValueKinds(parameters: MpzpParameterEvidence[]): ParameterKindSummary {
  const summary: ParameterKindSummary = { conflict: 0, conditional: 0, unconditional: 0 };
  for (const parameter of parameters) {
    summary[valueKindOf(parameter)] += 1;
  }
  return summary;
}
