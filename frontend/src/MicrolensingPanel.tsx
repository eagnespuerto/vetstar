import { useState } from "react";
import MicrolensingClassifier, { type ClassifierPrefill } from "./MicrolensingClassifier";
import MicrolensingCoverage from "./MicrolensingCoverage";
import type { CoverageEvent } from "./api";

// Top-level container for the microlensing pipeline. Two sub-modules:
//   A) Model-comparison classifier (PSPL vs flare vs null) on a user-flagged window.
//   B) TESS sector-overlap targeting from an uploaded event catalog.
// The Coverage table can hand off a selected event to the Classifier via the
// prefill channel below.

type SubTab = "classifier" | "coverage";

export default function MicrolensingPanel() {
  const [tab, setTab] = useState<SubTab>("classifier");
  const [prefill, setPrefill] = useState<ClassifierPrefill | null>(null);

  const handoff = (evt: CoverageEvent) => {
    // First observable-in-window sector, else the first sector at all, else null.
    const hitSector =
      evt.sectors.find((s) => s.t0_in_window) ??
      evt.sectors[0] ??
      null;
    setPrefill({
      source: "coverage",
      event_id: evt.event_id,
      ra: evt.ra,
      dec: evt.dec,
      t0: evt.t0,
      tE: evt.tE,
      sector: hitSector?.sector ?? null,
      camera: hitSector?.camera ?? null,
      ccd: hitSector?.ccd ?? null,
      observable: evt.observable,
    });
    setTab("classifier");
  };

  return (
    <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
      <div className="rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="text-lg font-semibold text-slate-800">
          Microlensing pipeline
        </h2>
        <p className="text-sm text-slate-600 mt-1">
          Detect and characterize single-lens microlensing events in TESS light curves.
          Two independent tools: the <strong>classifier</strong> fits PSPL, flare, and
          null models to a user-flagged positive excursion; the <strong>coverage</strong>
          finder tells you which known events actually fall inside TESS sectors.
        </p>
      </div>

      <div className="flex gap-2 border-b">
        <SubTabButton active={tab === "classifier"} onClick={() => setTab("classifier")}>
          Classifier (Module A)
        </SubTabButton>
        <SubTabButton active={tab === "coverage"} onClick={() => setTab("coverage")}>
          TESS coverage (Module B)
        </SubTabButton>
      </div>

      {tab === "classifier" && (
        <MicrolensingClassifier
          prefill={prefill}
          onDismissPrefill={() => setPrefill(null)}
        />
      )}
      {tab === "coverage" && <MicrolensingCoverage onAnalyzeInModuleA={handoff} />}

      <p className="text-[10px] text-slate-500 leading-relaxed">
        TESS microlensing caveats + best-practice guidance in this pipeline
        follow{" "}
        <a
          href="https://iopscience.iop.org/article/10.3847/2041-8213/ae7a50"
          target="_blank"
          rel="noopener noreferrer"
          className="underline text-slate-600 hover:text-slate-800"
        >
          Harris, Dragomir, Bachelet, Fausnaugh &amp; Johnson (2026), ApJL
          1005, L33
        </a>{" "}
        — the first-ever bound-planet microlensing detection in TESS data.
        Their pyLIMA joint TESS + Gaia fit of Gaia23bra sets the standard for
        breaking the θ_E ↔ M_L degeneracy that TESS-only single-band
        photometry cannot resolve.
      </p>
    </main>
  );
}

function SubTabButton({
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
