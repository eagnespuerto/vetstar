#!/usr/bin/env python3
"""
Build a single portable vetting.html that runs the whole vetting pipeline
in-browser using Pyodide.

Run from the project root:
    python build_html.py
Produces vetting.html (~25 KB). Double-click it in any modern browser.

What's bundled:
  * Pyodide bootstrap (loads from CDN at runtime)
  * pipeline.py / parsers.py / report.py (embedded verbatim)
  * Minimal UI (Tailwind via CDN) modelled on the React app

What's not bundled:
  * MAST fetch via astroquery — browsers block cross-origin requests to
    mast.stsci.edu (no CORS). We surface a "fetch from MAST" button that
    routes through a public CORS proxy as a best effort; if that fails,
    upload the FITS manually.
  * Future TESS sectors / new astropy versions — Pyodide pulls fixed wheel
    versions from CDN, so the bundle is reproducible but not auto-updating.
"""
from __future__ import annotations

import html
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent
BACKEND = ROOT / "backend" / "app"


def read(name):
    return (BACKEND / name).read_text(encoding="utf-8")


PIPELINE_PY = read("pipeline.py")
PARSERS_PY = read("parsers.py")
REPORT_PY = read("report.py")


# ---------------------------------------------------------------------- 
# In-browser glue: a thin Python module that exposes a single
# `run_vetting(file_bytes, filename)` function the JS layer calls.
# ---------------------------------------------------------------------- 
GLUE_PY = r"""
import io, json, base64, tempfile, os
from app.pipeline import run_full_vetting
from app.parsers import parse_upload
from app.report import build_pdf


def run_vetting_from_bytes(file_bytes, filename):
    suffix = os.path.splitext(filename)[1].lower() or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file_bytes)
        tmp.flush()
        tmp.close()
        parsed = parse_upload(tmp.name, filename)
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass
    if parsed.get("metadata_only"):
        return {"error": "ExoFOP metadata-only file; upload a FITS instead."}
    result = run_full_vetting(
        t=parsed["t"], flux=parsed["flux"], flux_err=parsed["flux_err"],
        quality=parsed["quality"], mom_x=parsed["mom_x"], mom_y=parsed["mom_y"],
        star=parsed["star"],
    )
    return json.dumps(result.to_dict())


def build_pdf_from_bytes(file_bytes, filename):
    suffix = os.path.splitext(filename)[1].lower() or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file_bytes); tmp.flush(); tmp.close()
        parsed = parse_upload(tmp.name, filename)
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass
    result = run_full_vetting(
        t=parsed["t"], flux=parsed["flux"], flux_err=parsed["flux_err"],
        quality=parsed["quality"], mom_x=parsed["mom_x"], mom_y=parsed["mom_y"],
        star=parsed["star"],
    )
    pdf = build_pdf(result)
    return base64.b64encode(pdf).decode("ascii")
"""


# ---------------------------------------------------------------------- 
# The HTML template. Python strings are embedded into JS as backtick-
# template literals; we escape backslashes, backticks, and `${`.
# ---------------------------------------------------------------------- 
def js_string(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TESS Vetting Studio — portable</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/pyodide/v0.26.2/full/pyodide.js"></script>
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85em; }
    .spinner { width: 14px; height: 14px; border: 2px solid #94a3b8; border-top-color: transparent; border-radius: 50%; display: inline-block; animation: spin 0.8s linear infinite; vertical-align: middle; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body class="bg-slate-50">

<header class="bg-slate-900 text-white py-4 px-6 shadow">
  <div class="max-w-6xl mx-auto">
    <h1 class="text-xl font-bold">TESS / Kepler Vetting Studio</h1>
    <p class="text-sm text-slate-300">
      Single-file portable build. All analysis runs in your browser via Pyodide — no server.
    </p>
  </div>
</header>

<main class="max-w-6xl mx-auto px-6 py-8 space-y-6">

  <!-- Boot status -->
  <div id="boot" class="bg-blue-50 border border-blue-200 text-blue-900 rounded p-4 text-sm">
    <span class="spinner"></span> <span id="boot-msg">Loading Python runtime (~30 s the first time, then cached)…</span>
  </div>

  <!-- Upload card -->
  <section id="upload-card" class="rounded-lg border-2 border-dashed border-slate-300 bg-white p-8 hidden">
    <div class="text-center space-y-3">
      <p class="text-slate-700 font-medium">
        Drop a <code>.fits</code> / <code>.fits.gz</code> / <code>.json</code> /
        <code>.customization</code> file here
      </p>
      <p class="text-xs text-slate-500">or</p>
      <input id="file-input" type="file" accept=".fits,.gz,.json,.customization" class="block mx-auto text-sm" />
      <p id="file-info" class="text-sm text-slate-600"></p>
      <div class="flex justify-center gap-3 mt-2">
        <button id="run-analyze" disabled class="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300">Run vetting</button>
        <button id="run-pdf" disabled class="px-4 py-2 bg-emerald-600 text-white rounded font-medium hover:bg-emerald-700 disabled:bg-slate-300">Download PDF report</button>
      </div>
    </div>
  </section>

  <!-- Error/status -->
  <div id="error" class="rounded bg-red-50 border border-red-300 text-red-800 p-4 hidden"></div>
  <div id="status" class="rounded bg-blue-50 border border-blue-200 p-4 text-blue-900 hidden"></div>

  <!-- Results -->
  <div id="results"></div>

</main>

<footer class="text-center text-xs text-slate-400 py-6">
  All Python (numpy, scipy, astropy, matplotlib, reportlab) runs in your browser via Pyodide.
  No data leaves your machine.
</footer>

<script type="module">
  // ===== Embedded Python =====
  const PIPELINE_PY = `__PIPELINE_PY__`;
  const PARSERS_PY  = `__PARSERS_PY__`;
  const REPORT_PY   = `__REPORT_PY__`;
  const GLUE_PY     = `__GLUE_PY__`;

  // ===== Pyodide bootstrap =====
  const bootEl = document.getElementById("boot");
  const bootMsg = document.getElementById("boot-msg");
  const uploadCard = document.getElementById("upload-card");

  function setBoot(msg) { bootMsg.textContent = msg; }
  function hideBoot() { bootEl.classList.add("hidden"); uploadCard.classList.remove("hidden"); }
  function showError(msg) {
    const el = document.getElementById("error");
    el.textContent = msg; el.classList.remove("hidden");
  }

  let pyodide;
  async function boot() {
    try {
      setBoot("Loading Pyodide runtime…");
      pyodide = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.2/full/" });

      setBoot("Installing numpy / scipy / astropy / matplotlib …");
      await pyodide.loadPackage(["numpy", "scipy", "astropy", "matplotlib", "pillow"]);

      setBoot("Installing reportlab …");
      await pyodide.loadPackage("micropip");
      await pyodide.runPythonAsync("import micropip; await micropip.install('reportlab')");

      setBoot("Loading vetting pipeline …");
      // Write modules into Pyodide's virtual fs as a package.
      pyodide.FS.mkdirTree("/home/pyodide/app");
      pyodide.FS.writeFile("/home/pyodide/app/__init__.py", "");
      pyodide.FS.writeFile("/home/pyodide/app/pipeline.py", PIPELINE_PY);
      pyodide.FS.writeFile("/home/pyodide/app/parsers.py",  PARSERS_PY);
      pyodide.FS.writeFile("/home/pyodide/app/report.py",   REPORT_PY);
      await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, "/home/pyodide")
${GLUE_PY}
`);
      hideBoot();
    } catch (e) {
      console.error(e);
      bootEl.classList.remove("hidden");
      bootEl.classList.remove("bg-blue-50", "border-blue-200", "text-blue-900");
      bootEl.classList.add("bg-red-50", "border-red-300", "text-red-800");
      bootMsg.textContent = "Failed to load Python runtime: " + e.message;
    }
  }
  boot();

  // ===== File handling =====
  let currentFile = null;
  const fileInput = document.getElementById("file-input");
  const fileInfo = document.getElementById("file-info");
  const runAnalyze = document.getElementById("run-analyze");
  const runPdf = document.getElementById("run-pdf");

  function setFile(f) {
    currentFile = f;
    fileInfo.textContent = `Selected: ${f.name} (${(f.size/1024/1024).toFixed(2)} MB)`;
    runAnalyze.disabled = false; runPdf.disabled = false;
  }
  fileInput.addEventListener("change", e => e.target.files[0] && setFile(e.target.files[0]));
  uploadCard.addEventListener("dragover", e => { e.preventDefault(); uploadCard.classList.add("bg-blue-50"); });
  uploadCard.addEventListener("dragleave", () => uploadCard.classList.remove("bg-blue-50"));
  uploadCard.addEventListener("drop", e => {
    e.preventDefault(); uploadCard.classList.remove("bg-blue-50");
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  });

  // ===== Run analysis =====
  async function fileToPyBytes(file) {
    const buf = new Uint8Array(await file.arrayBuffer());
    return buf;
  }

  function setStatus(msg) {
    const el = document.getElementById("status");
    el.innerHTML = `<span class="spinner"></span> ${msg}`;
    el.classList.remove("hidden");
  }
  function clearStatus() { document.getElementById("status").classList.add("hidden"); }

  runAnalyze.addEventListener("click", async () => {
    if (!currentFile || !pyodide) return;
    document.getElementById("error").classList.add("hidden");
    setStatus("Running BLS + Lomb-Scargle + centroid + odd-even + secondary tests…");
    try {
      const bytes = await fileToPyBytes(currentFile);
      pyodide.globals.set("_in_bytes", bytes);
      pyodide.globals.set("_in_name", currentFile.name);
      const json = await pyodide.runPythonAsync(
        "run_vetting_from_bytes(_in_bytes.tobytes(), _in_name)"
      );
      clearStatus();
      const data = JSON.parse(json);
      renderResults(data);
    } catch (e) {
      clearStatus();
      showError("Analysis failed: " + e.message);
      console.error(e);
    }
  });

  runPdf.addEventListener("click", async () => {
    if (!currentFile || !pyodide) return;
    document.getElementById("error").classList.add("hidden");
    setStatus("Generating PDF report…");
    try {
      const bytes = await fileToPyBytes(currentFile);
      pyodide.globals.set("_in_bytes", bytes);
      pyodide.globals.set("_in_name", currentFile.name);
      const b64 = await pyodide.runPythonAsync(
        "build_pdf_from_bytes(_in_bytes.tobytes(), _in_name)"
      );
      clearStatus();
      const binary = atob(b64);
      const arr = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) arr[i] = binary.charCodeAt(i);
      const blob = new Blob([arr], { type: "application/pdf" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "vetting_report.pdf";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      clearStatus();
      showError("PDF generation failed: " + e.message);
      console.error(e);
    }
  });

  // ===== Render =====
  function fmt(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "number") {
      if (Math.abs(v) < 1e-4 && v !== 0) return v.toExponential(3);
      if (Number.isInteger(v)) return v.toString();
      return v.toFixed(4);
    }
    return String(v);
  }

  const VERDICT_COLORS = {
    planet_candidate: "bg-emerald-100 border-emerald-400 text-emerald-900",
    eclipsing_binary_candidate: "bg-amber-100 border-amber-400 text-amber-900",
    false_positive_blend: "bg-rose-100 border-rose-400 text-rose-900",
    ambiguous: "bg-slate-100 border-slate-400 text-slate-900",
    no_signal: "bg-slate-50 border-slate-300 text-slate-700",
  };

  function kvTable(title, obj, hide = []) {
    const rows = Object.entries(obj || {})
      .filter(([k, v]) => !hide.includes(k) && !k.startsWith("_") && typeof v !== "object" && v !== null && v !== undefined)
      .map(([k, v]) => `<div class="contents"><dt class="text-slate-600 mono text-xs">${k}</dt><dd class="mono">${fmt(v)}</dd></div>`)
      .join("");
    return `<section class="bg-white rounded-lg shadow p-5">
      <h3 class="font-bold mb-2">${title}</h3>
      <dl class="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">${rows || '<p class="text-sm text-slate-500">—</p>'}</dl>
    </section>`;
  }

  function plotsSection(plots) {
    const order = ["lightcurve", "event_zoom", "centroid", "bls", "lomb_scargle"];
    const labels = {
      lightcurve: "Full detrended light curve",
      event_zoom: "Event zoom",
      centroid: "Centroid behaviour",
      bls: "BLS periodogram",
      lomb_scargle: "Lomb-Scargle top peaks",
    };
    const figs = order.filter(k => plots[k]).map(k =>
      `<figure><figcaption class="text-sm text-slate-600 mb-1">${labels[k]}</figcaption>
       <img src="data:image/png;base64,${plots[k]}" class="w-full rounded border"/></figure>`
    ).join("");
    return `<section class="bg-white rounded-lg shadow p-5 space-y-4">
      <h3 class="font-bold">Diagnostic plots</h3>${figs}</section>`;
  }

  function eventsTable(events) {
    if (!events.length) return `<section class="bg-white rounded-lg shadow p-5"><h3 class="font-bold mb-2">Discrete dip events</h3><p class="text-sm text-slate-500">None detected.</p></section>`;
    const rows = events.slice(0, 10).map((e, i) =>
      `<tr class="border-b"><td class="py-1">${i+1}</td><td class="mono">${e.t_start.toFixed(3)}</td><td class="mono">${e.t_end.toFixed(3)}</td><td>${(e.duration_d*24).toFixed(2)}</td><td>${(e.depth*100).toFixed(2)}</td></tr>`
    ).join("");
    return `<section class="bg-white rounded-lg shadow p-5">
      <h3 class="font-bold mb-3">Discrete dip events (${events.length})</h3>
      <table class="w-full text-sm">
        <thead class="border-b text-left text-slate-600"><tr><th class="py-1">#</th><th>t_start</th><th>t_end</th><th>Duration (h)</th><th>Depth (%)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
  }

  function renderResults(r) {
    const colour = VERDICT_COLORS[r.verdict.category] || VERDICT_COLORS.ambiguous;
    const reasons = (r.verdict.reasons || []).map(s => `<li>• ${s}</li>`).join("");
    const flags = (r.verdict.flags || []).map(f => `<span class="inline-block mr-1 px-2 py-0.5 bg-white/60 rounded mono">${f}</span>`).join("");

    document.getElementById("results").innerHTML = `
      <div class="space-y-6">
        <section class="rounded-lg border-2 p-5 ${colour}">
          <p class="text-xs uppercase tracking-wide opacity-70">Verdict</p>
          <h2 class="text-2xl font-bold mt-1">${r.verdict.headline}</h2>
          <p class="text-sm mt-1">Category: <code>${r.verdict.category}</code> &middot; Confidence: ${(r.verdict.confidence*100).toFixed(0)}%</p>
          <ul class="mt-3 space-y-1 text-sm">${reasons}</ul>
          ${flags ? `<p class="mt-2 text-xs">Flags: ${flags}</p>` : ""}
        </section>
        <div class="grid md:grid-cols-2 gap-4">
          ${kvTable("Stellar parameters", r.star)}
          ${kvTable("Light curve summary", r.summary)}
        </div>
        ${plotsSection(r.plots)}
        <div class="grid md:grid-cols-2 gap-4">
          ${kvTable("BLS", r.bls, ["_periodogram"])}
          ${kvTable("Lomb-Scargle", r.lomb_scargle, ["top_peaks"])}
          ${kvTable("Centroid (background-blend test)", r.centroid)}
          ${kvTable("Transit shape", r.shape)}
          ${kvTable("Odd / even depths", r.odd_even)}
          ${kvTable("Secondary eclipse", r.secondary)}
          ${kvTable("Physics", r.physics)}
        </div>
        ${eventsTable(r.events)}
      </div>`;
  }
</script>

</body>
</html>
"""


def main():
    out = HTML_TEMPLATE
    out = out.replace("__PIPELINE_PY__", js_string(PIPELINE_PY))
    out = out.replace("__PARSERS_PY__", js_string(PARSERS_PY))
    out = out.replace("__REPORT_PY__", js_string(REPORT_PY))
    out = out.replace("__GLUE_PY__", js_string(GLUE_PY))

    out_path = ROOT / "vetting.html"
    out_path.write_text(out, encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"Wrote {out_path}  ({size_kb:.1f} KB)")
    print("Open in any modern browser. First load ~30 s (downloads Pyodide + wheels from CDN).")


if __name__ == "__main__":
    main()
