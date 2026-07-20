export function getApiBaseUrl(): string {
  const value = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  if (!value) {
    throw new Error(
      "Brak konfiguracji NEXT_PUBLIC_API_BASE_URL. Ustaw adres API przed uruchomieniem aplikacji.",
    );
  }

  return value.replace(/\/+$/, "");
}
