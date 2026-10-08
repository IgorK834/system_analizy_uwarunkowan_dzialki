import {
  actLegalStatusLabel,
  actLinks,
  amendmentKindLabel,
  discoveryStatusLabel,
  discoveryStatusNote,
  multipleActsNote,
} from "@/lib/mpzpDiscovery";
import { formatPlDate } from "@/lib/pogStatus";
import { verifiedHttpsHref } from "@/lib/safeLink";
import type { MpzpDiscoveryAct, MpzpDiscoveryAmendment, MpzpDiscoverySection } from "@/lib/types";

/**
 * Akty MPZP wskazane przez KIMPZP w punktach działki (AU-004).
 *
 * To discovery z warstwy granic planów, nie przecięcie wektorowe stref. Zmiany
 * planu są pokazywane wyłącznie przy akcie zmienianym; przy kilku aktach karta
 * nie wskazuje „właściwego” — wymaga to rozstrzygnięcia w uchwałach.
 */
export function MpzpDiscoveryCard({ discovery }: { discovery: MpzpDiscoverySection }) {
  const note = discoveryStatusNote(discovery.status);
  const multiple = multipleActsNote(discovery);

  return (
    <div className="mpzp-discovery" data-testid="mpzp-discovery" data-status={discovery.status}>
      <h4>Akty wskazane przez KIMPZP</h4>
      <p data-testid="mpzp-discovery-status">
        <strong>{discoveryStatusLabel(discovery.status)}</strong>
        {discovery.acts.length > 0 && ` (${discovery.acts.length})`}
      </p>
      {note && (
        <p className="manual-review" role="note" data-testid="mpzp-discovery-note">
          {note}
        </p>
      )}
      {multiple && (
        <p className="manual-review" role="note" data-testid="mpzp-discovery-multiple">
          {multiple}
        </p>
      )}
      {discovery.acts.length > 0 && (
        <ul className="result-list" aria-label="Akty MPZP wskazane przez KIMPZP">
          {discovery.acts.map((act, index) => (
            <ActItem
              key={`${act.resolution_number ?? "akt"}-${index}`}
              act={act}
              selected={discovery.selected_act !== null && act.resolution_number === discovery.selected_act}
            />
          ))}
        </ul>
      )}
      <p className="result-preview">
        Rozpoznanie na {discovery.sampled_points}{" "}
        {discovery.sampled_points === 1 ? "punkcie" : "punktach"} działki
        {discovery.failed_points > 0 && ` (nieudane zapytania: ${discovery.failed_points})`} — nie jest
        przecięciem wektorowym stref.
      </p>
    </div>
  );
}

function ActItem({ act, selected }: { act: MpzpDiscoveryAct; selected: boolean }) {
  const links = actLinks(act);
  const validFrom = formatPlDate(act.valid_from);
  const adopted = formatPlDate(act.resolution_date);
  const repealed = formatPlDate(act.repealed_on);

  return (
    <li className="result-item" data-testid="mpzp-discovery-act" data-act={act.resolution_number ?? ""}>
      <strong>Uchwała {act.resolution_number ?? "bez numeru"}</strong>
      {selected && <span className="status-badge"> użyta w analizie</span>}
      {act.name && <p>{act.name}</p>}
      <p className="result-preview">
        {actLegalStatusLabel(act.legal_status)}
        {adopted && `; uchwalono ${adopted}`}
        {validFrom && `; obowiązuje od ${validFrom}`}
        {repealed && `; utracił moc ${repealed}`}
        {act.informatization === "raster" && "; plan rastrowy"}
      </p>
      {act.zone_symbols.length > 0 && <p>Symbole stref w punktach: {act.zone_symbols.join(", ")}</p>}
      {links.length > 0 && (
        <ul className="link-list" aria-label={`Dokumenty uchwały ${act.resolution_number ?? ""}`.trim()}>
          {links.map((link) => (
            <li key={link.field}>
              {link.href ? (
                <a href={link.href} target="_blank" rel="noopener noreferrer">
                  {link.label}
                </a>
              ) : (
                <>
                  {link.label}: <span className="mono" title="Adres bez HTTPS — pokazany jako tekst">{link.url}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {act.amendments.length > 0 && (
        <details>
          <summary>Zmiany planu ({act.amendments.length})</summary>
          <ul aria-label={`Zmiany uchwały ${act.resolution_number ?? ""}`.trim()}>
            {act.amendments.map((amendment, index) => (
              <AmendmentItem key={`${amendment.kind}-${index}`} amendment={amendment} />
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function AmendmentItem({ amendment }: { amendment: MpzpDiscoveryAmendment }) {
  const href = verifiedHttpsHref(amendment.document_url, amendment.document_url_verified);
  const validFrom = formatPlDate(amendment.valid_from);
  return (
    <li data-testid="mpzp-discovery-amendment" data-amendment={amendment.resolution_number ?? ""}>
      {amendmentKindLabel(amendment.kind)}
      {amendment.resolution_number && (
        <>
          {" "}
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {amendment.resolution_number}
            </a>
          ) : (
            amendment.resolution_number
          )}
        </>
      )}
      {validFrom && ` (obowiązuje od ${validFrom})`}
      {amendment.name && ` — ${amendment.name}`}
      {amendment.raw_text && `: ${amendment.raw_text}`}
    </li>
  );
}
