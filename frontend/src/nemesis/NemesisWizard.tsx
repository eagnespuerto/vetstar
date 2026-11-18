/**
 * Guided NEMESIS run for new users: pick a star, choose options, watch it
 * run, then read a plain-language verdict with LEO-Vetter's tests explained.
 * HCI v2 is offered as an optional extra once a signal is found.
 */
import { useEffect, useRef, useState } from "react";
import { mastSectors, type SectorInfo } from "../api";
import {
  nemesisFileBytes, nemesisFileUrl, nemesisFiles, nemesisHci, nemesisReportPdf, nemesisJob, nemesisResult, nemesisSectors, nemesisStatus,
  submitNemesis, type LeoTest, type NemesisJobRequest, type NemesisJobStatus,
  type NemesisResult, type NemesisStatus,
} from "./api";
import { MiniChart } from "./MiniChart";
import FfiCutoutPanel from "../FfiCutoutPanel";
import ExofopBulkPanel, { type ExofopImage } from "../ExofopBulkPanel";
import { ObservablesSection } from "../transit/ObservablesPanel";
import type { VettingResult } from "../types";

/** Plain-language notes shown under each plot. */
const HOW_TO_READ: Record<string, { title: string; alt: string; points: string[] }> = {
  light_curve: {
    title: "1. The light curve",
    alt: "Detrended TESS light curve with predicted transit times",
    points: [
      "This is the star's brightness over one TESS sector (about 27 days). Up is brighter, down is dimmer.",
      "Grey dots are single measurements; black dots are one-hour averages. Flux is in ppt (parts per thousand): −1 ppt means the star is 0.1 % dimmer.",
      "Red lines mark where NEMESIS expects each transit. A real planet makes a small dip at every red line, not just one.",
      "Gaps are normal: TESS pauses to send data to Earth.",
    ],
  },
  periodogram: {
    title: "2. The period search",
    alt: "Transit search power spectrum against trial period",
    points: [
      "NEMESIS tried thousands of possible orbital periods. Each point says how well a repeating dip fits at that period (SDE, signal detection efficiency).",
      "The solid red line is the best period. A good signal is one tall, narrow peak well above the rest.",
      "The dashed lines are half and double the best period. Smaller peaks there are normal echoes (aliases) of the same signal.",
      "If many peaks are about the same height, the signal is probably noise.",
    ],
  },
  fold: {
    title: "3. All transits stacked",
    alt: "Phase-folded transit with model and residuals",
    points: [
      "Every transit is lined up on top of the others using the best period, so the dip stands out from the noise.",
      "Grey dots are single measurements, black dots with bars are averages (the bar shows the uncertainty), and the red line is the transit model.",
      "How deep the dip goes tells you the size of the object; how wide it is tells you how long a transit lasts.",
      "The lower panel shows data minus model. It should look like random scatter around zero; a leftover pattern means the model misses something.",
    ],
  },
  odd_even: {
    title: "4. Odd and even transits",
    alt: "Odd and even transits plotted side by side",
    points: [
      "Transits 1, 3, 5… are on the left and 2, 4, 6… on the right.",
      "For a planet, both sides look the same. If one dip is clearly deeper, it is likely two stars eclipsing each other, with the true period twice as long.",
      "The dashed red line marks each side's depth, so you can compare them directly.",
    ],
  },
};

function PaperFigure({ name, b64, children }: { name: keyof typeof HOW_TO_READ; b64?: string | null; children?: React.ReactNode }) {
  const info = HOW_TO_READ[name];
  return (
    <figure className="card p-5 grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-5 items-start">
      <div className="rounded-md bg-white p-2 border border-line">
        {b64 ? <img src={`data:image/png;base64,${b64}`} alt={info.alt} className="w-full h-auto" /> : children}
      </div>
      <figcaption>
        <h4 className="text-base font-bold mb-2">{info.title}</h4>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">How to read this plot</p>
        <ul className="list-disc pl-5 space-y-1.5 text-sm">
          {info.points.map((pt) => <li key={pt}>{pt}</li>)}
        </ul>
      </figcaption>
    </figure>
  );
}

function saveBytes(bytes: Uint8Array, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([bytes as Uint8Array<ArrayBuffer>], { type }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

/** PDF report + ExoFOP-TESS bulk-upload ZIP for a finished NEMESIS run. */
function ExportCard({ jobId, result, files, hciImage }: {
  jobId: string; result: NemesisResult; files: { name: string; bytes: number }[]; hciImage: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { tic, sector } = result.meta;
  const method = result.primary ?? "TLS";
  const f = result.figures ?? {};
  // ExoFOP file-type codes: L = light curve, O = other.
  const images: ExofopImage[] = [
    f.light_curve && { key: "nemesis-lc", label: "NEMESIS detrended light curve", b64: f.light_curve, code: "L" },
    f.fold && { key: `nemesis-${method.toLowerCase()}-fold`, label: `NEMESIS ${method} phase-folded transit with residuals`, b64: f.fold, code: "L" },
    f.odd_even && { key: "nemesis-odd-even", label: "NEMESIS odd and even transits", b64: f.odd_even, code: "L" },
    f.periodogram && { key: `nemesis-${method.toLowerCase()}-periodogram`, label: `NEMESIS ${method} periodogram`, b64: f.periodogram, code: "O" },
    hciImage && { key: "hci-v2", label: "Habitability Chance Index v2 summary", b64: hciImage, code: "O" },
    { key: "nemesis-vetting-report", label: "NEMESIS vetting report (verdict, LEO-Vetter tests, figures)",
      b64: "", ext: "pdf", code: "O", fetchBytes: () => nemesisReportPdf(jobId, hciImage) },
    ...files.filter((x) => /multi_page\.pdf$/.test(x.name) && x.name.includes(`_${method}_`)).map((x) => ({
      key: `nemesis-${method.toLowerCase()}-report`, label: `NEMESIS ${method} multi-page vetting report`,
      b64: "", ext: "pdf", code: "O", fetchBytes: () => nemesisFileBytes(jobId, x.name),
    })),
  ].filter(Boolean) as ExofopImage[];

  return (
    <div className="card p-5 space-y-3">
      <h3 className="text-lg font-bold">Save or share these results</h3>
      <div className="flex flex-wrap gap-3 items-center">
        <button className="btn-primary" disabled={busy} onClick={async () => {
          setBusy(true); setErr(null);
          try {
            saveBytes(await nemesisReportPdf(jobId, hciImage),
              `vetstar_nemesis_TIC${tic}_S${String(sector).padStart(3, "0")}.pdf`, "application/pdf");
          } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
        }}>{busy ? "Building PDF…" : "Download PDF report"}</button>
        <span className="text-xs text-muted">Verdict, numbers, LEO-Vetter tests, the four plots, FFI cutout
          {hciImage ? " and your HCI score" : ""}.</span>
      </div>
      {err && <p role="alert" className="text-sm text-coral-700">{err}</p>}
      <ExofopBulkPanel ticId={tic} sector={sector} images={images}
        caption={<p className="text-muted mt-2">
          One <code>xxYYYYMMDD-nnn.zip</code> with a matching <code>.txt</code> descriptor, following the{" "}
          <a href="https://exofop.ipac.caltech.edu/tess/ExoFOP-TESS_Bulk_File_Upload_Instructions.pdf"
             target="_blank" rel="noopener noreferrer">ExoFOP-TESS bulk upload rules</a>: flat folder,
          <code>TIC{tic}C-xxYYYYMMDD.info.ext</code> names with the UT date, file codes L (light curve) and O
          (other), and proprietary period 0. Includes the four plots, {hciImage ? "the HCI summary, " : ""}the Vetstar
          PDF report and NEMESIS's own report.
        </p>} />
    </div>
  );
}

/** Shape a NEMESIS result like a v1 VettingResult so the v1 FFI cutout and
 *  ExoFOP/observables panels can be reused unchanged. */
function asVettingResult(r: NemesisResult): VettingResult | null {
  const prim = r.primary ? r.search?.[r.primary]?.params : undefined;
  if (!prim) return null;
  const s = r.star ?? { tic_id: r.meta.tic };
  return {
    star: { tic_id: r.meta.tic, sector: r.meta.sector, ra: s.ra ?? null, dec: s.dec ?? null,
            tmag: s.tmag ?? null, teff: s.teff ?? null, radius: s.radius ?? null, mass: s.mass ?? null,
            logg: s.logg ?? null, camera: null, ccd: null, crowdsap: null, source: "NEMESIS" },
    bls: { period: prim.period_d, t0: prim.t0_btjd,
           duration: prim.duration_hr != null ? prim.duration_hr / 24 : null,
           depth: prim.depth_ppt != null ? prim.depth_ppt / 1e3 : null },
    events: [], shape: {}, physics: {},
  } as unknown as VettingResult;
}

type Step = 1 | 2 | 3 | 4;

const STEP_NAMES = ["Pick a star", "Options", "Run NEMESIS", "Results"];

const RUN_STAGES: { key: string; label: string; hint: string }[] = [
  { key: "queued", label: "In the queue", hint: "One NEMESIS run happens at a time." },
  { key: "download", label: "Building the light curve", hint: "Downloading full-frame-image pixels from MAST, then aperture photometry and pixel-level decorrelation." },
  { key: "bls", label: "Transit search (BLS)", hint: "Box Least Squares looks for repeating box-shaped dips." },
  { key: "tls", label: "Refining (TLS)", hint: "Transit Least Squares fits a realistic transit shape for a sharper result." },
  { key: "report", label: "Vetting (LEO-Vetter)", hint: "18 automated tests check if the dip is noise, a binary star or a planet candidate." },
];

const VERDICTS = {
  PC: { title: "Planet candidate", chip: "bg-jade-100 text-jade-800 border-jade-300", icon: "✓",
        text: "The signal passed every LEO-Vetter test. It is worth a closer look. It is not a confirmed planet: that needs follow-up observations." },
  FP: { title: "Likely false positive", chip: "bg-coral-100 text-coral-800 border-coral-300", icon: "✕",
        text: "The dip looks real, but its shape or behaviour points to something other than a planet, most often an eclipsing binary or a nearby star." },
  FA: { title: "Needs a closer look", chip: "bg-apricot-100 text-apricot-800 border-apricot-300", icon: "!",
        text: "At least one false-alarm test flagged the signal. It may be noise or stellar activity. LEO-Vetter is strict for shallow dips, so check the folded light curve yourself." },
} as const;

// Share of the progress bar for each stage: [start %, end %]. Rough split
// from where a NEMESIS run spends its time.
const STAGE_SPAN: Record<string, [number, number]> = {
  queued: [0, 5], download: [5, 35], bls: [35, 60], tls: [60, 85], report: [85, 98], done: [100, 100],
};
// Typical seconds per stage on a small instance; sets how fast the bar creeps.
const STAGE_TYPICAL_S: Record<string, number> = { queued: 30, download: 240, bls: 180, tls: 300, report: 120 };

const TIPS = [
  "TESS takes a picture of a large patch of sky every 30 minutes (10 minutes in later years), for about 27 days.",
  "A planet the size of Earth dims a Sun-like star by less than 0.01 %. That is why the noise has to be removed so carefully.",
  "NEMESIS builds its own light curve from the full-frame images, so it can look at stars that never got a 2-minute light curve.",
  "M dwarfs are small, cool stars. A planet blocks more of their light, so small planets are easier to find around them.",
  "BLS looks for box-shaped dips; TLS uses a realistic transit shape with darker edges (limb darkening).",
  "Most signals are not planets: eclipsing binaries, spots and spacecraft jitter can all make dips. That is what LEO-Vetter checks.",
  "A planet candidate still needs follow-up, such as ground-based photometry or radial velocities, before it can be called a planet.",
  "Free servers are slow, so a run can take 10 to 30 minutes. You can switch tabs; the run keeps going.",
];

/** Estimated progress: the stage sets the range, time inside it creeps toward
 *  the end of the range but never reaches it, so the bar never stalls or lies. */
function estimateProgress(stage: string, secondsInStage: number): number {
  const [a, b] = STAGE_SPAN[stage] ?? [0, 5];
  if (a === b) return a;
  const typical = STAGE_TYPICAL_S[stage] ?? 120;
  return a + (b - a) * (1 - Math.exp(-secondsInStage / typical)) * 0.95;
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function RunProgress({ job, startedAt }: { job: NemesisJobStatus | null; startedAt: number | null }) {
  const [now, setNow] = useState(Date.now());
  const stageRef = useRef<{ key: string; since: number }>({ key: "queued", since: Date.now() });
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);
  const stage = job?.stage ?? "queued";
  if (stageRef.current.key !== stage) stageRef.current = { key: stage, since: Date.now() };
  const pct = estimateProgress(stage, (now - stageRef.current.since) / 1000);
  const elapsed = startedAt ? (now - startedAt) / 1000 : 0;
  const tip = TIPS[Math.floor(elapsed / 20) % TIPS.length];
  const label = RUN_STAGES.find((s) => s.key === stage)?.label ?? "Working";
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-sm">
        <span className="font-semibold text-heading">{label}…</span>
        <span className="font-mono text-muted tabular">{Math.round(pct)} % · {mmss(elapsed)} elapsed</span>
      </div>
      <div className="h-3 w-full rounded-full bg-ink-100 overflow-hidden" role="progressbar"
           aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label="NEMESIS run progress">
        <div className="h-full rounded-full bg-gradient-to-r from-iris-600 via-lagoon to-ember transition-[width] duration-1000 ease-out relative overflow-hidden"
             style={{ width: `${pct}%` }}>
          <div className="absolute inset-0 progress-shimmer" />
        </div>
      </div>
      <p className="text-xs text-muted">
        The percentage is an estimate from the current step. {stage === "queued" && elapsed > 20
          ? "A free server may be waking up from sleep; this can take a minute."
          : ""}
      </p>
      <div className="rounded-lg border border-line bg-surface-muted p-3 text-sm flex gap-2" aria-live="off">
        <span aria-hidden="true">💡</span><span key={tip} className="animate-fade-in">{tip}</span>
      </div>
    </div>
  );
}

function Stepper({ step }: { step: Step }) {
  return (
    <ol className="flex flex-wrap gap-2" aria-label="Progress">
      {STEP_NAMES.map((name, i) => {
        const n = (i + 1) as Step;
        const state = n < step ? "done" : n === step ? "now" : "todo";
        return (
          <li key={name} aria-current={state === "now" ? "step" : undefined}
              className={`chip border ${state === "now" ? "bg-iris-600 text-white border-iris-600"
                : state === "done" ? "bg-iris-50 text-iris-700 border-iris-200" : "bg-surface text-muted border-line"}`}>
            <span className="font-mono">{n}</span> {name}
          </li>
        );
      })}
    </ol>
  );
}

function Num({ label, value, unit, help }: { label: string; value: number | null | undefined; unit?: string; help: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface-muted p-3" title={help}>
      <div className="text-xs text-muted">{label}</div>
      <div className="font-display text-xl text-heading tabular">
        {value == null ? "—" : value.toPrecision(4)} {value != null && unit && <span className="text-sm text-muted font-sans">{unit}</span>}
      </div>
    </div>
  );
}

function LeoList({ tests }: { tests: LeoTest[] }) {
  return (
    <div className="grid md:grid-cols-2 gap-4">
      {(["FA", "FP"] as const).map((g) => (
        <div key={g}>
          <h4 className="text-sm font-semibold mb-2">
            {g === "FA" ? "Is it real? (false-alarm tests)" : "Is it a planet? (false-positive tests)"}
          </h4>
          <ul className="space-y-1.5">
            {tests.filter((t) => t.group === g).map((t) => (
              <li key={t.key}>
                <details className="group rounded-md border border-line bg-surface px-3 py-2">
                  <summary className="flex items-center justify-between gap-2 cursor-pointer list-none">
                    <span className="text-sm">{t.title}</span>
                    <span className={`chip ${t.passed ? "bg-jade-100 text-jade-800" : "bg-coral-100 text-coral-800"}`}>
                      {t.passed ? "✓ pass" : "✕ flag"}
                    </span>
                  </summary>
                  <p className="text-xs text-muted mt-2">
                    {t.passed ? "Passed. A flag here would mean: " : "Flagged: "}{t.meaning}
                  </p>
                  <p className="text-[11px] font-mono text-muted mt-1 break-words">{t.detail}</p>
                </details>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export default function NemesisWizard({ onUseQuickCheck }: { onUseQuickCheck: () => void }) {
  const [status, setStatus] = useState<NemesisStatus | null>(null);
  const [step, setStep] = useState<Step>(1);
  const [tic, setTic] = useState("");
  const [sectors, setSectors] = useState<SectorInfo[] | null>(null);
  const [sector, setSector] = useState<number | null>(null);
  const [lookup, setLookup] = useState<"idle" | "busy" | "error">("idle");
  const [skipped, setSkipped] = useState<number[]>([]);
  const [opts, setOpts] = useState<Omit<NemesisJobRequest, "tic" | "sector">>({
    cadence: "30 minute", minP: 0.5, N_transits: 3, do_TLS_Search: true,
  });
  const [job, setJob] = useState<NemesisJobStatus | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<NemesisResult | null>(null);
  const [files, setFiles] = useState<{ name: string; bytes: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [hci, setHci] = useState<any>(null);
  const [hciBusy, setHciBusy] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    nemesisStatus().then(setStatus).catch(() => setStatus({ enabled: false }));
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, []);

  const findSectors = async () => {
    const id = Number(tic);
    if (!Number.isInteger(id) || id <= 0) { setError("Enter a TIC number, for example 103633434."); return; }
    setError(null); setLookup("busy"); setSectors(null); setSector(null);
    try {
      let s: SectorInfo[];
      try {
        // Ask the worker which sectors NEMESIS can run; others make NEMESIS fail.
        const ns = await nemesisSectors(id);
        s = ns.sectors.map((x) => ({ sector: x.sector, providers: [] }));
        setSkipped(ns.unsupported);
      } catch {
        s = await mastSectors(id);  // older worker without /sectors
        setSkipped([]);
      }
      setSectors(s); setSector(s.length ? s[0].sector : null); setLookup("idle");
    } catch (e: any) {
      setLookup("error"); setError(e.message);
    }
  };

  const poll = (id: string) => {
    nemesisJob(id).then(async (s) => {
      setJob(s);
      if (s.state === "done") {
        const [r, f] = await Promise.all([nemesisResult(id), nemesisFiles(id).catch(() => [])]);
        setResult(r); setFiles(f); setStep(4);
      } else if (s.state === "error") {
        setError(s.error ?? "NEMESIS stopped with an error.");
      } else {
        timer.current = window.setTimeout(() => poll(id), 4000);
      }
    }).catch((e) => { setError(e.message); });
  };

  const run = async () => {
    if (sector == null) return;
    setError(null); setResult(null); setHci(null); setStep(3); setJob(null); setStartedAt(Date.now());
    try {
      const { job_id } = await submitNemesis({ tic: Number(tic), sector, ...opts });
      setJobId(job_id);
      poll(job_id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const reset = () => { setStep(1); setJob(null); setJobId(null); setResult(null); setError(null); setHci(null); };

  if (status && !status.enabled) {
    return (
      <section className="card p-6 space-y-3">
        <h2 className="text-lg font-bold">NEMESIS is not set up on this server</h2>
        <p className="text-sm text-muted">
          The guided NEMESIS search needs the separate NEMESIS worker. You can still vet a light curve with
          Quick check, vetstar's built-in BLS pipeline.
        </p>
        <button className="btn-accent" onClick={onUseQuickCheck}>Use Quick check</button>
      </section>
    );
  }

  const primary = result?.primary ? result.search?.[result.primary] : undefined;
  const p = primary?.params;
  const verdict = primary?.leo.available && primary.leo.disposition ? VERDICTS[primary.leo.disposition] : null;
  const vr = result ? asVettingResult(result) : null;
  const stageIdx = job ? Math.max(0, RUN_STAGES.findIndex((s) => s.key === job.stage)) : 0;

  return (
    <section className="space-y-5">
      <div className="card p-5 space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-xl font-bold">Find transiting planets with NEMESIS</h2>
            <p className="text-sm text-muted max-w-2xl">
              NEMESIS builds its own light curve from TESS full-frame images, searches it for repeating dips and
              runs LEO-Vetter's automated tests. Made for M dwarfs, but it works on any star TESS observed.
            </p>
          </div>
          {status?.online === false && (
            <span className="chip bg-apricot-100 text-apricot-800">Worker offline: runs may fail</span>
          )}
          {status?.online && status.queue_depth != null && status.queue_depth > 0 && (
            <span className="chip bg-lagoon-100 text-lagoon-800">{status.queue_depth} run(s) ahead of you</span>
          )}
        </div>
        <Stepper step={step} />
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-coral-300 bg-coral-50 text-coral-800 p-3 text-sm flex justify-between gap-3">
          <span>{error}</span>
          {step === 3 && <button className="underline" onClick={reset}>Start over</button>}
        </div>
      )}

      {step === 1 && (
        <div className="card p-5 space-y-4">
          <label className="block">
            <span className="text-sm font-semibold">TIC number</span>
            <span className="block text-xs text-muted">The star's TESS Input Catalog ID. Try 103633434 (TOI-1235, a known planet host).</span>
            <div className="flex gap-2 mt-2 flex-wrap">
              <input value={tic} onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))} inputMode="numeric"
                     onKeyDown={(e) => e.key === "Enter" && findSectors()}
                     className="font-mono rounded-lg border border-line bg-surface px-3 py-2 w-56" placeholder="103633434" />
              <button className="btn-primary" onClick={findSectors} disabled={!tic || lookup === "busy"}>
                {lookup === "busy" ? "Looking up…" : "Find sectors"}
              </button>
            </div>
          </label>
          {sectors && (
            sectors.length === 0
              ? <p className="text-sm text-muted">TESS has no light curves listed for this star yet.</p>
              : (
                <div>
                  <span className="text-sm font-semibold">Sector</span>
                  <span className="block text-xs text-muted">A sector is one ~27-day stare by TESS. Pick one to search.</span>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {sectors.map((s) => (
                      <button key={s.sector} onClick={() => setSector(s.sector)} aria-pressed={sector === s.sector}
                              className={`chip border px-3 py-1 font-mono ${sector === s.sector ? "bg-iris-600 text-white border-iris-600" : "bg-surface border-line text-body hover:border-iris-400"}`}>
                        S{s.sector}
                      </button>
                    ))}
                  </div>
                  {skipped.length > 0 && (
                    <p className="text-xs text-muted mt-2">
                      Not listed: sector{skipped.length > 1 ? "s" : ""} {skipped.join(", ")}. TESS observed this
                      star there, but this NEMESIS version cannot process those sectors yet. Use Quick check for them.
                    </p>
                  )}
                </div>
              )
          )}
          <div className="flex justify-end">
            <button className="btn-primary" disabled={sector == null} onClick={() => {
              // Full-frame-image cadence by mission phase.
              setOpts({ ...opts, cadence: sector! <= 26 ? "30 minute" : "10 minute" });
              setStep(2);
            }}>Next: options</button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="card p-5 space-y-4">
          <p className="text-sm text-muted">The defaults suit most stars. Change them only if you know why.</p>
          {sector != null && sector >= 56 && (
            <p role="note" className="rounded-lg border border-apricot-300 bg-apricot-50 text-apricot-800 p-3 text-sm">
              Sector {sector} uses 200-second full-frame images. This NEMESIS version lists only 30-minute,
              10-minute, 2-minute and 20-second cadences and was tested on sectors 1–26, so the run may fail.
              Quick check works for any sector.
            </p>
          )}
          <div className="grid sm:grid-cols-2 gap-4">
            <label className="block">
              <span className="text-sm font-semibold">Cadence</span>
              <span className="block text-xs text-muted">How often TESS saved a full-frame image. Set for you from the sector.</span>
              <select value={opts.cadence} onChange={(e) => setOpts({ ...opts, cadence: e.target.value as NemesisJobRequest["cadence"] })}
                      className="mt-1 rounded-lg border border-line bg-surface px-3 py-2 w-full">
                <option value="30 minute">30 minute (sectors 1–26)</option>
                <option value="10 minute">10 minute (sectors 27–55)</option>
                {/* 200-s FFIs (sector 56+) are not a NEMESIS cadence option. */}
                <option value="2 minute">2 minute (selected stars only)</option>
                <option value="20 second">20 second (selected stars only)</option>
              </select>
            </label>
            <label className="block">
              <span className="text-sm font-semibold">Transits needed</span>
              <span className="block text-xs text-muted">Minimum number of dips. Three is safer; two finds longer periods.</span>
              <input type="number" min={1} max={10} value={opts.N_transits}
                     onChange={(e) => setOpts({ ...opts, N_transits: Number(e.target.value) })}
                     className="mt-1 rounded-lg border border-line bg-surface px-3 py-2 w-full font-mono" />
            </label>
            <label className="block">
              <span className="text-sm font-semibold">Shortest period (days)</span>
              <input type="number" min={0.1} step={0.1} value={opts.minP ?? ""}
                     onChange={(e) => setOpts({ ...opts, minP: e.target.value ? Number(e.target.value) : undefined })}
                     className="mt-1 rounded-lg border border-line bg-surface px-3 py-2 w-full font-mono" />
            </label>
            <label className="block">
              <span className="text-sm font-semibold">Longest period (days)</span>
              <span className="block text-xs text-muted">Leave empty to let NEMESIS decide from the data length.</span>
              <input type="number" min={0.2} step={0.5} value={opts.maxP ?? ""}
                     onChange={(e) => setOpts({ ...opts, maxP: e.target.value ? Number(e.target.value) : undefined })}
                     className="mt-1 rounded-lg border border-line bg-surface px-3 py-2 w-full font-mono" />
            </label>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!opts.do_TLS_Search}
                   onChange={(e) => setOpts({ ...opts, do_TLS_Search: e.target.checked })} />
            Refine with Transit Least Squares (slower, more accurate)
          </label>
          <div className="flex justify-between">
            <button className="text-sm underline text-muted" onClick={() => setStep(1)}>Back</button>
            <button className="btn-accent" onClick={run}>
              Run NEMESIS on TIC {tic} · S{sector}
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="card p-5 space-y-4" aria-live="polite">
          <RunProgress job={job} startedAt={startedAt} />
          <p className="text-sm text-muted">A run usually takes 5 to 30 minutes. You can switch to another tab and come back.</p>
          <ol className="space-y-2">
            {RUN_STAGES.map((s, i) => {
              const done = i < stageIdx, now = i === stageIdx && job?.state !== "error";
              return (
                <li key={s.key} className="flex gap-3 items-start">
                  <span className={`mt-0.5 h-5 w-5 shrink-0 rounded-full grid place-items-center text-[11px] font-bold
                    ${done ? "bg-jade-500 text-white" : now ? "bg-ember text-white animate-pulse" : "bg-ink-100 text-muted"}`}>
                    {done ? "✓" : i + 1}
                  </span>
                  <div>
                    <div className={`text-sm font-semibold ${now ? "text-heading" : done ? "text-body" : "text-muted"}`}>
                      {s.label}{s.key === "queued" && job?.position ? ` (position ${job.position})` : ""}
                    </div>
                    {now && <div className="text-xs text-muted">{s.hint}</div>}
                  </div>
                </li>
              );
            })}
          </ol>
          {job?.log_tail?.length ? (
            <details>
              <summary className="text-xs text-muted cursor-pointer">Show the NEMESIS log</summary>
              <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-dusk-950 text-cream-100 text-[11px] p-3 font-mono">
                {job.log_tail.join("\n")}
              </pre>
            </details>
          ) : null}
        </div>
      )}

      {step === 4 && result && (
        <div className="space-y-5">
          {!primary ? (
            <div className="card p-5">
              <h3 className="text-lg font-bold">No transit search result</h3>
              <p className="text-sm text-muted">NEMESIS made a light curve but found no signal to search. Try another sector.</p>
            </div>
          ) : (
            <>
              <div className={`card p-5 border-2 ${verdict ? verdict.chip.split(" ").find((c) => c.startsWith("border-")) : ""}`}>
                <div className="flex items-start gap-4">
                  <span className={`h-10 w-10 shrink-0 rounded-full grid place-items-center text-lg font-bold border ${verdict?.chip ?? "bg-ink-100"}`}>
                    {verdict?.icon ?? "?"}
                  </span>
                  <div className="space-y-1">
                    <div className="text-xs text-muted font-mono">
                      TIC {result.meta.tic} · Sector {result.meta.sector} · {primary.params.method} · NEMESIS {result.meta.nemesis_version}
                      {result.meta.lite && (
                        <span className="chip ml-2 bg-lagoon-100 text-lagoon-800 font-sans"
                              title="This server runs NEMESIS with a coarser period grid (oversampling 3 instead of 9) to fit in a small amount of memory. Very shallow signals may be missed.">
                          Light mode
                        </span>
                      )}
                    </div>
                    <h3 className="text-2xl font-bold">{verdict?.title ?? "LEO-Vetter did not run"}</h3>
                    <p className="text-sm max-w-3xl">{verdict?.text ?? primary.leo.reason}</p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Num label="Period" value={p?.period_d} unit="d" help="Time between transits." />
                <Num label="Depth" value={p?.depth_ppt} unit="ppt" help="How much the star dims, in parts per thousand." />
                <Num label="Duration" value={p?.duration_hr} unit="h" help="How long each transit lasts." />
                <Num label="Size" value={p?.radius_earth} unit="R⊕" help="Implied radius of the transiting object, in Earth radii." />
              </div>

              <div className="space-y-4">
                <h3 className="text-lg font-bold">The plots, step by step</h3>
                <PaperFigure name="light_curve" b64={result.figures?.light_curve} />
                <PaperFigure name="periodogram" b64={result.figures?.periodogram}>
                  <MiniChart title={`${primary.params.method} periodogram`} xLabel="Period (days)" yLabel="SDE" logX
                    series={[{ x: primary.periodogram.period ?? [], y: primary.periodogram.sde ?? [], kind: "line", color: "text-ink-900", label: "SDE" }]} />
                </PaperFigure>
                <PaperFigure name="fold" b64={result.figures?.fold}>
                  <MiniChart title="Folded light curve" xLabel="Orbital phase" yLabel="Relative flux"
                    series={[
                      { x: primary.fold.phase ?? [], y: primary.fold.flux ?? [], kind: "dots", color: "text-ink-400", label: "cadences" },
                      { x: primary.fold.bin_phase ?? [], y: primary.fold.bin_flux ?? [], kind: "line", color: "text-coral-600", label: "binned" },
                    ]} />
                </PaperFigure>
                {result.figures?.odd_even && <PaperFigure name="odd_even" b64={result.figures.odd_even} />}
              </div>

              {vr && (
                <div className="-mx-6">
                  <ObservablesSection result={vr} />
                  {vr.star.ra != null && <FfiCutoutPanel result={vr} />}
                </div>
              )}

              {primary.leo.available && primary.leo.tests && (
                <div className="card p-5 space-y-3">
                  <h3 className="text-lg font-bold">LEO-Vetter tests</h3>
                  <p className="text-sm text-muted">Tap a test to see what it checks and the measured value.</p>
                  <LeoList tests={primary.leo.tests} />
                </div>
              )}

              <div className="card p-5 space-y-3">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    <h3 className="text-lg font-bold">Extra: Habitability Chance Index (HCI v2)</h3>
                    <p className="text-sm text-muted">A rough score of how Earth-like this candidate could be. For fun and teaching, not a discovery claim.</p>
                  </div>
                  <button className="btn-primary" disabled={hciBusy || !p?.period_d} onClick={async () => {
                    setHciBusy(true);
                    try {
                      setHci(await nemesisHci({ tic: result.meta.tic, period_d: p?.period_d, radius_earth: p?.radius_earth,
                                                duration_hr: p?.duration_hr, depth_ppt: p?.depth_ppt, leo: primary.leo }));
                    } catch (e: any) { setError(e.message); } finally { setHciBusy(false); }
                  }}>{hciBusy ? "Scoring…" : hci ? "Score again" : "Score HCI"}</button>
                </div>
                {hci && (
                  <div className="space-y-2">
                    <div className="font-display text-3xl text-heading">{hci.hci?.hci != null ? `${Math.round(hci.hci.hci)} / 100` : "—"}
                      <span className="text-base text-muted ml-2 font-sans">{hci.hci?.tier}</span></div>
                    {hci.hci_image && <img src={`data:image/png;base64,${hci.hci_image}`} alt="HCI summary chart" className="max-w-full rounded-md border border-line" />}
                  </div>
                )}
              </div>
            </>
          )}

          {primary && jobId && (
            <ExportCard jobId={jobId} result={result} files={files} hciImage={hci?.hci_image ?? null} />
          )}

          {files.length > 0 && jobId && (
            <div className="card p-5 space-y-2">
              <h3 className="text-lg font-bold">NEMESIS report files</h3>
              <p className="text-sm text-muted">The full multi-page NEMESIS vetting report, as made by the NEMESIS pipeline.</p>
              <ul className="flex flex-wrap gap-2">
                {files.map((f) => (
                  <li key={f.name}>
                    <a className="chip border border-line bg-surface-muted hover:border-iris-400 font-mono" target="_blank" rel="noreferrer"
                       href={nemesisFileUrl(jobId, f.name)}>{f.name.endsWith(".pdf") ? "📄" : "🖼"} {f.name}</a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex justify-between items-center flex-wrap gap-3 text-xs text-muted">
            <span>
              Please cite NEMESIS (Feliz et al. 2021, AJ 161, 247; Feliz et al. 2026) and LEO-Vetter (Kunimoto et al. 2025).
            </span>
            <button className="btn-primary" onClick={reset}>Search another star</button>
          </div>
        </div>
      )}
    </section>
  );
}
