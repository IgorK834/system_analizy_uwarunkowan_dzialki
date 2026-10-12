import { agreementNote, channelStatusLabel, statusPresentation } from "@/lib/mpzpActs";
import type { MpzpStatus } from "@/lib/types";

/**
 * Rozłączny stan ustalenia MPZP (AU-103): „brak pokrycia”, „nie znaleziono”, „nie sprawdzono”, „wiele planów”
 * i „ustalono akt” mają własny kod, etykietę i komunikat (z backendu, wspólne z PDF i paczką audytową).
 * Zapis sprzed wprowadzenia stanów dostaje osobny komunikat, a nie „nie znaleziono planu”.
 */
export function MpzpStatusBanner({ status }: { status: MpzpStatus | null | undefined }) {
  const view = statusPresentation(status);
  const noteClass = view.tone === "ok" ? "result-preview" : "manual-review";

  return (
    <div
      className={`mpzp-status mpzp-status-${view.tone}`}
      data-testid="mpzp-status"
      data-status={view.value}
      data-reason={status?.reason_code ?? ""}
    >
      <p data-testid="mpzp-status-label">
        <span aria-hidden="true">{view.mark} </span>
        <strong>Stan ustalenia MPZP: {view.label}</strong>
      </p>
      <p className={noteClass} role="note" data-testid="mpzp-status-message">
        {view.message}
      </p>
      {status && (
        <>
          {status.channels.length > 0 && (
            <ul className="link-list" aria-label="Stan kanałów MPZP" data-testid="mpzp-status-channels">
              {status.channels.map((channel) => (
                <li key={channel.channel} data-channel={channel.channel} data-channel-status={channel.status}>
                  {channel.label}: {channelStatusLabel(channel.status)}
                  {channel.acts_found > 0 && ` (akty: ${channel.acts_found})`}
                </li>
              ))}
            </ul>
          )}
          {status.channel_agreement === "mismatch" && (
            <p className="manual-review" role="note" data-testid="mpzp-status-agreement">
              {agreementNote(status.channel_agreement)}
            </p>
          )}
          <p className="result-preview">
            Kod powodu: <span className="mono">{status.reason_code}</span>; reguła pierwszeństwa aktów:{" "}
            <span className="mono">{status.precedence_rule_version}</span>.
          </p>
        </>
      )}
    </div>
  );
}
