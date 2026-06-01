import { useCallback, useEffect, useRef, useState } from "react";
import {
  analyze,
  downloadReport,
  fetchHabitability,
  fetchObservables,
  fetchMultisector,
  fetchRadialVelocity,
  fitsDownloadUrl,
  mastAnalyze,
  mastReport,
  mastSectors,
  multisectorReport,
  type SectorInfo,
  type DetectParams,
} from "./api";
import type { VettingResult, DvtResult } from "./types";
import ExoMinerPanel from "./ExoMinerPanel";
import FfiCutoutPanel from "./FfiCutoutPanel";
import ManualDipSelector from "./ManualDipSelector";
import { ShareToImgbbButton, ShareIcon } from "./ShareButton";

type Status = "idle" | "uploading" | "analyzing" | "done" | "error";
type Mode = "upload" | "mast";

// Funny-but-relevant loading messages cycled while the user waits.
const VETTING_LOADING_MSGS = [
  "Contacting aliens for a second opinion…",
  "Defusing gamma-ray pulsars…",
  "Feeding the black holes…",
  "Folding light curves into tiny paper cranes…",
  "Asking the star to hold still…",
  "Bribing photons to arrive on time…",
  "Untangling odd transits from even ones…",
  "Running BLS + Lomb-Scargle + centroid + odd/even + secondary tests…",
  "Politely interrogating the centroid…",
  "Checking if it's a planet or just a clingy binary…",
];

const HCI_LOADING_MSGS = [
  "Measuring the temperature of distant rocks…",
  "Surveying the habitable zone for vacancy signs…",
  "Asking ExoFOP if anyone's home…",
  "Calculating odds of decent weather…",
  "Consulting the STEHM oracle…",
  "Checking the Goldilocks paperwork…",
  "Estimating commute time to the nearest star…",
  "Sniffing for liquid water…",
];

function CyclingLoader({
  messages,
  intervalMs = 2200,
  className = "",
}: {
  messages: string[];
  intervalMs?: number;
  className?: string;
}) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % messages.length), intervalMs);
    return () => clearInterval(id);
  }, [messages, intervalMs]);
  return (
    <span className={`inline-flex items-center ${className}`}>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 640 640"
        className="w-3.5 h-3.5 mr-2 shrink-0 block animate-spin"
        fill="currentColor"
        aria-hidden="true"
      >
        <path d="M272 112C272 85.5 293.5 64 320 64C346.5 64 368 85.5 368 112C368 138.5 346.5 160 320 160C293.5 160 272 138.5 272 112zM272 528C272 501.5 293.5 480 320 480C346.5 480 368 501.5 368 528C368 554.5 346.5 576 320 576C293.5 576 272 554.5 272 528zM112 272C138.5 272 160 293.5 160 320C160 346.5 138.5 368 112 368C85.5 368 64 346.5 64 320C64 293.5 85.5 272 112 272zM480 320C480 293.5 501.5 272 528 272C554.5 272 576 293.5 576 320C576 346.5 554.5 368 528 368C501.5 368 480 346.5 480 320zM139 433.1C157.8 414.3 188.1 414.3 206.9 433.1C225.7 451.9 225.7 482.2 206.9 501C188.1 519.8 157.8 519.8 139 501C120.2 482.2 120.2 451.9 139 433.1zM139 139C157.8 120.2 188.1 120.2 206.9 139C225.7 157.8 225.7 188.1 206.9 206.9C188.1 225.7 157.8 225.7 139 206.9C120.2 188.1 120.2 157.8 139 139zM501 433.1C519.8 451.9 519.8 482.2 501 501C482.2 519.8 451.9 519.8 433.1 501C414.3 482.2 414.3 451.9 433.1 433.1C451.9 414.3 482.2 414.3 501 433.1z" />
      </svg>
      {messages[i]}
    </span>
  );
}

const REPO_URL = "https://github.com/eagnespuerto/vetstar";
const KOFI_URL = "https://ko-fi.com/eagnespuerto";

export default function App() {
  const [mode, setMode] = useState<Mode>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VettingResult | null>(null);
  const [drag, setDrag] = useState(false);

  // Detection sensitivity (shared across both modes).
  const [params, setParams] = useState<DetectParams>({
    threshold: 0.997,
    minSnr: 4.0,
    highVariability: false,
    rotationPeriod: null,
    secondarySigma: 3.0,
  });

  // MAST mode state
  const [ticInput, setTicInput] = useState<string>("");
  const [sectorInput, setSectorInput] = useState<string>("");
  const [availableSectors, setAvailableSectors] = useState<SectorInfo[] | null>(null);
  const [sectorLookupLoading, setSectorLookupLoading] = useState(false);

  // Analysis scope chosen up front: single sector vs multi-sector (≤5).
  const [scope, setScope] = useState<"single" | "multi">("single");
  // Sectors selected for a multi-sector run (max 5). Empty = backend picks newest 5.
  const [multiSectors, setMultiSectors] = useState<number[]>([]);
  const [msData, setMsData] = useState<any>(null);
  const [msTopLoading, setMsTopLoading] = useState(false);

  const MAX_MULTI_SECTORS = 5;
  const toggleMultiSector = (s: number) => {
    setMultiSectors((prev) =>
      prev.includes(s)
        ? prev.filter((x) => x !== s)
        : prev.length >= MAX_MULTI_SECTORS
        ? prev
        : [...prev, s]
    );
  };

  const runMultisectorTop = async () => {
    const tic = parseInt(ticInput);
    if (!tic) {
      setError("Enter a TIC ID.");
      return;
    }
    setStatus("analyzing");
    setError(null);
    setMsData(null);
    setMsTopLoading(true);
    try {
      const data = await fetchMultisector(tic, params, multiSectors);
      setMsData(data);
      setStatus("done");
    } catch (e: any) {
      setError(e.message || String(e));
      setStatus("error");
    } finally {
      setMsTopLoading(false);
    }
  };

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
            <h1 className="text-xl font-bold">Vetstar Alpha v0.1.3</h1>
            <p className="text-sm text-slate-300">
              Upload a SPOC light curve (FITS) or pull one from MAST by TIC + sector
            </p>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded text-sm font-medium transition"
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
            <a
              href={KOFI_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-2 bg-[#ff5e5b] hover:bg-[#e54d4a] border border-[#ff7a78] rounded text-sm font-medium transition"
              title="Support Vetstar — buy me a Ko-fi"
            >
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M3 4h14.5a4.5 4.5 0 0 1 0 9H17a6 6 0 0 1-6 5H8a5 5 0 0 1-5-5V4zm14 7h.5a2.5 2.5 0 0 0 0-5H17v5zM2 20h16v2H2v-2z" />
              </svg>
              Buy me a Ko-fi
            </a>
          </div>
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

            {/* Analysis scope — choose up front */}
            <div className="mb-4">
              <label className="block text-xs text-slate-600 mb-1">Analysis scope</label>
              <div className="inline-flex rounded-lg border border-slate-200 overflow-hidden">
                <button
                  onClick={() => setScope("single")}
                  className={`px-4 py-1.5 text-sm font-medium ${
                    scope === "single" ? "bg-blue-600 text-white" : "bg-white text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  Single sector
                </button>
                <button
                  onClick={() => setScope("multi")}
                  className={`px-4 py-1.5 text-sm font-medium border-l border-slate-200 ${
                    scope === "multi" ? "bg-blue-600 text-white" : "bg-white text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  Multi-sector (≤5)
                </button>
              </div>
              {scope === "multi" && (
                <p className="text-xs text-slate-500 mt-1">
                  Pick up to {MAX_MULTI_SECTORS} sectors below (or leave blank to use the
                  newest 5). One representative event per sector is cross-checked for the
                  same duration and period.
                </p>
              )}
            </div>
            {/* High stellar variability — fit + subtract a sinusoid before BLS. */}
            <div className="mb-4 p-3 bg-slate-50 rounded border border-slate-200">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <input
                  type="checkbox"
                  checked={params.highVariability}
                  onChange={(e) =>
                    setParams({ ...params, highVariability: e.target.checked })
                  }
                />
                High stellar variability (detrend before BLS)
              </label>
              {params.highVariability && (
                <div className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                  <label>Expected rotation period (days, optional):</label>
                  <input
                    type="number"
                    step="0.001"
                    min="0"
                    value={params.rotationPeriod ?? ""}
                    placeholder="auto (Lomb-Scargle peak)"
                    onChange={(e) => {
                      const v = e.target.value.trim();
                      setParams({
                        ...params,
                        rotationPeriod: v === "" ? null : parseFloat(v),
                      });
                    }}
                    className="border rounded px-2 py-0.5 font-mono w-40 text-xs"
                  />
                </div>
              )}
              <p className="text-[11px] text-slate-500 mt-1">
                Fits a sine + first harmonic and subtracts it before BLS. Helps
                detect shallow dips on spotted rotators and wave-like variables.
              </p>
            </div>
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
                      {availableSectors.map((si) => {
                        const selected =
                          scope === "multi"
                            ? multiSectors.includes(si.sector)
                            : String(si.sector) === sectorInput;
                        return (
                        <button
                          key={si.sector}
                          onClick={() =>
                            scope === "multi"
                              ? toggleMultiSector(si.sector)
                              : setSectorInput(String(si.sector))
                          }
                          title={
                            si.providers.length
                              ? `Providers: ${si.providers.join(", ")}`
                              : ""
                          }
                          className={`px-2 py-0.5 rounded font-mono text-xs ${
                            selected
                              ? "bg-blue-600 text-white"
                              : si.providers.includes("SPOC")
                              ? "bg-slate-100 hover:bg-slate-200"
                              : "bg-amber-50 hover:bg-amber-100 text-amber-900"
                          }`}
                        >
                          S{String(si.sector).padStart(3, "0")}
                        </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-center gap-3 mt-5">
              {scope === "single" ? (
                <>
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
                </>
              ) : (
                <button
                  onClick={runMultisectorTop}
                  disabled={status === "analyzing" || msTopLoading || !ticInput}
                  className="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300"
                >
                  {msTopLoading
                    ? "Running multi-sector…"
                    : multiSectors.length
                    ? `Run multi-sector (${multiSectors.length} selected)`
                    : "Run multi-sector (newest 5)"}
                </button>
              )}
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
            <CyclingLoader messages={VETTING_LOADING_MSGS} />
            <span className="block mt-1 text-xs text-blue-700">
              This can take 10–30 seconds for a 2-min cadence sector.
            </span>
          </div>
        )}

        {result && <ResultsView result={result} />}

        {msData && (
          <div className="mt-2">
            <MultisectorPanel data={msData} />
          </div>
        )}
      </main>

      {result && <FfiCutoutPanel result={result} />}
      {result && <ExoMinerPanel result={result} />}

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
  const isDefault =
    params.threshold === 0.997 &&
    params.minSnr === 4.0 &&
    params.secondarySigma === 3.0;

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
            {(() => {
              // Slider moves in log-depth space so the sensitive shallow end
              // (0.1–0.5%) gets most of the travel. Stored value stays the
              // raw threshold (0.95–0.999) — API unchanged.
              const MIN_DEPTH = 0.001; // 0.1% — strict end
              const MAX_DEPTH = 0.05; //  5%   — loose end
              const lnMin = Math.log(MIN_DEPTH);
              const lnMax = Math.log(MAX_DEPTH);
              const depth = Math.min(Math.max(1 - params.threshold, MIN_DEPTH), MAX_DEPTH);
              // pos 0 = loose (5%), 1 = strict (0.1%)
              const pos = (lnMax - Math.log(depth)) / (lnMax - lnMin);
              const setFromPos = (p: number) => {
                const d = Math.exp(lnMax - p * (lnMax - lnMin));
                setParams({ ...params, threshold: 1 - d });
              };
              return (
                <>
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
                    min={0}
                    max={1}
                    step={0.001}
                    value={pos}
                    onChange={(e) => setFromPos(parseFloat(e.target.value))}
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
                    <span>5% (very loose)</span>
                    <span>~0.3% default</span>
                    <span>0.1% (very strict)</span>
                  </div>
                </>
              );
            })()}
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

          <div>
            <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
              <span>
                Secondary eclipse σ:{" "}
                <span className="font-mono">{params.secondarySigma.toFixed(1)}σ</span>
                <span className="text-slate-400 ml-1">
                  (depth at phase 0.5 must exceed this × local scatter)
                </span>
              </span>
              <button
                onClick={() => setParams({ ...params, secondarySigma: 3.0 })}
                className="text-blue-600 hover:underline"
              >
                reset
              </button>
            </label>
            <input
              type="range"
              min={1.0}
              max={7.0}
              step={0.1}
              value={params.secondarySigma}
              onChange={(e) =>
                setParams({ ...params, secondarySigma: parseFloat(e.target.value) })
              }
              className="w-full"
            />
            <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
              <span>1σ (very loose, more EB flags)</span>
              <span>3σ default</span>
              <span>7σ (very strict)</span>
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
  const [hciData, setHciData] = useState<any>(null);
  const [hciLoading, setHciLoading] = useState(false);
  const [hciError, setHciError] = useState<string | null>(null);
  const [multisectorData, setMultisectorData] = useState<any>(null);
  const [msLoading, setMsLoading] = useState(false);
  const [msError, setMsError] = useState<string | null>(null);

  const runHci = async (massEarth?: number) => {
    if (!result.star.tic_id) return;
    setHciLoading(true); setHciError(null);
    try {
      const dvtTce = result.dvt?.tce;
      const enrichedVerdict = {
        ...result.verdict,
        _depth: result.events?.[0]?.depth ?? null,
        _bls_depth: result.bls?.depth ?? null,
        _t14_d: result.shape?.t14_d ?? null,
        _bls_duration: result.bls?.duration ?? null,
        _shape_class: result.shape?.shape_class ?? null,
        _events: result.events,
        // DVT-fitted parameters let the backend use SPOC's b and a/R★
        _dvt_period_d:   dvtTce?.period_d   ?? null,
        _dvt_duration_h: dvtTce?.duration_h ?? null,
        _dvt_depth_frac: dvtTce?.depth_frac ?? null,
        _dvt_impact_b:   dvtTce?.impact_b   ?? null,
        _dvt_a_over_rs:  dvtTce?.a_over_rs  ?? null,
      };
      const data = await fetchHabitability(result.star.tic_id, {
        stellar_teff: result.star.teff ?? undefined,
        stellar_radius_sun: result.star.radius ?? undefined,
        stellar_mass_sun: result.star.mass ?? undefined,
        orbital_period_d: dvtTce?.period_d ?? result.bls?.period ?? undefined,
        R_companion_Rjup: result.physics?.R_companion_Rjup ?? undefined,
        planet_mass_earth: massEarth ?? undefined,
        n_sectors_with_detections: result.summary.n_events_detected > 0 ? 1 : 0,
        n_sectors_observed: 1,
        vetting_verdict: enrichedVerdict,
      });
      setHciData(data);
    } catch (e: any) {
      setHciError(e.message);
    } finally {
      setHciLoading(false);
    }
  };

  const runMultisector = async () => {
    if (!result.star.tic_id) return;
    setMsLoading(true); setMsError(null);
    try {
      const data = await fetchMultisector(result.star.tic_id);
      setMultisectorData(data);
      // Re-run HCI with updated sector counts
      if (data.n_sectors_observed > 1) {
        const dvtTce = result.dvt?.tce;
        const enrichedVerdict = {
          ...result.verdict,
          _depth: result.events?.[0]?.depth ?? null,
          _bls_depth: result.bls?.depth ?? null,
          _t14_d: result.shape?.t14_d ?? null,
          _bls_duration: result.bls?.duration ?? null,
          _shape_class: result.shape?.shape_class ?? null,
          _events: result.events,
          _dvt_period_d:   dvtTce?.period_d   ?? null,
          _dvt_duration_h: dvtTce?.duration_h ?? null,
          _dvt_depth_frac: dvtTce?.depth_frac ?? null,
          _dvt_impact_b:   dvtTce?.impact_b   ?? null,
          _dvt_a_over_rs:  dvtTce?.a_over_rs  ?? null,
        };
        const updated = await fetchHabitability(result.star.tic_id, {
          stellar_teff: result.star.teff ?? undefined,
          stellar_radius_sun: result.star.radius ?? undefined,
          stellar_mass_sun: result.star.mass ?? undefined,
          orbital_period_d: dvtTce?.period_d ?? result.bls?.period ?? undefined,
          R_companion_Rjup: result.physics?.R_companion_Rjup ?? undefined,
          n_sectors_with_detections: data.n_sectors_with_detections,
          n_sectors_observed: data.n_sectors_observed,
          vetting_verdict: enrichedVerdict,
        });
        setHciData(updated);
      }
    } catch (e: any) {
      setMsError(e.message);
    } finally {
      setMsLoading(false);
    }
  };

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
          {result.star?.tic_id != null && result.star?.sector != null && (
            <a
              href={fitsDownloadUrl(result.star.tic_id, result.star.sector)}
              className="ml-3 inline-block px-2 py-0.5 rounded bg-slate-700 text-white text-xs font-medium hover:bg-slate-800 no-underline"
              download
            >
              ↓ Download FITS
            </a>
          )}
          {result.mast.fallback && (
            <span className="block mt-1 text-xs">
              SPOC 2-min wasn't available for this TIC+sector. Falling back to{" "}
              {result.mast.author} — the centroid (background-blend) test will be
              skipped because FFI products don't include centroid columns.
            </span>
          )}
          <div className="mt-2">
            <DvtStatus dvt={result.dvt} />
          </div>
        </div>
      )}
      {/* Glossary */}
      <GlossaryPanel />

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

      {/* BTJD → BJD conversion guide */}
      <section className="rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-slate-700">
        <div className="flex items-center gap-2 font-semibold text-sky-800">
          <span>🕑</span> Convert BTJD → BJD
        </div>
        <p className="mt-1">
          TESS times (including the transit epoch <code>t0</code> in this
          report) are in <strong>BTJD</strong>. ExoFOP-TESS expects the transit
          epoch in full <strong>BJD</strong>. Convert with:
        </p>
        <p className="mt-2 font-mono text-slate-900 bg-white border border-sky-200 rounded px-3 py-2">
          BJD = BTJD + 2,457,000
        </p>
        {result.bls?.t0 != null && (
          <p className="mt-2 text-xs text-slate-600">
            For this candidate: t0 = {Number(result.bls.t0).toFixed(5)} BTJD
            {"  →  "}
            <span className="font-mono text-slate-900">
              {(Number(result.bls.t0) + 2457000).toFixed(5)} BJD
            </span>
          </p>
        )}
      </section>

      <div className="grid md:grid-cols-2 gap-4">
        <KV title="Stellar parameters" data={result.star} />
        <KV title="Light curve summary" data={result.summary} />
      </div>

      {/* Plots */}
      <PlotsSection plots={result.plots} ticId={result.star.tic_id} sector={result.star.sector} />

      {/* SPOC DVT phase-fold (MAST results only, when DVT is available) */}
      {result.dvt?.available && <DvtPanel dvt={result.dvt} />}

      {/* Tests */}
      <div className="grid md:grid-cols-2 gap-4">
        <KV title="BLS (Box Least Squares)" data={result.bls} hide={["_periodogram"]} />
        <KV title="Lomb-Scargle periodogram" data={result.lomb_scargle} hide={["top_peaks"]} />
        <KV title="Centroid (background-blend test)" data={result.centroid} />
        <KV title="Transit shape (U vs V)" data={result.shape} />
        <KV title="Odd / even depths (EB test)" data={result.odd_even} />
        <KV title="Secondary eclipse search" data={result.secondary} />
        <KV title="Physical interpretation" data={result.physics} />
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

      {/* Manual tiny-dip selector */}
      <ManualDipSelector result={result} />

      {/* Observables, parameters & TLCM — standalone, independent of HCI */}
      <ObservablesSection result={result} />

      {/* Habitability Chance Index */}
      <section className="bg-white rounded-lg shadow p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-bold text-slate-800">
            🌍 Habitability Chance Index
            <span className="ml-2 text-xs font-normal text-slate-500">
              based on Hill et al. (2026) STEHM
            </span>
          </h3>
          <div className="flex gap-2">
            {result.star.tic_id && (
              <button
                onClick={() => runHci()}
                disabled={hciLoading}
                className="text-sm px-3 py-1.5 bg-teal-600 text-white rounded hover:bg-teal-700 disabled:bg-slate-300"
              >
                {hciLoading ? "Computing…" : hciData ? "Refresh" : "Compute HCI"}
              </button>
            )}
            {result.star.tic_id && (
              <button
                onClick={runMultisector}
                disabled={msLoading}
                className="text-sm px-3 py-1.5 bg-violet-600 text-white rounded hover:bg-violet-700 disabled:bg-slate-300"
              >
                {msLoading ? "Fetching sectors…" : "Multi-sector analysis"}
              </button>
            )}
          </div>
        </div>

        {hciError && <p className="text-sm text-red-700 mb-2">HCI error: {hciError}</p>}
        {msError && <p className="text-sm text-red-700 mb-2">Multi-sector error: {msError}</p>}

        {hciLoading && (
          <div className="rounded bg-teal-50 border border-teal-200 p-3 text-teal-900 text-sm mb-2">
            <CyclingLoader messages={HCI_LOADING_MSGS} />
          </div>
        )}

        {!hciData && !hciLoading && (
          <p className="text-sm text-slate-500">
            Click <strong>Compute HCI</strong> to query ExoFOP-TESS for TOI data and
            calculate a habitability score for this target using the STEHM framework.
          </p>
        )}

        {hciData && <HabitabilityPanel data={hciData} />}
        {hciData && <RVPanel ticId={result.star.tic_id} periodD={result.bls?.period ?? null} mstar={hciData?.planet?.stellar_mass_sun ?? null} onUseMass={(m) => runHci(m)} massInHci={hciData?.planet?.mass_earth ?? null} massSource={hciData?.planet?.mass_source ?? null} />}
        {multisectorData && <MultisectorPanel data={multisectorData} />}
      </section>
    </div>
  );
}

function RVPanel({ ticId, periodD, mstar, onUseMass, massInHci, massSource }: { ticId: number | null; periodD: number | null; mstar: number | null; onUseMass?: (m: number) => void; massInHci?: number | null; massSource?: string | null }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [rvText, setRvText] = useState("");
  const [periodIn, setPeriodIn] = useState<string>(periodD ? String(periodD) : "");
  const [massIn, setMassIn] = useState<string>(mstar ? String(mstar) : "");
  const fmt = (v: any, d = 4) =>
    v === null || v === undefined ? "—" : typeof v === "number" ? Number(v.toPrecision(d)) : String(v);

  const tryArchive = async () => {
    if (!ticId) { setShowUpload(true); return; }
    setLoading(true); setErr(null);
    try {
      const d = await fetchRadialVelocity({ tic_id: ticId, stellar_mass_sun: mstar ?? undefined });
      setData(d);
      if (!d.available) setShowUpload(true);
    } catch (e: any) { setErr(e.message); setShowUpload(true); }
    finally { setLoading(false); }
  };

  const submitUpload = async () => {
    const vals = rvText.split(/[\s,]+/).map((s) => parseFloat(s)).filter((x) => !isNaN(x));
    if (vals.length < 2) { setErr("Enter at least 2 RV values (m/s)."); return; }
    const period = parseFloat(periodIn);
    if (isNaN(period)) { setErr("Enter the orbital period (days)."); return; }
    setLoading(true); setErr(null);
    try {
      const d = await fetchRadialVelocity({
        orbital_period_d: period,
        stellar_mass_sun: massIn ? parseFloat(massIn) : undefined,
        rv_values_ms: vals,
      });
      setData(d);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  };

  const comp = data?.companion;
  return (
    <div className="rounded-lg border border-slate-300 bg-white p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="font-bold text-slate-800">Radial velocity → absolute mass</h4>
        <span className="text-xs text-slate-400">Archive K, else upload (TLCM mass function)</span>
      </div>
      <div className="flex gap-2">
        <button onClick={tryArchive} disabled={loading}
          className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:bg-slate-300">
          {loading ? "Querying…" : "Fetch RV from archive"}
        </button>
        <button onClick={() => setShowUpload((s) => !s)}
          className="px-3 py-1.5 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200">
          Paste RV data
        </button>
      </div>
      {err && <p className="text-sm text-rose-600">{err}</p>}
      {data && data.available && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
          <dt className="text-slate-600">Source</dt><dd className="mono">{data.source}</dd>
          <dt className="text-slate-600">K (m/s)</dt><dd className="mono">{fmt(data.K_ms, 4)}</dd>
          <dt className="text-slate-600">Mass function (M⊙)</dt><dd className="mono">{fmt(data.mass_function_msun)}</dd>
          {comp && <><dt className="text-slate-600">Companion mass (M♃ / M⊕)</dt>
            <dd className="mono">{fmt(comp.mp_mjup, 4)} / {fmt(comp.mp_earth, 4)}</dd></>}
        </dl>
      )}
      {comp && onUseMass && (
        <button onClick={() => onUseMass(comp.mp_earth)}
          className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">
          Use {fmt(comp.mp_earth, 3)} M⊕ in HCI (density check)
        </button>
      )}
      {massInHci != null && (
        <p className="text-xs text-slate-500">HCI size score currently using mass {fmt(massInHci, 3)} M⊕{massSource ? ` (${massSource})` : ""}.</p>
      )}
      {data && data.available === false && (
        <p className="text-sm text-amber-700">No catalog RV semi-amplitude found — paste an RV time series below.</p>
      )}
      {showUpload && (
        <div className="space-y-2 pt-2 border-t">
          <textarea value={rvText} onChange={(e) => setRvText(e.target.value)} rows={3}
            placeholder="RV values in m/s, comma/space/newline separated"
            className="w-full text-sm border rounded p-2 mono" />
          <div className="flex gap-2 text-sm">
            <input value={periodIn} onChange={(e) => setPeriodIn(e.target.value)} placeholder="Period (d)"
              className="border rounded p-1.5 w-28" />
            <input value={massIn} onChange={(e) => setMassIn(e.target.value)} placeholder="M★ (M⊙)"
              className="border rounded p-1.5 w-28" />
            <button onClick={submitUpload} disabled={loading}
              className="px-3 py-1.5 bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:bg-slate-300">
              Compute
            </button>
          </div>
          <p className="text-xs text-slate-500">K is estimated as (max − min)/2 from the series.</p>
        </div>
      )}
    </div>
  );
}

function ObservablesSection({ result }: { result: VettingResult }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    compute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function compute() {
    setLoading(true);
    setError(null);
    try {
      // Prefer SPOC DV-fitted parameters when a DVT file was found alongside
      // the light curve.  These carry a fitted impact parameter and a/R★ that
      // replace the b=0 central-transit assumption used by the BLS-only path.
      const dvtTce = result.dvt?.tce;
      const vv: Record<string, any> = {
        _bls_period: result.bls?.period ?? null,
        _period: result.bls?.period ?? null,
        _depth: result.physics?.observed_depth ?? result.events?.[0]?.depth ?? null,
        _bls_depth: result.bls?.depth ?? null,
        _t14_d: result.shape?.t14_d ?? null,
        _bls_duration: result.bls?.duration ?? null,
        _shape_class: result.shape?.shape_class ?? null,
        _R_companion_Rjup: result.physics?.R_companion_Rjup ?? null,
        _events: result.events,
        // DVT-fitted parameters (null when DVT unavailable — backend ignores null)
        _dvt_period_d:   dvtTce?.period_d   ?? null,
        _dvt_duration_h: dvtTce?.duration_h ?? null,
        _dvt_depth_frac: dvtTce?.depth_frac ?? null,
        _dvt_impact_b:   dvtTce?.impact_b   ?? null,
        _dvt_a_over_rs:  dvtTce?.a_over_rs  ?? null,
      };
      const obs = await fetchObservables({
        tic_id: result.star.tic_id ?? undefined,
        stellar_teff: result.star.teff ?? undefined,
        stellar_radius_sun: result.star.radius ?? undefined,
        stellar_mass_sun: result.star.mass ?? undefined,
        // Prefer DVT period (multi-sector fold) over single-sector BLS peak.
        orbital_period_d: dvtTce?.period_d ?? result.bls?.period ?? undefined,
        rp_rjup: result.physics?.R_companion_Rjup ?? undefined,
        transit_depth_frac: dvtTce?.depth_frac ?? result.physics?.observed_depth ?? result.bls?.depth ?? undefined,
        vetting_verdict: vv,
      });
      setData(obs);
    } catch (e: any) {
      setError(e?.message ?? "Observables request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="bg-white rounded-lg shadow p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="font-bold text-slate-800">Observables, parameters &amp; TLCM</h3>
          <p className="text-xs text-slate-400">
            POE forward model, transit geometry, and ExoFOP-TESS TOI parameters — independent of HCI
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!loading && (
            <button onClick={compute} className="text-xs text-slate-500 hover:text-slate-800 underline">
              Re-run
            </button>
          )}
          <button
            onClick={() => setExpanded((x) => !x)}
            className="text-slate-400 hover:text-slate-700 text-sm"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>

      {expanded && (
        <>
          {loading && (
            <div className="rounded bg-slate-50 border border-slate-200 p-3 text-sm">
              <CyclingLoader
                messages={[
                  "Forward-modelling observables…",
                  "Working out the transit geometry…",
                  "Mapping ExoFOP TOI fields…",
                ]}
              />
            </div>
          )}
          {!loading && error && (
            <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-3">
              {error}
            </p>
          )}
          {!loading && !error && data && (
            <ObservablesPanel
              obs={data}
              tlcm={data.tlcm}
              aSource={data.semi_major_axis_source}
              result={result}
            />
          )}
        </>
      )}
    </section>
  );
}

function ObservablesPanel({ obs, tlcm, aSource, result }: { obs: any; tlcm?: any; aSource?: string; result?: any }) {
  if (!obs) return null;
  const hz = obs.habitable_zone || {};
  const fmt = (v: any, d = 4) =>
    v === null || v === undefined ? "—" : typeof v === "number" ? Number(v.toPrecision(d)) : String(v);
  const rows: [string, any][] = [
    ["Luminosity (L⊙)", fmt(obs.luminosity_lsun)],
    ["HZ inner / centre / outer (AU)", `${fmt(hz.inner_au, 3)} / ${fmt(hz.center_au, 3)} / ${fmt(hz.outer_au, 3)}`],
    ["HZ centre (mas)", hz.center_mas !== undefined ? fmt(hz.center_mas, 3) : "— (needs distance)"],
    ["Semi-major axis a (AU)", `${fmt(obs.orbit?.semi_major_axis_au)}${aSource ? `  (${aSource})` : ""}`],
    ["Orbital period P (d)", fmt(obs.orbit?.orbital_period_d)],
    ["Insolation S (S⊕)", fmt(obs.insolation_searth)],
    ["Planet radius (R⊕ / R♃)", `${fmt(obs.planet?.rp_earth, 3)} / ${fmt(obs.planet?.rp_rjup, 3)}`],
    ["Planet mass (M♃)", `${fmt(obs.planet?.mp_mjup, 3)}  (${obs.planet?.mass_source})`],
    ...(obs.planet?.mass_estimates_earth ? [["M–R estimates (M⊕): C&K / power-law",
      `${fmt(obs.planet.mass_estimates_earth.chen_kipping, 3)} / ${fmt(obs.planet.mass_estimates_earth.powerlaw, 3)}`] as [string, any]] : []),
    ["RV semi-amplitude K (m/s)", obs.radial_velocity?.K_ms_textbook !== undefined
      ? `${fmt(obs.radial_velocity?.K_ms, 4)}  (textbook ${fmt(obs.radial_velocity?.K_ms_textbook, 4)}, Δ${fmt(obs.radial_velocity?.K_agreement_pct, 2)}%)`
      : fmt(obs.radial_velocity?.K_ms)],
    ["Astrometric Δθ (μas)", obs.astrometric?.theta_uas !== undefined ? fmt(obs.astrometric.theta_uas, 4) : "— (needs distance)"],
    ["Predicted transit depth (%)", fmt(obs.transit?.depth_pct, 4)],
    ["Max projected separation (″)", fmt(obs.max_projected_separation_arcsec)],
  ];
  const tl = tlcm || {};
  const rv = tl.radial_velocity || {};
  // Show the a/Rs source in the label: SPOC DV model fit is preferred over the
  // b=0 duration-inversion when a DVT file was available.
  const isSpocDv = (tl.a_over_rs_assumption ?? "").includes("SPOC DV");
  const aRsLabel = isSpocDv
    ? "Scaled semi-major axis a/Rs (SPOC DV / dynamical)"
    : "Scaled semi-major axis a/Rs (duration / dynamical)";
  const tlRows: [string, any][] = [
    ["Radius ratio k = Rp/Rs", fmt(tl.radius_ratio_k, 4)],
    [aRsLabel,
      tl.a_over_rs_dynamical != null
        ? `${fmt(tl.a_over_rs, 4)} / ${fmt(tl.a_over_rs_dynamical, 4)}${tl.a_over_rs_agreement_pct != null ? ` (Δ${fmt(tl.a_over_rs_agreement_pct, 1)}%)` : ""}`
        : fmt(tl.a_over_rs, 4)],
    ["Stellar density (g/cm³ · ρ⊙)", `${fmt(tl.stellar_density_gcc, 3)} · ${fmt(tl.stellar_density_rho_sun, 3)}`],
    ["M★ from density (M⊙)", fmt(tl.mstar_from_density_sun, 3)],
    ["Impact parameter b / i (°)", `${fmt(tl.impact_parameter_b, 3)} / ${fmt(tl.inclination_deg, 4)}`],
    ["Absolute mass from RV (M♃)", rv.mp_mjup !== undefined ? fmt(rv.mp_mjup, 4) : "— (needs RV K)"],
  ];
  const showTlcm = tl.radius_ratio_k != null || tl.a_over_rs != null || tl.a_over_rs_dynamical != null;

  // ExoFOP-TESS TOI parameters: pipeline-measured values + derived observables.
  // Transit epoch is converted BTJD → BJD (BJD = BTJD + 2,457,000).
  // Prefer SPOC DV-fitted values (DVT) over BLS estimates when available —
  // they are fitted from a Mandel-Agol model across all processed sectors.
  const dvtTce = result?.dvt?.tce;
  const t0btjd = result?.bls?.t0;
  const depthFrac = dvtTce?.depth_frac ?? result?.physics?.observed_depth ?? result?.bls?.depth;
  const durationH = dvtTce?.duration_h ?? result?.shape?.t14_hours ?? (result?.bls?.duration != null ? result.bls.duration * 24 : null);
  const insol = obs.insolation_searth;
  const teq = insol != null && insol > 0 ? 278.3 * Math.pow(insol, 0.25) : null;
  const efmt = (v: any, unit: string) => {
    if (v === null || v === undefined) return "—";
    if (unit === "BJD") return Number(v).toFixed(5);
    if (unit === "ppm") return Number(v).toFixed(1);
    if (unit === "m/s") return Number(v).toFixed(3);
    return typeof v === "number" ? Number(v.toPrecision(5)) : String(v);
  };
  const exofopRows: { label: string; value: any; unit: string; req: boolean }[] = [
    { label: "Orbital Period", unit: "days", req: true,
      value: dvtTce?.period_d ?? result?.bls?.period ?? obs.orbit?.orbital_period_d },
    { label: "Transit Epoch", unit: "BJD", req: true,
      value: dvtTce?.epoch_btjd != null ? dvtTce.epoch_btjd + 2457000 : (t0btjd != null ? t0btjd + 2457000 : null) },
    { label: "Transit Depth", unit: "ppm", req: true, value: depthFrac != null ? depthFrac * 1e6 : null },
    { label: "Transit Duration", unit: "hrs", req: true, value: durationH },
    { label: "Inclination", unit: "deg", req: false, value: tl.inclination_deg },
    { label: "Impact Parameter b", unit: "", req: false, value: tl.impact_parameter_b },
    { label: "R_planet/R_star", unit: "", req: false, value: tl.radius_ratio_k },
    { label: "a/R_star", unit: "", req: false, value: tl.a_over_rs },
    { label: "Radius", unit: "R_Earth", req: false, value: obs.planet?.rp_earth },
    { label: "Mass", unit: "M_Earth", req: false, value: obs.planet?.mp_earth },
    { label: "Equilibrium Temperature", unit: "K", req: false, value: teq },
    { label: "Insolation Flux", unit: "Flux_Earth", req: false, value: insol },
    { label: "Fitted Stellar Density", unit: "g/cm³", req: false, value: tl.stellar_density_gcc },
    { label: "Semi-major Axis", unit: "AU", req: false, value: obs.orbit?.semi_major_axis_au },
    { label: "Eccentricity", unit: "", req: false, value: obs.orbit?.eccentricity ?? 0 },
    { label: "Argument of Periastron ω", unit: "deg", req: false, value: null },
    { label: "Time of Periastron", unit: "BJD", req: false, value: null },
    { label: "Velocity Semi-amplitude", unit: "m/s", req: false, value: obs.radial_velocity?.K_ms },
  ];

  return (
    <div className="rounded-lg border border-slate-300 bg-white p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="font-bold text-slate-800">Predicted Observables (POE)</h4>
        <span className="text-xs text-slate-400">NASA Exoplanet Archive POE equations</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div className="contents" key={k}>
            <dt className="text-slate-600">{k}</dt>
            <dd className="mono text-slate-900">{v}</dd>
          </div>
        ))}
      </dl>
      {showTlcm && (
        <>
          <div className="flex items-center justify-between pt-2 border-t mt-2">
            <h4 className="font-bold text-slate-800">Transit geometry (TLCM)</h4>
            <span className="text-xs text-slate-400">Csizmadia 2020</span>
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
            {tlRows.map(([k, v]) => (
              <div className="contents" key={k}>
                <dt className="text-slate-600">{k}</dt>
                <dd className="mono text-slate-900">{v}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {obs.orbit?.derivation && obs.orbit.derivation !== "as supplied" && (
        <p className="text-xs text-slate-500">{obs.orbit.derivation}.</p>
      )}

      {/* ExoFOP-TESS TOI parameters for submission */}
      <div className="pt-2 border-t mt-2">
        <div className="flex items-center justify-between">
          <h4 className="font-bold text-slate-800">ExoFOP-TESS TOI parameters</h4>
          <span className="text-xs text-slate-400">*** = required for submission</span>
        </div>
        <p className="text-xs text-slate-500 mt-1">
          Transit epoch in BJD (BTJD + 2,457,000). Non-required fields are
          derived estimates from the observables / TLCM analyses.
        </p>
        <table className="w-full text-sm mt-2">
          <thead className="text-left text-slate-500 border-b">
            <tr><th className="py-1 font-medium">Parameter</th><th className="py-1 font-medium">Value</th><th className="py-1 font-medium">Unit</th></tr>
          </thead>
          <tbody>
            {exofopRows.map((r) => (
              <tr key={r.label} className="border-b border-slate-100">
                <td className="py-1 text-slate-600">
                  {r.label}
                  {r.req && <span className="text-sky-600 font-bold"> ***</span>}
                </td>
                <td className="py-1 mono text-slate-900">{efmt(r.value, r.unit)}</td>
                <td className="py-1 text-slate-400">{r.unit || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {[...(obs.caveats || []), ...((tlcm && tlcm.caveats) || [])].length > 0 && (
        <ul className="text-xs text-slate-500 list-disc pl-4 space-y-0.5">
          {[...(obs.caveats || []), ...((tlcm && tlcm.caveats) || [])].map((c: string, i: number) => <li key={i}>{c}</li>)}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SPOC DVT fetch-status indicator (single + multi-sector)
// ---------------------------------------------------------------------------

function DvtStatus({ dvt }: { dvt?: DvtResult | null }) {
  const ok = !!dvt?.available;
  const tce = dvt?.tce;
  const bits: string[] = [];
  if (tce?.period_d != null) bits.push(`P = ${Number(tce.period_d).toPrecision(6)} d`);
  if (tce?.a_over_rs != null) bits.push(`a/R★ = ${Number(tce.a_over_rs).toPrecision(4)}`);
  if (tce?.impact_b != null) bits.push(`b = ${Number(tce.impact_b).toPrecision(3)}`);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium border ${
        ok
          ? "bg-emerald-50 border-emerald-300 text-emerald-800"
          : "bg-slate-100 border-slate-300 text-slate-500"
      }`}
      title={
        ok
          ? "SPOC Data Validation time series fetched — fitted a/R★ and impact parameter are in use."
          : "No SPOC DV time series available for this target (FFI-only target or a very recent sector). The geometry falls back to the BLS-derived estimate."
      }
    >
      {ok ? "✓" : "—"} SPOC DV time series:{" "}
      <span className="font-semibold">{ok ? "fetched" : "not available"}</span>
      {ok && bits.length > 0 && (
        <span className="font-mono text-emerald-700/80">· {bits.join("  ")}</span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// SPOC DVT phase-fold panel
// ---------------------------------------------------------------------------

function DvtPanel({ dvt }: { dvt: DvtResult }) {
  const [open, setOpen] = useState(true);
  const tce = dvt.tce;
  if (!tce) return null;

  const fmt = (v: any, d = 4) =>
    v === null || v === undefined ? "—" : typeof v === "number" ? Number(v.toPrecision(d)) : String(v);

  const params: [string, string][] = [
    ["Period (d)",         fmt(tce.period_d, 6)],
    ["Duration T₁₄ (h)",  fmt(tce.duration_h, 5)],
    ["Depth (ppm)",        tce.depth_ppm != null ? tce.depth_ppm.toFixed(0) : "—"],
    ["a/R★ (ARAT)",       fmt(tce.a_over_rs, 4)],
    ["Impact param b",    fmt(tce.impact_b, 3)],
    ["Inclination (°)",   fmt(tce.inclination_deg, 4)],
    ["Rp/Rs",             fmt(tce.rprs, 4)],
    ["SNR",               fmt(tce.snr, 3)],
    ["N transits",        tce.n_transits != null ? String(Math.round(tce.n_transits)) : "—"],
  ].filter(([, v]) => v !== "—") as [string, string][];

  return (
    <section className="bg-white rounded-lg shadow p-5">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="font-bold text-slate-800">
            SPOC DV phase-fold
            {dvt.n_tces > 1 && (
              <span className="ml-2 text-xs font-normal text-slate-500">
                TCE 1 of {dvt.n_tces}
              </span>
            )}
          </h3>
          <p className="text-xs text-slate-400">
            SPOC Data Validation fit — period, a/R★ (ARAT), and impact parameter b
            replace the b=0 BLS assumption in the TLCM geometry and semi-major axis.
          </p>
        </div>
        <button
          onClick={() => setOpen((x) => !x)}
          className="text-slate-400 hover:text-slate-700 text-sm ml-4"
          aria-label={open ? "Collapse" : "Expand"}
        >
          {open ? "▾" : "▸"}
        </button>
      </div>

      {open && (
        <div className="space-y-3">
          {/* Fitted parameter chips */}
          <div className="flex flex-wrap gap-2">
            {params.map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1 rounded bg-indigo-50 border border-indigo-200 px-2 py-0.5 text-xs"
              >
                <span className="text-indigo-500 font-medium">{k}</span>
                <span className="font-mono text-slate-800">{v}</span>
              </span>
            ))}
            {tce.a_over_rs != null && (
              <span className="inline-flex items-center gap-1 rounded bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-xs text-emerald-700 font-medium">
                ✓ SPOC DV a/R★ used in TLCM
              </span>
            )}
            {tce.impact_b != null && (
              <span className="inline-flex items-center gap-1 rounded bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-xs text-emerald-700 font-medium">
                ✓ SPOC DV b used in TLCM
              </span>
            )}
          </div>

          {/* Phase-fold plot with SPOC transit model */}
          {tce.phase_fold_plot && (
            <div>
              <div className="flex items-center justify-between gap-2 mb-1">
                <p className="text-xs text-slate-500">
                  Phase-folded light curve (LC_INIT) with SPOC Mandel-Agol model overlay (MODEL_INIT)
                </p>
                <ShareToImgbbButton
                  base64={tce.phase_fold_plot}
                  title="SPOC_DV_phase_fold"
                  label="SPOC DV phase-fold"
                />
              </div>
              <img
                src={`data:image/png;base64,${tce.phase_fold_plot}`}
                alt="SPOC DV phase-fold with transit model"
                className="w-full rounded border border-slate-200"
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function HabitabilityPanel({ data }: { data: any }) {
  const [expanded, setExpanded] = useState(false);
  const [imgOpen, setImgOpen] = useState(false);
  const hci = data.hci;
  if (!hci) return null;

  const score: number = hci.hci;
  const barColor =
    score >= 70 ? "#10b981" : score >= 45 ? "#f59e0b" : score >= 20 ? "#ef4444" : "#94a3b8";

  return (
    <div className="space-y-3">
      {/* Score summary */}
      <div className={`rounded-lg border-2 p-4 ${hci.tier_color}`}>
        <div className="flex items-center justify-between">
          <div>
            <span className="text-3xl font-bold">{score}</span>
            <span className="text-lg font-semibold ml-1">/ 100</span>
            {hci.hci_low != null && hci.hci_high != null && (hci.hci_high - hci.hci_low) > 0.1 && (
              <span className="ml-2 text-sm font-medium opacity-80">({hci.hci_low}–{hci.hci_high})</span>
            )}
            <span className="ml-3 text-sm font-semibold">{hci.tier}</span>
          </div>
          <div className="text-right text-xs opacity-70">
            <div>Habitability Chance Index</div>
            <div>Hill et al. (2026) STEHM</div>
          </div>
        </div>
        {/* Bar */}
        <div className="mt-3 h-3 bg-white/40 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${score}%`, backgroundColor: barColor }}
          />
        </div>
        {/* Planet/TOI info */}
        {(data.planet?.toi_number || data.planet?.radius_earth) && (
          <div className="mt-2 text-xs opacity-80 flex flex-wrap gap-3">
            {data.planet.toi_number && <span>TOI {data.planet.toi_number}</span>}
            {data.planet.disposition && <span>Disposition: {data.planet.disposition}</span>}
            {data.planet.radius_earth && <span>R = {data.planet.radius_earth.toFixed(2)} R⊕ <span className="text-[10px] opacity-60">({data.planet.radius_source || "unknown source"})</span></span>}
            {data.planet.semi_major_axis_au && <span>a = {data.planet.semi_major_axis_au.toFixed(3)} AU</span>}
            {data.planet.orbital_period_d && <span>P = {data.planet.orbital_period_d.toFixed(2)} d</span>}
            {data.exofop_source && (
              <span className="italic">data: {data.exofop_source}</span>
            )}
          </div>
        )}
      </div>

      {/* Expandable breakdown */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left text-sm text-slate-600 hover:text-slate-900 flex items-center gap-1"
      >
        <span>{expanded ? "▲" : "▼"}</span>
        {expanded ? "Hide" : "Show"} score breakdown
      </button>

      {expanded && (
        <div className="space-y-2">
          {(hci.sub_scores || []).map((s: any) => (
            <div key={s.name} className="bg-slate-50 rounded p-3">
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="font-medium">{s.name}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-500">
                    weight {(s.weight * 100).toFixed(0)}%
                  </span>
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded ${
                      s.score >= 0.7
                        ? "bg-emerald-100 text-emerald-800"
                        : s.score >= 0.4
                        ? "bg-amber-100 text-amber-800"
                        : "bg-rose-100 text-rose-800"
                    }`}
                  >
                    {s.label}
                  </span>
                  <span className="font-mono text-xs w-10 text-right">
                    {(s.score * 100).toFixed(0)}/100
                  </span>
                </div>
              </div>
              {/* Mini progress bar */}
              <div className="h-1.5 bg-slate-200 rounded-full overflow-hidden mb-1">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${s.score * 100}%`,
                    backgroundColor:
                      s.score >= 0.7 ? "#10b981" : s.score >= 0.4 ? "#f59e0b" : "#ef4444",
                  }}
                />
              </div>
              <p className="text-xs text-slate-600">{s.explanation}</p>
            </div>
          ))}

          {/* All TOIs from ExoFOP */}
          {data.all_tois && data.all_tois.length > 1 && (
            <div className="mt-2">
              <p className="text-xs font-semibold text-slate-700 mb-1">
                All TOIs for this star ({data.all_tois.length}):
              </p>
              <table className="w-full text-xs">
                <thead className="border-b text-slate-500">
                  <tr>
                    <th className="py-1 text-left">TOI</th>
                    <th>P (d)</th>
                    <th>R (R⊕)</th>
                    <th>a (AU)</th>
                    <th>Disposition</th>
                  </tr>
                </thead>
                <tbody>
                  {data.all_tois.map((t: any) => (
                    <tr key={t.toi_number} className="border-b">
                      <td className="py-0.5 font-mono">{t.toi_number}</td>
                      <td className="text-center">{t.period_d?.toFixed(3) ?? "—"}</td>
                      <td className="text-center">{t.radius_earth?.toFixed(2) ?? "—"}</td>
                      <td className="text-center">{t.semi_major_axis_au?.toFixed(3) ?? "—"}</td>
                      <td className="text-center">
                        <span
                          className={`px-1 rounded text-xs ${
                            ["CP", "KP"].includes(t.disposition ?? "")
                              ? "bg-emerald-100 text-emerald-800"
                              : ["PC", "APC"].includes(t.disposition ?? "")
                              ? "bg-blue-100 text-blue-800"
                              : t.disposition === "FP"
                              ? "bg-rose-100 text-rose-800"
                              : "bg-slate-100 text-slate-600"
                          }`}
                        >
                          {t.disposition || "—"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Caveats */}
          {hci.caveats?.length > 0 && (
            <div className="text-xs text-slate-500 bg-slate-50 rounded p-3 space-y-1 border-l-2 border-slate-300">
              <p className="font-semibold text-slate-600">⚠ Caveats</p>
              {hci.caveats.map((c: string, i: number) => (
                <p key={i}>• {c}</p>
              ))}
              <p className="mt-1 italic">Ref: {hci.paper_ref}</p>
            </div>
          )}
        </div>
      )}

      {/* Python-generated HCI summary image — metrics, weightings,
          observables & TLCM values in one shareable figure. */}
      {data.hci_image && (
        <div className="border-t pt-3">
          <div className="flex items-center justify-between gap-2">
            <button
              onClick={() => setImgOpen(!imgOpen)}
              className="text-left text-sm text-slate-600 hover:text-slate-900 flex items-center gap-1"
            >
              <span>{imgOpen ? "▲" : "▼"}</span>
              {imgOpen ? "Hide" : "Show"} HCI summary image
            </button>
            {imgOpen && (
              <ShareToImgbbButton
                base64={data.hci_image}
                title={`HCI_summary_TIC${data.planet?.tic_id || data.planet?.toi_number || ""}`}
                label="HCI summary"
              />
            )}
          </div>
          {imgOpen && (
            <div className="mt-2">
              <img
                src={`data:image/png;base64,${data.hci_image}`}
                alt="HCI summary (metrics, weightings, observables, TLCM)"
                className="w-full rounded border"
              />
              <a
                href={`data:image/png;base64,${data.hci_image}`}
                download="hci_summary.png"
                className="inline-block mt-1 text-xs text-blue-600 hover:underline"
              >
                Download image (PNG)
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function SharePlot({
  b64,
  label,
  title,
}: {
  b64: string | null | undefined;
  label: string;
  title: string;
}) {
  if (!b64) return null;
  return (
    <figure className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-xs text-slate-500">{label}</figcaption>
        <ShareToImgbbButton base64={b64} title={title} label={label} />
      </div>
      <img
        src={`data:image/png;base64,${b64}`}
        alt={label}
        className="w-full rounded border"
        loading="lazy"
      />
    </figure>
  );
}

const MS_EXOMINER_VIEWS: Array<[string, string]> = [
  ["global_view", "Global view"],
  ["local_view", "Local view"],
  ["secondary_view", "Secondary view"],
  ["odd_even_view", "Odd vs even"],
  ["centroid_global_view", "Centroid global"],
  ["centroid_local_view", "Centroid local"],
  ["diagnostic_sigmas", "Diagnostic σ"],
];

function MultisectorPanel({ data }: { data: any }) {
  const [expanded, setExpanded] = useState(true);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfErr, setPdfErr] = useState<string | null>(null);
  if (!data) return null;

  const downloadPdf = async () => {
    if (!data.tic_id) return;
    setPdfBusy(true);
    setPdfErr(null);
    try {
      const sectors: number[] = (data.sector_verdicts || [])
        .map((s: any) => s.sector)
        .filter((s: any) => s != null);
      const blob = await multisectorReport(data.tic_id, undefined, sectors);
      triggerDownload(blob, `vetting_TIC${data.tic_id}_multisector.pdf`);
    } catch (e: any) {
      setPdfErr(e?.message || String(e));
    } finally {
      setPdfBusy(false);
    }
  };

  return (
    <div className="mt-4 border-t pt-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-left font-semibold text-slate-700 flex items-center gap-1 text-sm"
        >
          <span>{expanded ? "▲" : "▼"}</span>
          🔭 Multi-sector analysis — {data.summary}
        </button>
        <button
          onClick={downloadPdf}
          disabled={pdfBusy}
          className="shrink-0 text-xs px-3 py-1.5 bg-slate-700 text-white rounded hover:bg-slate-800 disabled:bg-slate-300 transition"
        >
          {pdfBusy ? "Building…" : "Download multi-sector PDF"}
        </button>
      </div>
      {pdfErr && <p className="text-xs text-red-600">PDF error: {pdfErr}</p>}

      {/* SPOC DV time series fetch status for this multi-sector run */}
      <div>
        <DvtStatus dvt={data.dvt} />
      </div>

      {expanded && (
        <div className="space-y-3">
          {/* Timeline plot */}
          {data.timeline_plot && (
            <SharePlot
              b64={data.timeline_plot}
              label="Detection timeline across all fetched sectors"
              title={`multisector_timeline_TIC${data.tic_id || ""}`}
            />
          )}

          {/* Detected objects (up to 2), each cross-confirmed by duration + period */}
          {data.objects && data.objects.length > 0 && (
            <div className="space-y-2">
              <p className="text-sm font-semibold text-slate-700">
                {data.n_objects_detected} object
                {data.n_objects_detected === 1 ? "" : "s"} identified
                <span className="text-xs font-normal text-slate-500">
                  {" "}(≤{data.events_per_sector} events/sector, ≤{data.max_objects} objects,
                  duration tolerance ±{data.duration_tol_h} h)
                </span>
              </p>
              {data.objects.map((o: any) => {
                const ok = o.confirmed_multisector;
                const single = o.sectors.length < 2;
                return (
                  <div
                    key={o.object_id}
                    className={`rounded border p-3 text-sm ${
                      ok
                        ? "bg-emerald-50 border-emerald-300 text-emerald-900"
                        : single
                        ? "bg-slate-50 border-slate-300 text-slate-700"
                        : "bg-amber-50 border-amber-300 text-amber-900"
                    }`}
                  >
                    <p className="font-medium">
                      {ok ? "✓ " : single ? "" : "⚠ "}
                      Object {o.object_id} — {o.duration_h_median} h ·{" "}
                      {o.depth_pct_median}% deep · {o.sectors.length} sector
                      {o.sectors.length === 1 ? "" : "s"}
                      {o.period_d_median ? ` · P ≈ ${o.period_d_median} d` : ""}
                    </p>
                    <p className="text-xs mt-1">{o.note}</p>
                    <table className="w-full text-xs mt-2">
                      <thead className="text-left opacity-70">
                        <tr>
                          <th className="py-0.5">Sector</th>
                          <th>t_center</th>
                          <th>Duration (h)</th>
                          <th>Depth (%)</th>
                          <th>Period (d)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {o.members.map((e: any, i: number) => (
                          <tr key={`${e.sector}-${i}`}>
                            <td className="py-0.5 font-mono">S{String(e.sector).padStart(3, "0")}</td>
                            <td className="font-mono">{e.t_center}</td>
                            <td className="font-mono">{e.duration_h}</td>
                            <td className="font-mono">{e.depth_pct}</td>
                            <td className="font-mono">{e.bls_period_d?.toFixed?.(4) ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>

                    {/* HCI for this object */}
                    {o.hci_bundle && (
                      <div className="mt-3 rounded bg-white/70 border border-slate-200 p-2">
                        <p className="text-xs font-semibold text-slate-700">
                          Habitability Chance Index
                          {o.hci_bundle.hci && o.hci_bundle.hci.hci != null
                            ? ` — ${Math.round(o.hci_bundle.hci.hci)}/100 (${o.hci_bundle.hci.tier})`
                            : ""}
                          {o.representative_sector
                            ? ` · from S${String(o.representative_sector).padStart(3, "0")}`
                            : ""}
                        </p>
                        {o.hci_bundle.hci_image && (
                          <div className="mt-2">
                            <SharePlot
                              b64={o.hci_bundle.hci_image}
                              label="HCI summary"
                              title={`HCI_obj${o.object_id}_TIC${data.tic_id || ""}`}
                            />
                          </div>
                        )}
                      </div>
                    )}

                    {/* ExoMiner views for this object */}
                    {o.exominer?.plots && (
                      <div className="mt-3 rounded bg-white/70 border border-slate-200 p-2 space-y-3">
                        <p className="text-xs font-semibold text-slate-700">
                          ExoMiner feature views
                          {o.representative_sector
                            ? ` · from S${String(o.representative_sector).padStart(3, "0")}`
                            : ""}
                        </p>
                        {MS_EXOMINER_VIEWS.filter(([k]) => o.exominer.plots[k]).map(
                          ([k, label]) => (
                            <SharePlot
                              key={k}
                              b64={o.exominer.plots[k]}
                              label={label}
                              title={`exominer_${k}_obj${o.object_id}_TIC${data.tic_id || ""}`}
                            />
                          )
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Per-sector table */}
          {data.sector_verdicts && (
            <table className="w-full text-xs">
              <thead className="border-b text-slate-500 text-left">
                <tr>
                  <th className="py-1">Sector</th>
                  <th>Events</th>
                  <th>Verdict</th>
                  <th>BLS period (d)</th>
                  <th>SDE</th>
                  <th>FITS</th>
                </tr>
              </thead>
              <tbody>
                {data.sector_verdicts.map((v: any) => (
                  <tr key={v.sector} className="border-b">
                    <td className="py-0.5 font-mono">S{String(v.sector).padStart(3, "0")}</td>
                    <td className="text-center">{v.n_events}</td>
                    <td>
                      <span
                        className={`px-1 rounded ${
                          v.category === "planet_candidate"
                            ? "bg-emerald-100 text-emerald-800"
                            : v.category === "eclipsing_binary_candidate"
                            ? "bg-amber-100 text-amber-800"
                            : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {v.verdict ?? v.category ?? "—"}
                      </span>
                    </td>
                    <td className="text-center font-mono">{v.bls_period_d?.toFixed(4) ?? "—"}</td>
                    <td className="text-center">{v.bls_sde?.toFixed(1) ?? "—"}</td>
                    <td className="text-center">
                      {data.tic_id != null ? (
                        <a
                          href={fitsDownloadUrl(data.tic_id, v.sector)}
                          className="text-blue-600 hover:underline"
                          download
                          title={`Download TIC ${data.tic_id} S${String(v.sector).padStart(3, "0")} FITS`}
                        >
                          ↓
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* Period consensus */}
          {data.period_consensus && (
            <p className="text-xs text-slate-600">
              <strong>Period consensus:</strong>{" "}
              {data.period_consensus.value_d?.toFixed(5)} d{" "}
              {data.period_consensus.std_d
                ? `± ${data.period_consensus.std_d.toFixed(5)} d`
                : ""}{" "}
              <span className="text-slate-400">({data.period_consensus.source})</span>
            </p>
          )}

          {/* Fetch errors */}
          {data.errors?.length > 0 && (
            <div className="text-xs text-slate-400 bg-slate-50 p-2 rounded">
              {data.errors.length} sector(s) could not be fetched:{" "}
              {data.errors.map((e: any) => `S${e.sector}`).join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function PlotsSection({
  plots,
  ticId,
  sector,
}: {
  plots: Record<string, string>;
  ticId?: number | null;
  sector?: number | null;
}) {
  const order = ["lightcurve", "event_zoom", "centroid", "bls", "lomb_scargle"];
  const labels: Record<string, string> = {
    lightcurve: "Full detrended light curve",
    event_zoom: "Event zoom",
    centroid: "Centroid behaviour",
    bls: "BLS periodogram",
    lomb_scargle: "Lomb-Scargle top peaks",
  };

  const [albumResult, setAlbumResult] = useState<any>(null);
  const [albumLoading, setAlbumLoading] = useState(false);
  const [albumError, setAlbumError] = useState<string | null>(null);
  const [forumText, setForumText] = useState<string | null>(null);

  const uploadAll = async () => {
    setAlbumLoading(true);
    setAlbumError(null);
    try {
      const { uploadAllPlots, forumPost } = await import("./imgbb");
      const title = ticId
        ? `TIC_${ticId}${sector ? `_S${sector}` : ""}`
        : "Vetstar";
      const result = await uploadAllPlots(plots, labels, title);
      setAlbumResult(result);
      setForumText(forumPost(result.images, ticId, sector));
    } catch (e: any) {
      setAlbumError(e.message || String(e));
    } finally {
      setAlbumLoading(false);
    }
  };

  return (
    <section className="bg-white rounded-lg shadow p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-bold">Diagnostic plots</h3>
        <button
          onClick={uploadAll}
          disabled={albumLoading}
          className="text-xs px-3 py-1.5 bg-purple-600 text-white rounded hover:bg-purple-700 disabled:bg-slate-300 flex items-center gap-1"
        >
          {albumLoading ? (
            "Uploading…"
          ) : (
            <>
              <ShareIcon />
              Upload all to ImgBB
            </>
          )}
        </button>
      </div>

      {albumError && (
        <p className="text-xs text-red-700 bg-red-50 rounded p-2">
          ImgBB error: {albumError}
        </p>
      )}

      {albumResult && (
        <div className="bg-purple-50 border border-purple-200 rounded p-3 space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-purple-900">
              Plots uploaded to ImgBB
            </span>
            <span className="text-xs text-purple-600">
              {albumResult.images.length} image{albumResult.images.length !== 1 ? "s" : ""}
            </span>
          </div>
          <div className="grid gap-1">
            {albumResult.images.map((img: any) => (
              <CopyField
                key={img.name}
                label={img.label}
                value={img.link}
              />
            ))}
          </div>
          {/* Forum BBCode ready to paste */}
          {forumText && (
            <details className="mt-2">
              <summary className="text-xs text-purple-700 cursor-pointer hover:underline">
                Copy BBCode for Planet Hunters / forum post
              </summary>
              <div className="mt-1 relative">
                <textarea
                  readOnly
                  value={forumText}
                  className="w-full h-32 text-xs font-mono bg-white border rounded p-2 text-slate-700"
                />
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(forumText);
                  }}
                  className="absolute top-1 right-1 text-[10px] px-2 py-0.5 bg-purple-600 text-white rounded hover:bg-purple-700"
                >
                  Copy
                </button>
              </div>
            </details>
          )}
        </div>
      )}

      {order.map((k) =>
        plots[k] ? (
          <figure key={k}>
            <div className="flex items-center justify-between mb-1">
              <figcaption className="text-sm text-slate-600">
                {labels[k]}
              </figcaption>
              <ShareToImgbbButton
                base64={plots[k]}
                title={
                  ticId
                    ? `TIC ${ticId}${sector ? ` S${sector}` : ""} — ${labels[k]}`
                    : labels[k]
                }
                label={labels[k]}
              />
            </div>
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


function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-slate-600 w-36 truncate" title={label}>
        {label}
      </span>
      <input
        readOnly
        value={value}
        className="flex-1 bg-white border rounded px-2 py-0.5 font-mono text-[11px] text-slate-700"
        onClick={(e) => (e.target as HTMLInputElement).select()}
      />
      <button
        onClick={() => {
          navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }}
        className={`px-2 py-0.5 rounded text-[10px] transition ${
          copied
            ? "bg-emerald-100 text-emerald-700"
            : "bg-slate-100 hover:bg-purple-100 text-slate-600 hover:text-purple-700"
        }`}
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
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
              <dt className="text-slate-600 font-mono text-xs">
                <Tip term={k}>{k}</Tip>
              </dt>
              <dd className="font-mono">{formatVal(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Glossary: inline tooltips + collapsible reference panel
// ---------------------------------------------------------------------------

import { GLOSSARY, lookupTerm } from "./glossary";

/**
 * Tooltip wrapper — shows a dotted underline; on hover/tap reveals the
 * glossary definition. Works on both desktop (hover) and mobile (tap).
 */
function Tip({ term, children }: { term: string; children?: React.ReactNode }) {
  const def = lookupTerm(term);
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const tooltipRef = useRef<HTMLSpanElement | null>(null);
  const [visible, setVisible] = useState(false);
  const [offsetX, setOffsetX] = useState(0);

  // Recompute horizontal offset whenever the tooltip becomes visible so that
  // it stays within the viewport even if the trigger sits near a screen edge.
  useEffect(() => {
    if (!visible) return;
    const trigger = triggerRef.current;
    const tooltip = tooltipRef.current;
    if (!trigger || !tooltip) return;

    const margin = 8;
    const tRect = trigger.getBoundingClientRect();
    const tipRect = tooltip.getBoundingClientRect();
    const viewportW = window.innerWidth;

    // Tooltip is centered on the trigger by default. Its left edge sits at
    // (trigger center - tipWidth/2). Compute the shift needed to keep both
    // edges inside [margin, viewportW - margin].
    const centerX = tRect.left + tRect.width / 2;
    const desiredLeft = centerX - tipRect.width / 2;
    let shift = 0;
    if (desiredLeft < margin) {
      shift = margin - desiredLeft;
    } else if (desiredLeft + tipRect.width > viewportW - margin) {
      shift = viewportW - margin - tipRect.width - desiredLeft;
    }
    setOffsetX(shift);
  }, [visible]);

  if (!def) return <>{children || term}</>;

  const show = () => setVisible(true);
  const hide = () => setVisible(false);

  return (
    <span
      className="relative inline"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      ref={triggerRef}
    >
      <span className="border-b border-dotted border-slate-400 cursor-help">
        {children || term}
      </span>
      <span
        ref={tooltipRef}
        style={{ transform: `translateX(calc(-50% + ${offsetX}px))` }}
        className={`
          pointer-events-none absolute z-50 bottom-full left-1/2 mb-2
          w-72 max-w-[90vw] p-2.5 rounded-lg shadow-lg
          bg-slate-900 text-white text-xs leading-relaxed
          transition-opacity duration-150
          ${visible ? "opacity-100" : "opacity-0"}
        `}
        role="tooltip"
      >
        <strong className="text-emerald-300">{term}</strong>
        <br />
        {def}
      </span>
    </span>
  );
}


function GlossaryPanel() {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");

  const entries = Object.entries(GLOSSARY).filter(
    ([k, v]) =>
      !filter ||
      k.toLowerCase().includes(filter.toLowerCase()) ||
      v.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <section className="bg-white rounded-lg shadow overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-3 hover:bg-slate-50 transition text-sm"
      >
        <span className="font-semibold text-slate-700">
          📖 Glossary of technical terms
          <span className="ml-2 text-xs font-normal text-slate-400">
            ({Object.keys(GLOSSARY).length} terms — hover any{" "}
            <span className="border-b border-dotted border-slate-400">
              dotted-underlined
            </span>{" "}
            term in the results for its definition)
          </span>
        </span>
        <span className="text-slate-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-5 pb-4 border-t border-slate-100 space-y-3">
          <input
            type="text"
            placeholder="Search glossary…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-full border rounded px-3 py-1.5 text-sm mt-3"
          />
          <dl className="space-y-2 max-h-96 overflow-y-auto">
            {entries.length === 0 && (
              <p className="text-sm text-slate-400 italic">No matching terms.</p>
            )}
            {entries.map(([term, def]) => (
              <div key={term} className="text-sm">
                <dt className="font-semibold text-slate-800 font-mono text-xs">
                  {term}
                </dt>
                <dd className="text-slate-600 ml-2">{def}</dd>
              </div>
            ))}
          </dl>
        </div>
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
