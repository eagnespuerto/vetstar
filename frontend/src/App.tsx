import { useCallback, useState } from "react";
import {
  analyze,
  downloadReport,
  mastAnalyze,
  mastReport,
  mastSectors,
  type SectorInfo,
  type DetectParams,
} from "./api";
import type { VettingResult } from "./types";

type Status = "idle" | "uploading" | "analyzing" | "done" | "error";
type Mode = "upload" | "mast";

const REPO_URL = "https://github.com/eagnespuerto/vetstar";

export default function App() {
  const [mode, setMode] = useState<Mode>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VettingResult | null>(null);
  const [drag, setDrag] = useState(false);

  // Detection sensitivity (shared across both modes).
  const [params, setParams] = useState<DetectParams>({ threshold: 0.997, minSnr: 4.0 });

  // MAST mode state
  const [ticInput, setTicInput] = useState<string>("");
  const [sectorInput, setSectorInput] = useState<string>("");
  const [availableSectors, setAvailableSectors] = useState<SectorInfo[] | null>(null);
  const [sectorLookupLoading, setSectorLookupLoading] = useState(false);

  const onFile = (f: File) => {
    setFile(f);
    setError(null);
    setResult(null);
    setStatus("idle");
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files?.[0]) onFile(e.dataTransfer.files[0]);
  }, []);

  const runAnalyze = async () => {
    if (!file) return;
    setStatus("analyzing");
    setError(null);
    try {
      const r = await analyze(file, params);
      setResult(r);
      setStatus("done");
    } catch (e: any) {
      setError(e.message || String(e));
      setStatus("error");
    }
  };

  const runReport = async () => {
    if (!file) return;
    try {
      const blob = await downloadReport(file, params);
      triggerDownload(blob, `vetting_TIC${result?.star.tic_id || "report"}.pdf`);
    } catch (e: any) {
      setError(e.message || String(e));
    }
  };

  // -- MAST mode handlers --
  const lookupSectors = async () => {
    const tic = parseInt(ticInput);
    if (!tic) return;
    setSectorLookupLoading(true);
    setError(null);
    try {
      const sectors = await mastSectors(tic);
      setAvailableSectors(sectors);
      if (sectors.length && !sectorInput) {
        setSectorInput(String(sectors[sectors.length - 1].sector));
      }
    } catch (e: any) {
      setError(e.message || String(e));
      setAvailableSectors(null);
    } finally {
      setSectorLookupLoading(false);
    }
  };

  const runMastAnalyze = async () => {
    const tic = parseInt(ticInput);
    const sec = parseInt(sectorInput);
    if (!tic || !sec) {
      setError("Enter both TIC and sector.");
      return;
    }
    setStatus("analyzing");
    setError(null);
    setResult(null);
    try {
      const r = await mastAnalyze(tic, sec, params);
      setResult(r);
      setStatus("done");
    } catch (e: any) {
      setError(e.message || String(e));
      setStatus("error");
    }
  };

  const runMastReport = async () => {
    const tic = parseInt(ticInput);
    const sec = parseInt(sectorInput);
    if (!tic || !sec) return;
    try {
      const blob = await mastReport(tic, sec, params);
      triggerDownload(blob, `vetting_TIC${tic}_S${String(sec).padStart(3, "0")}.pdf`);
    } catch (e: any) {
      setError(e.message || String(e));
    }
  };

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white py-4 px-6 shadow">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold">Vetstar: TESS Vetting Studio Alpha</h1>
            <p className="text-sm text-slate-300">
              Upload a SPOC light curve (FITS) or pull one from MAST by TIC + sector
            </p>
          </div>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 inline-flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded text-sm font-medium transition"
            title="View on GitHub, report a bug, or contribute"
          >
            <svg
              viewBox="0 0 16 16"
              width="16"
              height="16"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
            </svg>
            Report an Issue or Contribute
          </a>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {/* Mode tabs */}
        <div className="flex gap-2 border-b">
          <TabButton active={mode === "upload"} onClick={() => setMode("upload")}>
            Upload file
          </TabButton>
          <TabButton active={mode === "mast"} onClick={() => setMode("mast")}>
            Fetch from MAST
          </TabButton>
        </div>

        {/* Detection sensitivity (collapsed by default) */}
        <SensitivityPanel params={params} setParams={setParams} />

        {/* Upload mode */}
        {mode === "upload" && (
          <section
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            className={`rounded-lg border-2 border-dashed bg-white p-8 transition ${
              drag ? "border-blue-500 bg-blue-50" : "border-slate-300"
            }`}
          >
            <div className="text-center space-y-3">
              <p className="text-slate-700 font-medium">
                Drop a <code>.fits</code> / <code>.fits.gz</code> / <code>.json</code> /{" "}
                <code>.customization</code> file here
              </p>
              <p className="text-xs text-slate-500">or</p>
              <input
                type="file"
                accept=".fits,.gz,.json,.customization"
                onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
                className="block mx-auto text-sm"
              />
              {file && (
                <p className="text-sm text-slate-600">
                  Selected: <span className="font-mono">{file.name}</span> (
                  {(file.size / 1024 / 1024).toFixed(2)} MB)
                </p>
              )}
              <div className="flex justify-center gap-3 mt-2">
                <button
                  onClick={runAnalyze}
                  disabled={!file || status === "analyzing"}
                  className="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300"
                >
                  {status === "analyzing" ? "Analyzing…" : "Run vetting"}
                </button>
                <button
                  onClick={runReport}
                  disabled={!file || status === "analyzing"}
                  className="px-4 py-2 bg-emerald-600 text-white rounded font-medium hover:bg-emerald-700 disabled:bg-slate-300"
                >
                  Download PDF report
                </button>
              </div>
            </div>
          </section>
        )}

        {/* MAST mode */}
        {mode === "mast" && (
          <section className="rounded-lg border-2 border-slate-200 bg-white p-6">
            <p className="text-sm text-slate-600 mb-4">
              Enter a TIC ID and sector; the backend will fetch the matching SPOC
              2-min light curve from <code>mast.stsci.edu</code> via{" "}
              <code>astroquery.mast.Observations</code>, then run full vetting.
            </p>
            <div className="grid sm:grid-cols-3 gap-3 items-end">
              <div>
                <label className="block text-xs text-slate-600 mb-1">TIC ID</label>
                <input
                  type="number"
                  placeholder="e.g. 451483379"
                  value={ticInput}
                  onChange={(e) => setTicInput(e.target.value)}
                  className="w-full border rounded px-3 py-2 font-mono text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-600 mb-1">Sector</label>
                <input
                  type="number"
                  placeholder="e.g. 100"
                  value={sectorInput}
                  onChange={(e) => setSectorInput(e.target.value)}
                  className="w-full border rounded px-3 py-2 font-mono text-sm"
                />
              </div>
              <button
                onClick={lookupSectors}
                disabled={!ticInput || sectorLookupLoading}
                className="px-4 py-2 bg-slate-700 text-white rounded font-medium hover:bg-slate-800 disabled:bg-slate-300"
              >
                {sectorLookupLoading ? "Looking up…" : "List sectors"}
              </button>
            </div>
            {availableSectors && (
              <div className="mt-3 text-sm">
                {availableSectors.length === 0 ? (
                  <p className="text-slate-500">No TESS sectors found for this TIC.</p>
                ) : (
                  <div className="text-slate-700 space-y-1">
                    <p className="text-xs text-slate-500">
                      Click a sector. Hover for providers (SPOC = 2-min, best;
                      TESS-SPOC / QLP = FFI fallback, no centroid).
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {availableSectors.map((si) => (
                        <button
                          key={si.sector}
                          onClick={() => setSectorInput(String(si.sector))}
                          title={
                            si.providers.length
                              ? `Providers: ${si.providers.join(", ")}`
                              : ""
                          }
                          className={`px-2 py-0.5 rounded font-mono text-xs ${
                            String(si.sector) === sectorInput
                              ? "bg-blue-600 text-white"
                              : si.providers.includes("SPOC")
                              ? "bg-slate-100 hover:bg-slate-200"
                              : "bg-amber-50 hover:bg-amber-100 text-amber-900"
                          }`}
                        >
                          S{String(si.sector).padStart(3, "0")}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-center gap-3 mt-5">
              <button
                onClick={runMastAnalyze}
                disabled={status === "analyzing" || !ticInput || !sectorInput}
                className="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300"
              >
                {status === "analyzing" ? "Analyzing…" : "Fetch & vet"}
              </button>
              <button
                onClick={runMastReport}
                disabled={status === "analyzing" || !ticInput || !sectorInput}
                className="px-4 py-2 bg-emerald-600 text-white rounded font-medium hover:bg-emerald-700 disabled:bg-slate-300"
              >
                Fetch & download PDF
              </button>
            </div>
          </section>
        )}

        {error && (
          <div className="rounded bg-red-50 border border-red-300 text-red-800 p-4">
            <strong>Error:</strong> {error}
          </div>
        )}

        {status === "analyzing" && (
          <div className="rounded bg-blue-50 border border-blue-200 p-4 text-blue-900">
            Running BLS + Lomb-Scargle + centroid + odd/even + secondary-eclipse
            tests… this can take 10–30 seconds for a 2-min cadence sector.
          </div>
        )}

        {result && <ResultsView result={result} />}
      </main>

      <footer className="text-center text-xs text-slate-400 py-6">
        Pipeline: astropy <code>BoxLeastSquares</code> + <code>LombScargle</code>, scipy
        median filtering, centroid + odd/even + secondary tests, physics-based
        companion sizing.
      </footer>
    </div>
  );
}

function SensitivityPanel({
  params,
  setParams,
}: {
  params: DetectParams;
  setParams: (p: DetectParams) => void;
}) {
  const [open, setOpen] = useState(false);
  const isDefault = params.threshold === 0.997 && params.minSnr === 4.0;

  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2 text-sm hover:bg-slate-50 transition"
      >
        <span className="font-medium text-slate-700">
          ⚙️ Detection sensitivity{" "}
          <span className="text-xs text-slate-500 ml-1">
            {isDefault ? "(defaults)" : `(threshold=${params.threshold}, SNR=${params.minSnr})`}
          </span>
        </span>
        <span className="text-slate-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-4 py-4 border-t border-slate-200 space-y-4 text-sm">
          <p className="text-xs text-slate-600">
            Tune how aggressively the pipeline flags dips in the light curve.
            Defaults work well for typical 2-min cadence stars. Loosen for shallow
            transits on quiet stars; tighten for noisy targets.
          </p>

          <div>
            <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
              <span>
                Depth threshold:{" "}
                <span className="font-mono">{params.threshold.toFixed(4)}</span>
                <span className="text-slate-400 ml-1">
                  (flag dips deeper than {((1 - params.threshold) * 100).toFixed(2)}%)
                </span>
              </span>
              <button
                onClick={() => setParams({ ...params, threshold: 0.997 })}
                className="text-blue-600 hover:underline"
              >
                reset
              </button>
            </label>
            <input
              type="range"
              min={0.95}
              max={0.999}
              step={0.001}
              value={params.threshold}
              onChange={(e) =>
                setParams({ ...params, threshold: parseFloat(e.target.value) })
              }
              className="w-full"
            />
            <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
              <span>0.95 (very loose)</span>
              <span>0.997 default</span>
              <span>0.999 (very strict)</span>
            </div>
          </div>

          <div>
            <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
              <span>
                Minimum SNR:{" "}
                <span className="font-mono">{params.minSnr.toFixed(1)}σ</span>
                <span className="text-slate-400 ml-1">
                  (dips must exceed this × local scatter)
                </span>
              </span>
              <button
                onClick={() => setParams({ ...params, minSnr: 4.0 })}
                className="text-blue-600 hover:underline"
              >
                reset
              </button>
            </label>
            <input
              type="range"
              min={1.0}
              max={10.0}
              step={0.5}
              value={params.minSnr}
              onChange={(e) =>
                setParams({ ...params, minSnr: parseFloat(e.target.value) })
              }
              className="w-full"
            />
            <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
              <span>1σ (max sensitivity)</span>
              <span>4σ default</span>
              <span>10σ (very strict)</span>
            </div>
          </div>

          <p className="text-xs text-slate-500 italic">
            Tip: if real shallow transits are being missed, lower SNR first. If
            noise spikes are being flagged as events, raise SNR.
          </p>
        </div>
      )}
    </section>
  );
}


function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition ${
        active
          ? "border-blue-600 text-blue-700"
          : "border-transparent text-slate-500 hover:text-slate-800"
      }`}
    >
      {children}
    </button>
  );
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function ResultsView({ result }: { result: VettingResult }) {
  const verdictColor = {
    planet_candidate: "bg-emerald-100 border-emerald-400 text-emerald-900",
    eclipsing_binary_candidate: "bg-amber-100 border-amber-400 text-amber-900",
    false_positive_blend: "bg-rose-100 border-rose-400 text-rose-900",
    ambiguous: "bg-slate-100 border-slate-400 text-slate-900",
    no_signal: "bg-slate-50 border-slate-300 text-slate-700",
  }[result.verdict.category] || "bg-slate-100 border-slate-400 text-slate-900";

  return (
    <div className="space-y-6">
      {result.mast && (
        <div
          className={`text-sm rounded border p-3 ${
            result.mast.fallback
              ? "bg-amber-50 border-amber-300 text-amber-900"
              : "bg-blue-50 border-blue-200 text-blue-900"
          }`}
        >
          <strong>Data source:</strong>{" "}
          <code>{result.mast.author}</code> ({Math.round(result.mast.exptime ?? 0)} s
          cadence) &middot; <span className="font-mono text-xs">{result.mast.filename}</span>
          {result.mast.fallback && (
            <span className="block mt-1 text-xs">
              SPOC 2-min wasn't available for this TIC+sector. Falling back to{" "}
              {result.mast.author} — the centroid (background-blend) test will be
              skipped because FFI products don't include centroid columns.
            </span>
          )}
        </div>
      )}
      {/* Verdict */}
      <section className={`rounded-lg border-2 p-5 ${verdictColor}`}>
        <p className="text-xs uppercase tracking-wide opacity-70">Verdict</p>
        <h2 className="text-2xl font-bold mt-1">{result.verdict.headline}</h2>
        <p className="text-sm mt-1">
          Category: <code>{result.verdict.category}</code> &nbsp;·&nbsp; Confidence:{" "}
          {(result.verdict.confidence * 100).toFixed(0)}%
        </p>
        <ul className="mt-3 space-y-1 text-sm">
          {result.verdict.reasons.map((r, i) => (
            <li key={i}>• {r}</li>
          ))}
        </ul>
        {result.verdict.flags.length > 0 && (
          <p className="mt-2 text-xs">
            Flags:{" "}
            {result.verdict.flags.map((f) => (
              <span
                key={f}
                className="inline-block mr-1 px-2 py-0.5 bg-white/60 rounded font-mono"
              >
                {f}
              </span>
            ))}
          </p>
        )}
      </section>

      {/* Star + Summary */}
      <div className="grid md:grid-cols-2 gap-4">
        <KV title="Stellar parameters" data={result.star} />
        <KV title="Light curve summary" data={result.summary} />
      </div>

      {/* Plots */}
      <PlotsSection plots={result.plots} />

      {/* Tests */}
      <div className="grid md:grid-cols-2 gap-4">
        <KV title="BLS" data={result.bls} hide={["_periodogram"]} />
        <KV title="Lomb-Scargle" data={result.lomb_scargle} hide={["top_peaks"]} />
        <KV title="Centroid (background-blend test)" data={result.centroid} />
        <KV title="Transit shape" data={result.shape} />
        <KV title="Odd / even depths" data={result.odd_even} />
        <KV title="Secondary eclipse" data={result.secondary} />
        <KV title="Physics" data={result.physics} />
      </div>

      {/* Events */}
      <section className="bg-white rounded-lg shadow p-5">
        <h3 className="font-bold mb-3">Discrete dip events ({result.events.length})</h3>
        {result.events.length === 0 ? (
          <p className="text-sm text-slate-500">No discrete events detected.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-slate-600">
              <tr>
                <th className="py-1">#</th>
                <th>t_start</th>
                <th>t_end</th>
                <th>Duration (h)</th>
                <th>Depth (%)</th>
              </tr>
            </thead>
            <tbody>
              {result.events.map((e, i) => (
                <tr key={i} className="border-b">
                  <td className="py-1">{i + 1}</td>
                  <td className="font-mono">{e.t_start.toFixed(3)}</td>
                  <td className="font-mono">{e.t_end.toFixed(3)}</td>
                  <td>{(e.duration_d * 24).toFixed(2)}</td>
                  <td>{(e.depth * 100).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function PlotsSection({ plots }: { plots: Record<string, string> }) {
  const order = ["lightcurve", "event_zoom", "centroid", "bls", "lomb_scargle"];
  const labels: Record<string, string> = {
    lightcurve: "Full detrended light curve",
    event_zoom: "Event zoom",
    centroid: "Centroid behaviour",
    bls: "BLS periodogram",
    lomb_scargle: "Lomb-Scargle top peaks",
  };
  return (
    <section className="bg-white rounded-lg shadow p-5 space-y-4">
      <h3 className="font-bold">Diagnostic plots</h3>
      {order.map((k) =>
        plots[k] ? (
          <figure key={k}>
            <figcaption className="text-sm text-slate-600 mb-1">{labels[k]}</figcaption>
            <img
              src={`data:image/png;base64,${plots[k]}`}
              alt={labels[k]}
              className="w-full rounded border"
            />
          </figure>
        ) : null
      )}
    </section>
  );
}

function KV({
  title,
  data,
  hide = [],
}: {
  title: string;
  data: Record<string, any>;
  hide?: string[];
}) {
  const entries = Object.entries(data).filter(
    ([k, v]) =>
      !hide.includes(k) &&
      !k.startsWith("_") &&
      v !== null &&
      v !== undefined &&
      typeof v !== "object"
  );
  return (
    <section className="bg-white rounded-lg shadow p-5">
      <h3 className="font-bold mb-2">{title}</h3>
      {entries.length === 0 ? (
        <p className="text-sm text-slate-500">No data.</p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
          {entries.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-slate-600 font-mono text-xs">{k}</dt>
              <dd className="font-mono">{formatVal(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function formatVal(v: any): string {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    if (Math.abs(v) < 1e-4 && v !== 0) return v.toExponential(3);
    if (Math.abs(v) >= 1e5) return v.toExponential(3);
    return Number.isInteger(v) ? v.toString() : v.toFixed(4);
  }
  return String(v);
}
