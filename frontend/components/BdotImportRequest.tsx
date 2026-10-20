"use client";
import { useState } from "react";
import { requestBdotImport } from "@/lib/api";

export function BdotImportRequest() {
  const [powiat, setPowiat] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  return <form onSubmit={(event) => {
    event.preventDefault();
    setPending(true); setError(false); setMessage("");
    void requestBdotImport(powiat).then((result) => { setMessage(result.detail); })
      .catch(() => { setError(true); setMessage("Nie potwierdzono zlecenia importu BDOT10k. Spróbuj ponownie."); })
      .finally(() => setPending(false));
  }}>
    <label htmlFor="bdot-county">Powiat do importu (TERYT, np. 1261)</label>
    <input id="bdot-county" value={powiat} inputMode="numeric" maxLength={4} pattern="[0-9]{4}" required
      onChange={(event) => { setPowiat(event.target.value); setMessage(""); }} />
    <button type="submit" className="secondary-button" disabled={pending || !/^\d{4}$/.test(powiat)}>
      {pending ? "Zlecanie…" : "Poproś o import BDOT10k"}
    </button>
    {message && <p role={error ? "alert" : "status"}>{message}</p>}
  </form>;
}
