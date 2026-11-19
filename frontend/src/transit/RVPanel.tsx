import { useState } from "react";
import { fetchRadialVelocity } from "../api";

export function RVPanel({ ticId, periodD, mstar, onUseMass, massInHci, massSource }: { ticId: number | null; periodD: number | null; mstar: number | null; onUseMass?: (m: number) => void; massInHci?: number | null; massSource?: string | null }) {
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
