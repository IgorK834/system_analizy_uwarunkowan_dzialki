export function getApiBaseUrl(): string {
  const value = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  if (!value) {
    throw new Error(
      "Brak konfiguracji NEXT_PUBLIC_API_BASE_URL. Ustaw adres API przed uruchomieniem aplikacji.",
    );
  }

  return value.replace(/\/+$/, "");
}

/** Rozwiązuje względną ścieżkę publicznego API bez przepuszczania URL upstreamu. */
export function getApiResourceUrl(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error("Ścieżka zasobu API musi zaczynać się od ukośnika.");
  }
  return `${getApiBaseUrl()}${path}`;
}
