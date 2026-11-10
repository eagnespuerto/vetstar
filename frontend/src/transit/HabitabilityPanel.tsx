import { useState } from "react";
import { ShareToImgbbButton } from "../ShareButton";

export function HabitabilityPanel({ data }: { data: any }) {
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
            <div>Habitability Chance Index{hci.hci_version ? ` v${hci.hci_version}` : ""}</div>
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
            {data.planet.age_gyr != null && <span>Age ≈ {data.planet.age_gyr.toFixed(2)} Gyr <span className="text-[10px] opacity-60">({data.planet.age_source})</span></span>}
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
              <p className="mt-1 italic">Ref: {[hci.paper_ref, ...(hci.references || [])].join("; ")}</p>
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
