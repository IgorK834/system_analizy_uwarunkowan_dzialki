#!/usr/bin/env node
/**
 * Real local UI captures; no mocked requests, injected results or image retouching.
 * Install Playwright and sharp OUTSIDE this repository, then set PLAYWRIGHT_MODULE
 * and SHARP_MODULE to their absolute package directories if not globally resolvable.
 * Optional CHROMIUM_EXECUTABLE selects an existing headless Chromium installation.
 * Optional PNGQUANT selects an external pngquant binary (quality 80-95).
 *
 * Start a NEW Compose project with an empty database (never clear an existing DB).
 *   node scripts/portfolio-screenshots.mjs empty
 * Import POG using the reviewer guide, then:
 *   node scripts/portfolio-screenshots.mjs results
 * Optional PDF-only recapture: node scripts/portfolio-screenshots.mjs documents
 *
 * BASE_URL / API_URL default to localhost:3000 / localhost:8000.
 * CAPTURE_WORK_DIR holds private downloads and intermediate files outside Git.
 * OUTPUT_DIR defaults to docs/portfolio; finished images also go to docs/screenshots.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import os from 'node:os';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const sharp = require(process.env.SHARP_MODULE || 'sharp');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.resolve(process.env.OUTPUT_DIR || path.join(root, 'docs/portfolio'));
const work = path.resolve(process.env.CAPTURE_WORK_DIR || path.join(os.tmpdir(), 'parcel-portfolio-capture'));
const base = process.env.BASE_URL || 'http://localhost:3000';
const api = process.env.API_URL || 'http://localhost:8000';
const parcel = '246101_1.0056.155/3';
const stage = process.argv[2];
if (!['empty', 'results', 'documents'].includes(stage)) throw new Error('Use empty, results or documents.');
await fs.mkdir(output, { recursive: true });
await fs.mkdir(work, { recursive: true, mode: 0o700 });
await fs.mkdir(path.join(root, 'docs/screenshots'), { recursive: true });
const evidencePath = path.join(output, 'capture-evidence.json');
const evidence = await fs.readFile(evidencePath, 'utf8').then(JSON.parse).catch(() => ({ captures: [] }));
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}),
});
evidence.environment = { browser: `Chromium ${browser.version()}`, playwright: require(process.env.PLAYWRIGHT_MODULE ? path.join(process.env.PLAYWRIGHT_MODULE, 'package.json') : 'playwright/package.json').version, viewport: '1600x1000', device_scale_factor: 1, locale: 'pl-PL', base_url: base };
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, locale: 'pl-PL' });
const page = await context.newPage();
page.setDefaultTimeout(30000);
const responses = [];
page.on('response', r => {
  const u = new URL(r.url());
  if (u.origin === new URL(api).origin) responses.push({ path: u.pathname, status: r.status() });
});

async function capture(name, detail, target = page) {
  const raw = await target.screenshot({ type: 'png', fullPage: false });
  await fs.writeFile(path.join(work, `${name}.raw.png`), raw);
  // Only the explicitly requested optimizers: lossless PNG, pngquant 80-95 when
  // installed, then lossless WebP. No resizing, annotation, recolouring or retouching.
  let buffer = await sharp(raw).png({ compressionLevel: 9, adaptiveFiltering: true }).toBuffer();
  let file = `${name}.png`;
  for (const quality of ['80-95', '80-90', '80-85']) {
    if (buffer.length <= 900000) break;
    const quantized = spawnSync(process.env.PNGQUANT || 'pngquant', ['--quality', quality, '--speed', '1', '--strip', '--output', '-', '-'], { input: raw, maxBuffer: 10000000 });
    if (quantized.status === 0) buffer = quantized.stdout;
  }
  if (buffer.length > 900000) { buffer = await sharp(raw).webp({ lossless: true, effort: 6 }).toBuffer(); file = `${name}.webp`; }
  if (buffer.length > 900000) throw new Error(`${file} exceeds 900 KB; recapture a less detailed map viewport.`);
  await fs.writeFile(path.join(output, file), buffer);
  await fs.writeFile(path.join(root, 'docs/screenshots', file), buffer);
  evidence.captures = evidence.captures.filter(c => c.name !== name);
  evidence.captures.push({ name, file, captured_at: new Date().toISOString(), width: 1600, height: 1000, bytes: buffer.length, detail });
  await fs.writeFile(evidencePath, JSON.stringify(evidence, null, 2) + '\n');
  console.log(`Captured ${file} (${buffer.length} bytes)`);
}

async function settle() {
  await page.locator('.maplibregl-canvas').waitFor();
  await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(3500);
  await page.evaluate(() => document.fonts.ready);
}

async function documentPages() {
  const render = spawnSync(process.env.PDFTOPPM || 'pdftoppm', ['-f', '1', '-l', '2', '-r', '110', '-png', path.join(work, 'report.pdf'), path.join(work, 'report-page')], { encoding: 'utf8' });
  if (render.status !== 0) throw new Error(`pdftoppm failed: ${render.stderr}`);
  const files = (await fs.readdir(work)).filter(f => /^report-page-\d+\.png$/.test(f)).sort();
  if (files.length !== 2) throw new Error('Expected two rendered report pages.');
  const pdfPage = await context.newPage();
  await pdfPage.setContent('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;width:1600px;height:1000px;background:white}body{display:flex;justify-content:center;gap:70px}img{height:1000px;width:auto;display:block}</style>' + files.map(() => '<img alt="Complete page of the downloaded PDF report">').join(''));
  for (let i = 0; i < files.length; i++) await pdfPage.locator('img').nth(i).evaluate((el, src) => { el.src = src; }, `data:image/png;base64,${(await fs.readFile(path.join(work, files[i]))).toString('base64')}`);
  await pdfPage.locator('img').evaluateAll(imgs => Promise.all(imgs.map(img => img.decode())));
  await capture('05-pdf-report', 'First two complete pages of the downloaded report, pdftoppm at 110 DPI, fitted to 1000px page height, no viewer chrome.', pdfPage);
  await pdfPage.close();
}

try {
  if (stage === 'empty') {
    const state = await context.request.get(`${api}/api/v1/map/pog/state`);
    if (state.status() !== 404) throw new Error('Empty-state capture requires a real empty POG registry (HTTP 404).');
    await page.goto(base);
    await page.getByText('Warstwa POG niedostępna: baza nie zawiera jeszcze zaimportowanych planów ogólnych.', { exact: false }).waitFor();
    await settle();
    await capture('04-empty-state', 'Captured FIRST, before any import; real map state endpoint returned HTTP 404.');
  }

  if (stage === 'results') {
    if (!evidence.captures.some(c => c.name === '04-empty-state')) throw new Error('Capture the empty state first.');
    const state = await context.request.get(`${api}/api/v1/map/pog/state`);
    if (!state.ok()) throw new Error('Import POG before capturing the results.');
    await page.goto(base);
    await page.getByRole('tab', { name: 'Identyfikator działki', exact: true }).click();
    await page.locator('#parcel-identifier').fill(parcel);
    const analysisResponse = page.waitForResponse(r => r.url() === `${api}/analyze` && r.request().method() === 'POST', { timeout: 180000 });
    await page.getByRole('button', { name: 'Analizuj działkę', exact: true }).click();
    const response = await analysisResponse;
    if (!response.ok()) throw new Error(`Analysis failed: HTTP ${response.status()}`);
    const analysis = await response.json();
    // Tokens stay only in the private work directory; public evidence is explicitly allowlisted.
    await fs.writeFile(path.join(work, 'analysis.private.json'), JSON.stringify(analysis), { mode: 0o600 });
    evidence.analysis = {
      parcel, analysis_id: analysis.analysis_id, analyzed_at: analysis.analyzed_at, status: analysis.status,
      completeness: analysis.completeness, sources: analysis.sources, warnings: analysis.warnings,
      section_quality: analysis.section_quality,
    };
    await page.getByTestId('completeness').waitFor();
    // Use existing UI switches to show the vector POG map clearly. Do not alter
    // source responses or map styles; raster previews remain available to users.
    for (const name of ['Uzbrojenie terenu (nakładka WMS, KIUT)', 'MPZP (nakładka WMS, KIMPZP)', 'Plan Ogólny Gminy (nakładka WMS)']) {
      const toggle = page.getByRole('switch', { name, exact: true });
      if (await toggle.getAttribute('aria-checked') === 'true') await toggle.click();
    }
    await page.locator('.map-controls').evaluate(el => { el.scrollTop = 0; });
    await settle();
    await capture('01-analysis-result', 'Real parcel analysis, locally imported vector POG, native completeness headline; WMS previews disabled with their existing UI switches.');

    // Native map click in the unobstructed middle of the map opens the POG inspector.
    await page.mouse.click(1000, 520);
    await page.getByTestId('pog-inspector-zone').first().waitFor();
    await settle();
    await capture('02-map-layers', 'Native map click selected a real vector POG object; inspector fetched its details.');
    await page.getByRole('button', { name: 'Zamknij inspektor (Escape)' }).click();

    await page.locator('.result-stack').evaluate(el => { el.scrollTop = 0; });
    await page.getByText('Sekcje analizy i podstawa ustalenia', { exact: true }).click();
    await capture('03-section-statuses', 'Native expanded completeness details: per-section status, missing-coverage labels and interpretation note.');
    await page.getByText('Sekcje analizy i podstawa ustalenia', { exact: true }).click();
    // A readable supplementary capture preserves the long warning list without
    // shrinking, rearranging or retouching the actual application interface.
    await page.getByRole('region', { name: 'Ostrzeżenia', exact: true }).evaluate(el => {
      const panel = el.closest('.result-stack');
      panel.scrollTop += el.getBoundingClientRect().top - panel.getBoundingClientRect().top;
    });
    await capture('03b-warning-list', 'Supplement to view 03: beginning of the real warning list, using only native result-panel scrolling.');
    await page.locator('.result-stack').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await capture('03c-warning-list-end', 'Supplement to view 03: end of the real warning list; together with 03b preserves every warning at native text size.');

    await page.locator('.result-stack').evaluate(el => { el.scrollTop = 0; });
    const pdfDownload = page.waitForEvent('download', { timeout: 180000 });
    await page.getByRole('button', { name: 'Pobierz raport PDF', exact: true }).click();
    await (await pdfDownload).saveAs(path.join(work, 'report.pdf'));
    await documentPages();
    const zipDownload = page.waitForEvent('download', { timeout: 90000 });
    await page.getByRole('button', { name: 'Pobierz pakiet audytowy (ZIP)', exact: true }).click();
    await (await zipDownload).saveAs(path.join(work, 'audit.zip'));
    const hash = await page.getByTestId('audit-package-hash').locator('.mono').innerText();
    if (!/^[a-f0-9]{64}$/.test(hash)) throw new Error('Invalid UI package hash.');
    const verification = spawnSync('python3', [path.join(root, 'backend/scripts/verify_audit_package.py'), path.join(work, 'audit.zip'), '--package-sha256', hash], { encoding: 'utf8' });
    await fs.writeFile(path.join(work, 'verify.stdout.txt'), verification.stdout);
    await fs.writeFile(path.join(work, 'verify.stderr.txt'), verification.stderr);
    if (verification.status !== 0) throw new Error(`Verifier failed: ${verification.stderr}`);
    evidence.audit = { sha256_from_ui: hash, verifier_stdout: verification.stdout, verifier_stderr: verification.stderr, exit_code: verification.status };
    // Preserve actual UI text and actual subprocess output verbatim, not a typed imitation.
    const uiText = (await Promise.all(['.audit-package-button', '[data-testid="audit-package-note"]', '[data-testid="audit-package-hash"]'].map(selector => page.locator(selector).innerText()))).join('\n\n');
    const htmlPage = await context.newPage();
    await htmlPage.setContent('<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#f4f5f3;color:#17231d;font:20px/1.6 monospace}main{display:grid;grid-template-columns:1fr 1fr;gap:48px;padding:125px 65px}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}section{padding:30px;background:white;border:1px solid #ccd2cc}h1{font:24px/1.4 monospace;margin:0 0 28px}</style><main><section><h1>Local application · audit download</h1><pre id="ui"></pre></section><section><h1>Python verifier · actual stdout</h1><pre id="stdout"></pre></section></main>');
    await htmlPage.locator('#ui').evaluate((el, text) => { el.textContent = text; }, uiText);
    await htmlPage.locator('#stdout').evaluate((el, text) => { el.textContent = text; }, verification.stdout);
    await capture('06-audit-package', 'Plain monospace HTML shows verbatim live audit UI text and unmodified verifier stdout.', htmlPage);
    await htmlPage.close();
    evidence.local_http_responses = responses;
  }

  if (stage === 'documents') {
    await documentPages();
  }
  await fs.writeFile(evidencePath, JSON.stringify(evidence, null, 2) + '\n');
  const warningCodes = new Set(evidence.analysis?.warnings?.map(w => w.code));
  const resultCaption = warningCodes.has('ISOK_WARNING') && warningCodes.has('MPZP_DISCOVERY_UNAVAILABLE')
    ? `POG is imported; ISOK and municipal MPZP discovery were unavailable. Analysis status: ${evidence.analysis.status}.`
    : `POG is imported; analysis status: ${evidence.analysis?.status}. Missing data is shown explicitly.`;
  const verifiedCount = /OK:\s+(\d+)/.exec(evidence.audit?.verifier_stdout || '')?.[1];
  const descriptions = [
    ['01-analysis-result', 'A parcel outline and general-plan zones appear beside the Polish analysis completeness panel.', resultCaption],
    ['02-map-layers', 'A selected general-plan zone appears on the vector map with planning parameters in the inspector.', 'The inspector displays the selected POG zone and its planning parameters.'],
    ['03-section-statuses', 'Expanded completeness details show checked sections, partial results and missing coverage with explicit labels.', 'Section statuses distinguish missing coverage from a verified absence of restrictions.'],
    ['04-empty-state', 'The map panel explains that the general-plan layer is unavailable because no plans have been imported.', 'Before import, the POG panel explicitly reports that no general plans have been imported.'],
    ['05-pdf-report', 'The first two complete pages of the downloaded PDF report show parcel details and the analysis summary.', 'The downloaded PDF report v2 preserves the parcel details and completeness summary.'],
    ['06-audit-package', 'The audit download text and its SHA-256 appear beside the unmodified output of the Python verifier.', `The real verifier confirms ${verifiedCount} files and matches the package SHA-256 shown by the application.`],
  ];
  if (descriptions.every(([name]) => evidence.captures.some(c => c.name === name))) {
    const screenshots = descriptions.map(([name, alt, caption]) => ({ file: evidence.captures.find(c => c.name === name).file, alt, caption }));
    await fs.writeFile(path.join(output, 'screenshots.json'), JSON.stringify({ cover: screenshots[0], screenshots }, null, 2) + '\n');
  }
} finally { await browser.close(); }
