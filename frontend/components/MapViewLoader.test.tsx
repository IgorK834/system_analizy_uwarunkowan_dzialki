import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MapViewLoader } from "@/components/MapViewLoader";

const dynamicState = vi.hoisted(() => ({
  loader: null as null | (() => Promise<unknown>),
  options: null as null | { ssr?: boolean; loading?: () => React.ReactNode },
}));

vi.mock("next/dynamic", () => ({
  default: vi.fn(
    (
      loader: () => Promise<unknown>,
      options: { ssr?: boolean; loading?: () => React.ReactNode },
    ) => {
      dynamicState.loader = loader;
      dynamicState.options = options;
      return function MockClientMap() {
        return <div data-testid="client-map" />;
      };
    },
  ),
}));

describe("MapViewLoader", () => {
  it("wyłącza SSR i renderuje dynamiczny komponent", () => {
    render(<MapViewLoader onMapClick={vi.fn()} />);

    expect(dynamicState.options?.ssr).toBe(false);
    expect(dynamicState.loader).toEqual(expect.any(Function));
    expect(screen.getByTestId("client-map")).toBeInTheDocument();
    expect(dynamicState.options?.loading?.()).toBeTruthy();
  });
});
