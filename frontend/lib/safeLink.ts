/**
 * Klikalne mogą być wyłącznie odnośniki zweryfikowane przez backend jako HTTPS
 * (`*_verified`) i ponownie sprawdzone po stronie klienta. Pozostałe adresy są
 * pokazywane jako zwykły tekst, więc np. `javascript:` nigdy nie trafia do href.
 */
export function verifiedHttpsHref(
  url: string | null | undefined,
  verified: boolean | undefined,
): string | null {
  if (!url || !verified) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) {
      return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

/** Skrócony SHA-256 do wyświetlenia; pełna wartość trafia do atrybutu title. */
export function shortSha(value: string | null | undefined): string {
  return value ? `${value.slice(0, 12)}…` : "—";
}
