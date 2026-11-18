/**
 * Build an ExoFOP-TESS bulk-upload archive with the app's own builder, for
 * the compliance check in backend/tests/test_exofop_archive.py.
 * Usage: node exofop-check.mjs <result.json> <out.zip>
 */
import { readFileSync, writeFileSync } from "node:fs";
import { buildExofopArchive, type ExofopImage } from "../src/ExofopBulkPanel";

const [, , resultPath, outPath] = process.argv;
const r = JSON.parse(readFileSync(resultPath, "utf8"));
const f = r.figures;
const pdf = new TextEncoder().encode("%PDF-1.4 test report");
const images: ExofopImage[] = [
  { key: "nemesis-lc", label: "NEMESIS detrended light curve", b64: f.light_curve, code: "L" },
  { key: "nemesis-tls-fold", label: "NEMESIS TLS phase-folded transit | with residuals", b64: f.fold, code: "L" },
  { key: "nemesis-odd-even", label: "NEMESIS odd and even transits", b64: f.odd_even, code: "L" },
  { key: "nemesis-tls-periodogram", label: "NEMESIS TLS periodogram — 1.9 R⊕", b64: f.periodogram, code: "O" },
  { key: "nemesis-vetting-report", label: "Vetstar NEMESIS vetting report", b64: "", ext: "pdf", code: "O",
    fetchBytes: async () => pdf },
  { key: "nemesis-vetting-report", label: "duplicate key on purpose", b64: "", ext: "pdf", code: "O",
    fetchBytes: async () => pdf },
];
// 23:30 UTC on 18 Nov is already 19 Nov in UTC+8: the file date must stay 20261118.
const a = await buildExofopArchive({
  ticId: 103633434, sector: 20, initials: "ea", counter: "007", tag: "20261118_puerto_vetstar_001",
  images, now: new Date(Date.UTC(2026, 10, 18, 23, 30)),
});
if (!a) throw new Error("no archive");
writeFileSync(outPath, new Uint8Array(await a.zip.arrayBuffer()));
console.log(a.zipName, a.entries.length);
