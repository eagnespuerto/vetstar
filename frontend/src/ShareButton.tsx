import { useState } from "react";

export function ShareIcon({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M21.95 6.536a1.523 1.523 0 0 0-1.07-.445H3.12c-.417 0-.77.148-1.07.445A1.463 1.463 0 0 0 1.605 7.6v8.8c0 .417.148.77.445 1.07.297.296.653.445 1.07.445h17.76c.417 0 .773-.149 1.07-.446.296-.296.445-.652.445-1.069V7.6c0-.417-.149-.77-.445-1.064zM12 16.4c-2.648 0-4.8-2.152-4.8-4.8 0-2.648 2.152-4.8 4.8-4.8 2.648 0 4.8 2.152 4.8 4.8 0 2.648-2.152 4.8-4.8 4.8zm0-7.2a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8z" />
    </svg>
  );
}

function CopyMini({ value, tooltip, label }: { value: string; tooltip: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className={`text-[10px] px-1.5 py-0.5 rounded transition ${
        copied ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600 hover:bg-purple-100 hover:text-purple-700"
      }`}
      title={tooltip}
      onClick={() => {
        navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
    >
      {copied ? "Copied!" : label || "URL"}
    </button>
  );
}

export function ShareToImgbbButton({
  base64,
  title,
  label,
}: {
  base64: string;
  title: string;
  label: string;
}) {
  const [state, setState] = useState<"idle" | "uploading" | "done" | "error">("idle");
  const [result, setResult] = useState<{ link: string; markdown: string; bbcode: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = async () => {
    setState("uploading");
    setError(null);
    try {
      const { uploadImage, markdownEmbed, bbcodeEmbed } = await import("./imgbb");
      const res = await uploadImage(base64, title.replace(/[^a-zA-Z0-9_-]/g, "_"));
      setResult({ link: res.url, markdown: markdownEmbed(res.url, label), bbcode: bbcodeEmbed(res.url) });
      setState("done");
    } catch (e: any) {
      setError(e.message || String(e));
      setState("error");
    }
  };

  if (state === "idle") {
    return (
      <button
        onClick={upload}
        className="text-[10px] px-2 py-0.5 bg-slate-100 text-slate-600 rounded hover:bg-purple-100 hover:text-purple-700 flex items-center gap-1 transition"
        title="Upload to ImgBB for a shareable link"
      >
        <ShareIcon size={10} />
        Share
      </button>
    );
  }
  if (state === "uploading") return <span className="text-[10px] text-slate-400">uploading…</span>;
  if (state === "error")
    return (
      <span className="text-[10px] text-red-500 cursor-pointer" title={error || ""} onClick={upload}>
        failed — retry?
      </span>
    );

  return (
    <span className="flex items-center gap-1">
      <CopyMini value={result!.link} tooltip="Copy image URL" />
      <CopyMini value={result!.bbcode} tooltip="Copy BBCode [img]" label="BBC" />
      <CopyMini value={result!.markdown} tooltip="Copy Markdown" label="MD" />
    </span>
  );
}
