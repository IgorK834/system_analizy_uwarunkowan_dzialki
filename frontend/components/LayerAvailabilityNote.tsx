import type { PreviewSource } from "@/lib/types";

export type LayerAvailabilityNoteProps = {
  sources: PreviewSource[];
  compact?: boolean;
};

export function LayerAvailabilityNote({
  sources,
  compact = false,
}: LayerAvailabilityNoteProps) {
  const content = (
    <>
      <p>
        Podgląd ma charakter poglądowy; obraz pochodzi z usług publicznych i
        może być nieaktualny lub niepełny. Brak obiektów na mapie nie oznacza
        braku sieci ani aktu planistycznego.
      </p>
      {sources.length > 0 ? (
        <ul>
          {sources.map((source) => (
            <li key={source.source_key}>
              <a href={source.info_url} target="_blank" rel="noreferrer">
                {source.label}
              </a>
              {compact
                ? ` — ${source.legal_note}`
                : ` — ${source.attribution}. ${source.legal_note}`}
            </li>
          ))}
        </ul>
      ) : (
        <p>Metadane i atrybucje źródeł są chwilowo niedostępne.</p>
      )}
    </>
  );

  if (compact) {
    return (
      <section
        className="layer-availability-note compact"
        aria-label="Informacja o warstwach podglądowych"
      >
        <details className="layer-availability-details">
          <summary className="layer-availability-summary">
            Informacja o źródłach i ograniczeniach
          </summary>
          {content}
        </details>
      </section>
    );
  }

  return (
    <section
      className="layer-availability-note"
      aria-label="Informacja o warstwach podglądowych"
    >
      {content}
    </section>
  );
}
