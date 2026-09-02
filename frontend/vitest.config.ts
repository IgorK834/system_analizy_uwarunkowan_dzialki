import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: [
        "lib/config.ts",
        "lib/api.ts",
        "lib/mapStyle.ts",
        "lib/layerStyles.ts",
        "hooks/useAnalyzeParcel.ts",
        "hooks/useResumeAnalysis.ts",
        "components/MapView.tsx",
        "components/MapViewLoader.tsx",
        "components/SearchPanel.tsx",
        "components/ResultPanel.tsx",
        "components/LayerToggle.tsx",
        "components/PreviewOverlays.tsx",
        "components/LayerAvailabilityNote.tsx",
        "components/ReportDownloadButton.tsx",
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 80,
      },
    },
  },
});
