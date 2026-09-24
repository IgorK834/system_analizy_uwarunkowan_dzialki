import { describe, expect, it } from "vitest";

import { shortSha, verifiedHttpsHref } from "@/lib/safeLink";

describe("verifiedHttpsHref", () => {
  it("przepuszcza wyłącznie zweryfikowany HTTPS", () => {
    expect(verifiedHttpsHref("https://bip.sopot.pl/m,287,plan-ogolny.html", true)).toBe(
      "https://bip.sopot.pl/m,287,plan-ogolny.html",
    );
  });

  it.each([
    ["https://bip.sopot.pl/a.pdf", false],
    ["http://bip.sopot.pl/a.pdf", true],
    ["javascript:alert(1)", true],
    ["https://user:pass@bip.sopot.pl/", true],
    ["nie-url", true],
    [null, true],
    [undefined, true],
  ] as const)("odrzuca %s (verified=%s)", (url, verified) => {
    expect(verifiedHttpsHref(url, verified)).toBeNull();
  });
});

describe("shortSha", () => {
  it("skraca SHA i obsługuje brak wartości", () => {
    expect(shortSha("a".repeat(64))).toBe(`${"a".repeat(12)}…`);
    expect(shortSha(null)).toBe("—");
  });
});
