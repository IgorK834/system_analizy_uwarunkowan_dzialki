import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node 22 może wystawić w jsdom niekompletny localStorage, gdy runner nie ma
// ścieżki --localstorage-file. Testy preferencji warstw potrzebują stabilnego,
// izolowanego magazynu zgodnego z Web Storage API.
const storageValues = new Map<string, string>();
const localStorageMock: Storage = {
  get length() {
    return storageValues.size;
  },
  clear: () => storageValues.clear(),
  getItem: (key) => storageValues.get(key) ?? null,
  key: (index) => [...storageValues.keys()][index] ?? null,
  removeItem: (key) => storageValues.delete(key),
  setItem: (key, value) => storageValues.set(key, String(value)),
};

Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: localStorageMock,
});

afterEach(() => {
  cleanup();
  localStorageMock.clear();
});
