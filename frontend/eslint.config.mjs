// ESLint 9 (flat config) dla frontendu (AU-010). Celowo bez `eslint-config-next`: jego zależność
// `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces` ma wysoką podatność bez poprawki
// (GHSA-vfj7-8cjw-p6xm), a `npm audit` ma zostać czysty. Reguły Next (np. `no-img-element`) nie mają
// tu zastosowania — aplikacja nie używa `next/image`, a mapa to MapLibre.
import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [".next/**", "node_modules/**", "coverage/**", "next-env.d.ts", "out/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  {
    // Tylko klasyczne reguły hooków. Zestaw `recommended` wtyczki v7 dodaje reguły kompilatora React
    // (np. `set-state-in-effect`), których naruszenia są w istniejącym kodzie celowe (odczyt
    // `localStorage` po hydracji, reset stanu przy zmianie wydania) — ich egzekwowanie wymaga
    // osobnej refaktoryzacji, a nie zmiany narzędzi.
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
  {
    files: ["**/*.{ts,tsx,mjs}"],
    rules: {
      // Wartości celowo nieużywane poprzedza `_`.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Testy mockują MapLibre i fetch, więc dopuszczają `any`/`require` w pomocnikach.
    files: ["**/*.test.{ts,tsx}", "test/**"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      // Elementy pomocnicze w testach (np. rodzic łapiący kliknięcie) nie są interfejsem użytkownika.
      "jsx-a11y/click-events-have-key-events": "off",
      "jsx-a11y/no-static-element-interactions": "off",
    },
  },
);
