import type { BdotReleaseInfo, SourceMetadata } from "@/lib/types";

/** Wydanie danych BDOT10k: powiat, stan danych (data najnowszej wersji obiektu) i etykieta paczki. */
export function BdotReleaseLine({
  release,
  formatDate,
  testId,
}: {
  release: BdotReleaseInfo | null;
  formatDate: (value: string | null | undefined) => string | null;
  testId: string;
}) {
  if (!release) return null;
  const asOf = formatDate(release.data_as_of);
  const imported = formatDate(release.imported_at);
  return (
    <p className="field-hint" data-testid={testId}>
      Wydanie danych: powiat {release.teryt_powiat}, stan danych na {asOf ?? "nieustalony"}
      {imported ? `, zaimportowano ${imported}` : ""} · {release.version_label}
    </p>
  );
}

export function BdotSourceLine({
  source,
  testId,
  label,
}: {
  source: SourceMetadata | null;
  testId: string;
  label: string;
}) {
  if (!source) {
    return (
      <p className="section-empty" data-testid={testId}>
        Brak metadanych źródła {label}.
      </p>
    );
  }
  return (
    <p className="field-hint" data-testid={testId}>
      Źródło: {source.source_name}
      {source.source_version ? ` (${source.source_version})` : ""} · pewność{" "}
      {(source.confidence * 100).toFixed(0)}%
      {source.manual_review_required ? " · wymaga weryfikacji" : ""}
    </p>
  );
}
