import {
  actHeading,
  actLinks,
  legalStatusLabel,
  precedenceLabel,
  rejectionLabel,
  shareLabel,
  showsEffectiveDates,
  sourceLabel,
} from "@/lib/mpzpActs";
import { formatPlDate } from "@/lib/pogStatus";
import { verifiedHttpsHref } from "@/lib/safeLink";
import type { MpzpActAmendment, MpzpActResult, MpzpRejectedAct } from "@/lib/types";

/**
 * Akty MPZP przecinające działkę (AU-101): Rejestr Urbanistyczny i KIMPZP scalone po numerze uchwały, z udziałem
 * działki objętym aktem i linkiem do dokumentu. Akt nie jest strefą przeznaczenia. Akt bez granicy w źródle ma
 * udział „nie ustalono” (to nie jest 0%), a nakładające się akty bez rozstrzygnięcia są wszystkie wymienione.
 */
export function MpzpActsList({
  acts,
  rejected = [],
}: {
  acts: MpzpActResult[];
  rejected?: MpzpRejectedAct[];
}) {
  if (acts.length === 0 && rejected.length === 0) return null;

  return (
    <div className="mpzp-acts" data-testid="mpzp-acts">
      {acts.length > 0 && (
        <>
          <h4>Akty MPZP przecinające działkę</h4>
          <ul className="result-list" aria-label="Akty MPZP przecinające działkę">
            {acts.map((act, index) => (
              <ActItem key={`${act.act_identifier ?? act.resolution_number ?? "akt"}-${index}`} act={act} />
            ))}
          </ul>
          <p className="result-preview" data-testid="mpzp-acts-note">
            Akt to granica i dokument planu, a nie strefa przeznaczenia. Udział działki policzono w EPSG:2180 z
            granicy aktu w Rejestrze Urbanistycznym; akt znany tylko z KIMPZP nie ma granicy, więc udziału nie
            ustalono.
          </p>
        </>
      )}
      {rejected.length > 0 && (
        <details data-testid="mpzp-rejected-acts">
          <summary>Akty odrzucone — nie przecinają działki ({rejected.length})</summary>
          <ul aria-label="Akty odrzucone">
            {rejected.map((item, index) => (
              <RejectedItem key={`${item.act_identifier}-${index}`} item={item} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ActItem({ act }: { act: MpzpActResult }) {
  const links = actLinks(act);
  const adopted = formatPlDate(act.resolution_date);
  const validFrom = showsEffectiveDates(act) ? formatPlDate(act.effective_from) : null;
  const validTo = formatPlDate(act.effective_to);

  return (
    <li
      className="result-item"
      data-testid="mpzp-act"
      data-act={act.resolution_number ?? act.act_identifier ?? ""}
      data-precedence={act.precedence}
      data-legal-status={act.legal_status}
    >
      <strong>{actHeading(act)}</strong>
      {act.manual_review_required && (
        <span className="status-badge" data-testid="mpzp-act-review">
          {" "}
          wymaga weryfikacji
        </span>
      )}
      {act.title && <p>{act.title}</p>}
      <p className="result-preview">
        {legalStatusLabel(act.legal_status)}
        {act.raw_status && ` (${act.raw_status})`}
        {adopted && `; uchwalono ${adopted}`}
        {validFrom && `; obowiązuje od ${validFrom}`}
        {validTo && `; do ${validTo}`}
      </p>
      <p data-testid="mpzp-act-share">
        Działka objęta aktem: <strong>{shareLabel(act)}</strong>
      </p>
      <p className="result-preview" data-testid="mpzp-act-sources">
        Źródła: {act.sources.map(sourceLabel).join(", ")}; {precedenceLabel(act.precedence)}.
      </p>
      {links.length > 0 && (
        <ul className="link-list" aria-label={`Dokumenty aktu ${act.resolution_number ?? ""}`.trim()}>
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
          <ul aria-label={`Zmiany aktu ${act.resolution_number ?? ""}`.trim()}>
            {act.amendments.map((amendment, index) => (
              <AmendmentItem key={`${amendment.resolution_number ?? "zmiana"}-${index}`} amendment={amendment} />
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function AmendmentItem({ amendment }: { amendment: MpzpActAmendment }) {
  const href = verifiedHttpsHref(amendment.document_url, amendment.document_url_verified);
  const validFrom = formatPlDate(amendment.effective_from);
  return (
    <li data-testid="mpzp-act-amendment" data-amendment={amendment.resolution_number ?? ""}>
      Zmiana
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
    </li>
  );
}

function RejectedItem({ item }: { item: MpzpRejectedAct }) {
  return (
    <li data-testid="mpzp-rejected-act" data-reason={item.reason}>
      <strong>{item.resolution_number ?? item.act_identifier}</strong>
      {item.title && ` — ${item.title}`}
      <br />
      <span className="result-preview">
        {legalStatusLabel(item.legal_status)}
        {item.raw_status && ` (${item.raw_status})`}; {rejectionLabel(item.reason)}
        {item.distance_m != null &&
          item.reason === "no_overlap" &&
          ` (odległość od działki ok. ${item.distance_m.toLocaleString("pl-PL", { maximumFractionDigits: 1 })} m)`}
        .
      </span>
    </li>
  );
}
