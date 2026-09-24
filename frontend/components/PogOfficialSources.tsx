import { formatPlDate } from "@/lib/pogStatus";
import { shortSha, verifiedHttpsHref } from "@/lib/safeLink";
import type { FormalDocumentSource, PogActResult } from "@/lib/types";

const RELATION_LABELS: Record<string, string> = {
  przystapienie: "przystąpienie do sporządzenia",
  uchwala: "uchwalenie",
  zmienia: "zmiana",
  uchyla: "uchylenie",
  uniewaznia: "unieważnienie",
};

const STATUS_LABELS: Record<FormalDocumentSource["status"], string> = {
  current: "aktualny",
  superseded: "nieaktualny",
  unavailable: "niedostępny",
  unresolved: "powiązanie nierozstrzygnięte",
};

const formatDate = formatPlDate;

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/**
 * Łańcuch źródeł urzędowych aktu POG (BK-107): identyfikator i wersja aktu,
 * publikacja, wydanie danych z SHA, karta metadanych CSW i dokumenty formalne.
 */
export function PogOfficialSources({ act }: { act: PogActResult }) {
  const gmlHref = verifiedHttpsHref(act.gml_url, act.gml_url_verified);
  const cardHref = verifiedHttpsHref(act.card_url, act.card_url_verified);
  const documents = act.formal_documents ?? [];
  const validFrom = formatDate(act.valid_from);
  const validTo = formatDate(act.valid_to);

  return (
    <section className="pog-sources" aria-label="Źródła urzędowe aktu POG">
      <h4>Źródła urzędowe</h4>
      <dl className="result-summary">
        <div>
          <dt>Identyfikator aktu</dt>
          <dd>{act.act_identifier ?? act.id}</dd>
        </div>
        <div>
          <dt>Wersja aktu</dt>
          <dd>
            {act.act_version ?? act.version ?? "—"}
            {act.version_started_at ? ` (od ${formatDate(act.version_started_at)})` : ""}
          </dd>
        </div>
        {act.publication_id && (
          <div>
            <dt>Identyfikator publikacji</dt>
            <dd className="mono">{act.publication_id}</dd>
          </div>
        )}
        {validFrom && (
          <div>
            <dt>Obowiązuje od (wg APP)</dt>
            <dd>
              {validFrom}
              {validTo ? ` do ${validTo}` : ""}
            </dd>
          </div>
        )}
        {act.publication_date && (
          <div>
            <dt>Publikacja zbioru (CSW)</dt>
            <dd>{formatDate(act.publication_date)}</dd>
          </div>
        )}
        {act.data_release_id != null && (
          <div>
            <dt>Wydanie danych</dt>
            <dd>
              #{act.data_release_id}
              {act.release_label ? ` (${act.release_label})` : ""}
              {act.artifact_sha256 && (
                <>
                  {" · SHA-256 "}
                  <span className="mono" title={act.artifact_sha256}>
                    {shortSha(act.artifact_sha256)}
                  </span>
                </>
              )}
            </dd>
          </div>
        )}
        <div>
          <dt>GML wersji aktu</dt>
          <dd>
            {gmlHref ? (
              <ExternalLink href={gmlHref}>Rejestr Urbanistyczny — WFS APP</ExternalLink>
            ) : (
              "brak zweryfikowanego odnośnika"
            )}
          </dd>
        </div>
        <div>
          <dt>Karta metadanych (CSW)</dt>
          <dd>
            {cardHref ? (
              <ExternalLink href={cardHref}>
                rekord {act.metadata?.record_id ?? ""}
              </ExternalLink>
            ) : (
              "metadane CSW niedostępne"
            )}
          </dd>
        </div>
      </dl>
      {documents.length > 0 && (
        <ul className="result-list" aria-label="Dokumenty formalne aktu">
          {documents.map((document) => {
            const href = verifiedHttpsHref(document.link, document.link_verified);
            const title = document.title ?? document.short_name ?? document.document_identifier;
            return (
              <li
                key={`${document.document_identifier}-${document.document_version ?? ""}`}
                className="result-list-item"
                data-status={document.status}
              >
                <strong>{href ? <ExternalLink href={href}>{title}</ExternalLink> : title}</strong>
                <p className="field-hint mono">
                  {document.document_identifier}
                  {document.document_version ? ` / ${document.document_version}` : ""}
                  {" · SHA-256 "}
                  <span title={document.record_sha256 ?? undefined}>
                    {shortSha(document.record_sha256)}
                  </span>
                </p>
                <p className="field-hint">
                  {RELATION_LABELS[document.relation ?? ""] ?? document.relation ?? "relacja nieznana"}
                  {" · "}
                  {STATUS_LABELS[document.status]}
                  {document.effective_date ? ` · w życie ${formatDate(document.effective_date)}` : ""}
                  {document.repeal_date ? ` · uchylony ${formatDate(document.repeal_date)}` : ""}
                </p>
                {!href && document.link && <p className="field-hint">{document.link}</p>}
                {document.warning && (
                  <p className="manual-review" role="note">
                    {document.warning}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
