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
        "lib/pogStatus.ts",
        "lib/safeLink.ts",
        "lib/zoneSymbol.ts",
        "lib/compatibility.ts",
        "lib/terrain.ts",
        "lib/pogZones.ts",
        "lib/pogThemes.ts",
        "lib/pogPatterns.ts",
        "lib/pogLayers.ts",
        "hooks/useAnalyzeParcel.ts",
        "hooks/useResumeAnalysis.ts",
        "hooks/usePogTileRelease.ts",
        "components/MapView.tsx",
        "components/MapViewLoader.tsx",
        "components/SearchPanel.tsx",
        "components/ResultPanel.tsx",
        "components/PogOfficialSources.tsx",
        "components/MpzpZoneCard.tsx",
        "components/ManualZonePanel.tsx",
        "components/MpzpRasterPreview.tsx",
        "components/CompatibilityAssessmentCard.tsx",
        "components/TerrainCard.tsx",
        "components/LayerToggle.tsx",
        "components/PreviewOverlays.tsx",
        "components/LayerAvailabilityNote.tsx",
        "components/ReportDownloadButton.tsx",
        "components/PogThemeSelector.tsx",
        "components/PogLegend.tsx",
        "components/PogMapPanel.tsx",
        "components/PogZoneShareChart.tsx",
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
