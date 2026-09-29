import path from "node:path";

import type { NextConfig } from "next";

// BK-403: frontend importuje wspólny artefakt prezentacji POG z
// ../shared/pog-presentation.json. Turbopack rozwiązuje wyłącznie pliki pod
// swoim korzeniem, dlatego korzeniem (i korzeniem śledzenia standalone) jest
// katalog repozytorium — w obrazie Docker /workspace.
const repoRoot = path.join(__dirname, "..");

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: { root: repoRoot },
  outputFileTracingRoot: repoRoot,
};

export default nextConfig;
