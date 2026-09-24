#!/usr/bin/env bash
# BK-001: dowody dla dokładnego commita, bez kopiowania lokalnego .env i zmian roboczych.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
revision="${1:-8418bddc73bc76f590e021af122262f0a44bcaa4}"
sha="$(git -C "$root" rev-parse --verify "${revision}^{commit}")" || exit 2
short="${sha:0:8}"
out="$root/docs/evaluation/results/baseline"
mkdir -p "$out"
snapshot="$(mktemp -d "${TMPDIR:-/tmp}/bk001-${short}.XXXXXX")"
project="bk001-${short}-$$"
compose=(docker compose --project-name "$project" --env-file "$snapshot/.env.example" -f "$snapshot/docker-compose.yml" -f "$root/scripts/baseline-compose.override.yml")

cleanup() {
  "${compose[@]}" down -v > "$out/docker-cleanup.txt" 2>&1 || true
  rm -rf "$snapshot"
}
trap cleanup EXIT

git -C "$root" archive "$sha" | tar -x -C "$snapshot" || exit 2
printf 'revision=%s\nproject=%s\nstarted_utc=%s\n' "$sha" "$project" "$(date -u +%FT%TZ)" > "$out/docker-run.txt"
printf 'Obraz źródłowy: git archive %s; lokalny .env nie jest kopiowany.\n' "$sha" >> "$out/docker-run.txt"

run_step() {
  local name="$1" rc
  shift
  "$@" > "$out/${name}.txt" 2>&1
  rc=$?
  printf 'exit_code=%s\n' "$rc" >> "$out/${name}.txt"
  printf '%s=%s\n' "$name" "$rc" >> "$out/docker-run.txt"
  return "$rc"
}

run_step docker-info docker version --format '{{.Server.Version}}' || exit 1
run_step compose-config "${compose[@]}" config --quiet || exit 1
run_step compose-build-backend "${compose[@]}" build backend || exit 1
run_step compose-build-frontend "${compose[@]}" build frontend || exit 1
run_step db-image "${compose[@]}" run --rm --no-deps --entrypoint sh db -c 'postgres --version; psql --version' || true
run_step backend-pip-freeze "${compose[@]}" run --rm --no-deps --entrypoint python backend -m pip freeze || true
run_step backend-pytest "${compose[@]}" run --rm \
  -v "$snapshot:/repo:ro" -v "$out:/evidence" -e REPO_ROOT=/repo \
  backend pytest -m 'not docker_cli' --cov=app \
  --cov-report=term-missing --cov-report=xml:/evidence/backend-docker-coverage.xml \
  --cov-report=json:/evidence/backend-docker-coverage.json --cov-fail-under=80 -v || true
run_step compose-images "${compose[@]}" images || true
run_step image-identities docker image inspect \
  --format '{{json .RepoTags}} {{.Id}} {{.Os}}/{{.Architecture}}' \
  "${project}-backend:latest" "${project}-frontend:latest" \
  postgis/postgis:16-3.4 || true
run_step db-postgis-version "${compose[@]}" exec -T db psql -U app -d dzialki -Atc 'SELECT postgis_full_version()' || true

tag="${project}-frontend-test"
run_step frontend-test-image docker build --target test -t "$tag" "$snapshot/frontend" || exit 1
run_step frontend-node-version docker run --rm --entrypoint sh "$tag" -c 'node --version; npm --version' || true
run_step frontend-typecheck docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 "$tag" npm run typecheck || true
mkdir -p "$out/frontend-coverage"
run_step frontend-coverage docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
  -v "$out/frontend-coverage:/evidence" "$tag" sh -c \
  'npm run test:coverage; rc=$?; cp -R coverage/. /evidence/; exit "$rc"' || true
run_step frontend-build docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 "$tag" npm run build || true

printf 'finished_utc=%s\n' "$(date -u +%FT%TZ)" >> "$out/docker-run.txt"
if rg -q '=[1-9][0-9]*$' "$out/docker-run.txt"; then exit 1; fi
