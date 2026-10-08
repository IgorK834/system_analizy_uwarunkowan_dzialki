# AU-010: obraz bazowy przypięty digestem (indeks wieloplatformowy); aktualizuje go Dependabot.
FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS dependencies
WORKDIR /workspace/frontend
COPY package.json package-lock.json ./
RUN npm ci

# BK-403: wspólny artefakt prezentacji POG spoza kontekstu frontendu
# (docker-compose.yml: additional_contexts.shared; CI: --build-context shared=./shared).
# Katalog roboczy /workspace/frontend odwzorowuje układ repozytorium, więc import
# ../shared/pog-presentation.json działa tak samo lokalnie i w obrazie.
FROM dependencies AS source
COPY --from=shared pog-presentation.json /workspace/shared/pog-presentation.json
# PV3-04: reguły symbolu strefy i przypadki zgodności wspólne z backendem.
COPY --from=shared zone-symbol-rules.json /workspace/shared/zone-symbol-rules.json
COPY . .

FROM source AS build
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM source AS test
CMD ["npm", "test"]

FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
ENV NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

RUN groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid nodejs nextjs

# Korzeń śledzenia plików Next.js to katalog repozytorium (turbopack.root), więc
# samodzielny serwer leży w .next/standalone/frontend.
COPY --from=build --chown=nextjs:nodejs /workspace/frontend/public ./public
COPY --from=build --chown=nextjs:nodejs /workspace/frontend/.next/standalone/frontend ./
COPY --from=build --chown=nextjs:nodejs /workspace/frontend/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
