import { useEffect, useRef, useState } from "react";
import { lookupTerm } from "../glossary";

export function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function CopyField({ label, value }: { label: string; value: string }) {
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

export function KV({
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

/**
 * Tooltip wrapper — shows a dotted underline; on hover/tap reveals the
 * glossary definition. Works on both desktop (hover) and mobile (tap).
 */
export function Tip({ term, children }: { term: string; children?: React.ReactNode }) {
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

export function formatVal(v: any): string {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    if (Math.abs(v) < 1e-4 && v !== 0) return v.toExponential(3);
    if (Math.abs(v) >= 1e5) return v.toExponential(3);
    return Number.isInteger(v) ? v.toString() : v.toFixed(4);
  }
  return String(v);
}
