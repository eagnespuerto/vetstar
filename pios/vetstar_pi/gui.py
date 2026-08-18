"""Tkinter GUI for VetStar Pi.

Two tabs (Transit / Microlensing), each with a file-picker, parameter
inputs, an embedded matplotlib canvas, verdict text, and export buttons.

Pipelines run in a background thread so the GUI stays responsive on a
1 GB Pi.  All matplotlib work uses the TkAgg backend which shares the
image buffer with Tk — no PIL required.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from dataclasses import asdict
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from . import __version__
from .fitsio import read_any, read_csv, read_json
from .microlens import analyze_event as microlens_analyze
from .pdf_report import build_microlens_pdf, build_transit_pdf
from .plots import build_microlens_fit, build_transit_overview
from .transit import clean_lightcurve, run_vetting


APP_TITLE = f"VetStar Pi v{__version__}"


class _JobBus:
    """Simple thread → Tk-main-loop callback bridge."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        root.after(100, self._pump)

    def submit(self, callback):
        self.q.put(callback)

    def _pump(self):
        while True:
            try:
                cb = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                cb()
            except Exception as exc:
                messagebox.showerror("Error", str(exc))
        self.root.after(100, self._pump)


class TransitTab(ttk.Frame):
    def __init__(self, master, bus: _JobBus):
        super().__init__(master)
        self.bus = bus
        self.lc = None
        self.result = None
        self._t_clean = None
        self._f_clean = None

        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(top, text="Open FITS/JSON…", command=self.open_file).pack(side=tk.LEFT)
        self.file_lbl = ttk.Label(top, text="(no file)", foreground="grey")
        self.file_lbl.pack(side=tk.LEFT, padx=8)

        params = ttk.LabelFrame(self, text="Detection sensitivity")
        params.pack(fill=tk.X, padx=8, pady=4)
        self.thr_var = tk.StringVar(value="0.997")
        self.snr_var = tk.StringVar(value="4.0")
        self.sec_var = tk.StringVar(value="3.0")
        self.oe_var = tk.StringVar(value="3.0")
        for i, (label, var) in enumerate([
            ("Threshold", self.thr_var),
            ("min SNR", self.snr_var),
            ("Secondary σ", self.sec_var),
            ("Odd/even σ", self.oe_var),
        ]):
            ttk.Label(params, text=label).grid(row=0, column=2 * i, padx=(6, 2), pady=4)
            ttk.Entry(params, textvariable=var, width=7).grid(row=0, column=2 * i + 1, padx=(0, 6))

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=8, pady=4)
        self.run_btn = ttk.Button(actions, text="Run vetting", command=self.run, state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT)
        self.png_btn = ttk.Button(actions, text="Save plot PNG…", command=self.save_png, state=tk.DISABLED)
        self.png_btn.pack(side=tk.LEFT, padx=6)
        self.pdf_btn = ttk.Button(actions, text="Save PDF report…", command=self.save_pdf, state=tk.DISABLED)
        self.pdf_btn.pack(side=tk.LEFT, padx=6)
        self.json_btn = ttk.Button(actions, text="Save JSON…", command=self.save_json, state=tk.DISABLED)
        self.json_btn.pack(side=tk.LEFT, padx=6)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=8, pady=(2, 4))

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Plot pane
        plot_frame = ttk.Frame(body)
        body.add(plot_frame, weight=3)
        self.fig = Figure(figsize=(6, 5), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Text pane
        text_frame = ttk.Frame(body)
        body.add(text_frame, weight=2)
        self.text = tk.Text(text_frame, width=42, height=25, wrap="word",
                            font=("TkFixedFont", 9))
        self.text.pack(fill=tk.BOTH, expand=True)
        self._set_text("Open a TESS/Kepler SPOC FITS file (or a JSON light curve) "
                       "and click 'Run vetting'.")

    def _set_text(self, msg):
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, msg)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open light curve",
            filetypes=[
                ("Light curve", "*.fits *.fits.gz *.json"),
                ("FITS", "*.fits *.fits.gz"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.lc = read_any(path)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.file_lbl.config(text=os.path.basename(path), foreground="black")
        self.run_btn.config(state=tk.NORMAL)
        self.result = None
        self.png_btn.config(state=tk.DISABLED)
        self.pdf_btn.config(state=tk.DISABLED)
        self.json_btn.config(state=tk.DISABLED)
        self._set_text(
            f"Loaded {os.path.basename(path)}\n"
            f"  cadences: {len(self.lc.t)}\n"
            f"  TIC: {self.lc.star.tic_id}\n"
            f"  sector: {self.lc.star.sector}\n"
            f"  Tmag: {self.lc.star.tmag}\n"
            f"Ready to run."
        )

    def run(self):
        if self.lc is None:
            return
        try:
            thr = float(self.thr_var.get())
            snr = float(self.snr_var.get())
            sec = float(self.sec_var.get())
            oe = float(self.oe_var.get())
        except ValueError:
            messagebox.showerror("Bad input", "Sensitivity fields must be numeric.")
            return

        self.run_btn.config(state=tk.DISABLED)
        self.progress.start(50)
        self._set_text("Running pipeline (BLS / Lomb-Scargle / events / verdict)…\n"
                       "This can take 5–20 s on a Pi depending on cadence count.")

        def worker():
            try:
                res = run_vetting(
                    self.lc,
                    detect_threshold=thr, detect_min_snr=snr,
                    secondary_sigma=sec, odd_even_sigma=oe,
                )
                t, f, _, _, _ = clean_lightcurve(self.lc)
                self.bus.submit(lambda: self._on_done(res, t, f))
            except Exception as exc:
                self.bus.submit(lambda e=exc: self._on_err(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, result, t, f):
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL)
        self.result = result
        self._t_clean = t
        self._f_clean = f

        # Redraw with the overview
        self.fig.clf()
        overview = build_transit_overview(result, t, f)
        # Copy axes from overview onto our persistent figure
        # (simpler: swap the figure entirely)
        self.canvas.figure = overview
        self.fig = overview
        self.canvas.draw()

        v = result.verdict
        lines = [
            f"Verdict: {v.get('headline', '—')}",
            f"Category: {v.get('category', '—')}",
            f"Confidence: {v.get('confidence', 0):.2f}",
            "",
            f"BLS period : {result.bls.get('period', 0):.6f} d",
            f"BLS depth  : {result.bls.get('depth', 0):.5f}",
            f"BLS SDE    : {result.bls.get('sde', 0):.2f}",
            f"LS  period : {result.lomb_scargle.get('top_period', 0):.6f} d",
            f"Events     : {len(result.events)}",
            "",
        ]
        if result.physics.get("available"):
            ph = result.physics
            lines.append(f"R_companion: {ph['R_companion_Rjup']:.2f} R_Jup ({ph['category']})")
        oe = result.odd_even
        if oe.get("available"):
            lines.append(f"Odd/even σ : {oe['sigma']:.2f}  flag_eb={oe['flag_eb']}")
        sec = result.secondary
        if sec.get("available"):
            lines.append(f"Secondary σ: {sec['sigma']:.2f}  detected={sec['detected']}")
        cen = result.centroid
        if cen.get("available"):
            lines.append(f"Centroid   : Δcol={cen['shift_col_sigma']:.2f}σ  "
                         f"Δrow={cen['shift_row_sigma']:.2f}σ  on_target={cen['on_target']}")
        lines.append("")
        lines.append("Reasons:")
        for r in v.get("reasons", []):
            lines.append(" • " + r)

        self._set_text("\n".join(lines))
        for b in (self.png_btn, self.pdf_btn, self.json_btn):
            b.config(state=tk.NORMAL)

    def _on_err(self, exc):
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL)
        messagebox.showerror("Pipeline error", str(exc))
        self._set_text(f"Error: {exc}")

    def save_png(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile="vetstar_pi_transit.png",
        )
        if not path:
            return
        self.fig.savefig(path, dpi=140, bbox_inches="tight")

    def save_pdf(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile=self._suggest_name("pdf"),
        )
        if not path:
            return
        build_transit_pdf(self.result, self._t_clean, self._f_clean, path)
        messagebox.showinfo("Saved", f"Wrote {path}")

    def save_json(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile=self._suggest_name("json"),
        )
        if not path:
            return
        d = self.result.to_dict()
        # Strip periodograms from JSON so the file stays small.
        for key in ("bls", "lomb_scargle"):
            if isinstance(d.get(key), dict):
                d[key].pop("periodogram", None)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, default=str)

    def _suggest_name(self, ext):
        tic = self.result.star.tic_id if self.result else None
        return f"vetstar_pi_TIC{tic}.{ext}" if tic else f"vetstar_pi_transit.{ext}"


class MicrolensTab(ttk.Frame):
    def __init__(self, master, bus: _JobBus):
        super().__init__(master)
        self.bus = bus
        self.lc = None
        self.result = None

        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(top, text="Open CSV/JSON…", command=self.open_file).pack(side=tk.LEFT)
        self.file_lbl = ttk.Label(top, text="(no file)", foreground="grey")
        self.file_lbl.pack(side=tk.LEFT, padx=8)

        win = ttk.LabelFrame(self, text="Fit window (event coordinates)")
        win.pack(fill=tk.X, padx=8, pady=4)
        self.t_start_var = tk.StringVar()
        self.t_end_var = tk.StringVar()
        self.t0_var = tk.StringVar()
        self.label_var = tk.StringVar(value="")
        for i, (label, var) in enumerate([
            ("t_start", self.t_start_var),
            ("t_end", self.t_end_var),
            ("t0 guess", self.t0_var),
            ("event label (optional)", self.label_var),
        ]):
            ttk.Label(win, text=label).grid(row=0, column=2 * i, padx=(6, 2), pady=4)
            ttk.Entry(win, textvariable=var, width=13 if "label" not in label else 20).grid(
                row=0, column=2 * i + 1, padx=(0, 6),
            )

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=8, pady=4)
        self.run_btn = ttk.Button(actions, text="Fit PSPL / Flare / Null", command=self.run, state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT)
        self.png_btn = ttk.Button(actions, text="Save plot PNG…", command=self.save_png, state=tk.DISABLED)
        self.png_btn.pack(side=tk.LEFT, padx=6)
        self.pdf_btn = ttk.Button(actions, text="Save PDF report…", command=self.save_pdf, state=tk.DISABLED)
        self.pdf_btn.pack(side=tk.LEFT, padx=6)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=8, pady=(2, 4))

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        plot_frame = ttk.Frame(body)
        body.add(plot_frame, weight=3)
        self.fig = Figure(figsize=(6, 5), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(body)
        body.add(text_frame, weight=2)
        self.text = tk.Text(text_frame, width=42, height=25, wrap="word",
                            font=("TkFixedFont", 9))
        self.text.pack(fill=tk.BOTH, expand=True)
        self._set_text(
            "Open a CSV (columns time,flux,flux_err) or JSON light curve.\n"
            "Enter t_start / t_end around the excursion and a t0 guess near the peak.\n"
            "Click 'Fit …' to run the 3-way model comparison."
        )

    def _set_text(self, msg):
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, msg)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open microlensing light curve",
            filetypes=[("CSV/JSON", "*.csv *.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                self.lc = read_csv(path)
            else:
                self.lc = read_json(path)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.file_lbl.config(text=os.path.basename(path), foreground="black")
        t = self.lc.t
        # Prefill sensible defaults: whole range, midpoint as t0 guess
        self.t_start_var.set(f"{t.min():.4f}")
        self.t_end_var.set(f"{t.max():.4f}")
        self.t0_var.set(f"{0.5 * (t.min() + t.max()):.4f}")
        self.run_btn.config(state=tk.NORMAL)
        self.result = None
        self.png_btn.config(state=tk.DISABLED)
        self.pdf_btn.config(state=tk.DISABLED)
        self._set_text(
            f"Loaded {os.path.basename(path)}\n"
            f"  points: {len(t)}\n"
            f"  time range: [{t.min():.3f}, {t.max():.3f}]\n"
            "Adjust the window and click Fit."
        )
        # Show the raw LC
        self._show_raw()

    def _show_raw(self):
        fig = Figure(figsize=(6, 5), dpi=90)
        ax = fig.add_subplot(111)
        ax.errorbar(self.lc.t, self.lc.flux,
                    yerr=self.lc.flux_err if self.lc.flux_err is not None else None,
                    fmt="k.", ms=2, elinewidth=0.3, alpha=0.7)
        ax.set_xlabel("Time")
        ax.set_ylabel("Flux")
        ax.set_title("Raw light curve")
        fig.tight_layout()
        self.canvas.figure = fig
        self.fig = fig
        self.canvas.draw()

    def run(self):
        if self.lc is None:
            return
        try:
            t_start = float(self.t_start_var.get())
            t_end = float(self.t_end_var.get())
            t0 = float(self.t0_var.get())
        except ValueError:
            messagebox.showerror("Bad input", "t_start, t_end, t0 must be numeric.")
            return
        if t_end <= t_start:
            messagebox.showerror("Bad input", "t_end must be greater than t_start.")
            return

        self.run_btn.config(state=tk.DISABLED)
        self.progress.start(50)
        self._set_text("Fitting PSPL, Davenport-2014 flare, and null models…")

        def worker():
            try:
                fe = self.lc.flux_err
                # Fall back to per-point median-abs scatter if the file had no errors.
                import numpy as np
                if fe is None or not np.any(np.isfinite(fe)):
                    fe = np.full_like(self.lc.flux, float(np.nanstd(self.lc.flux) or 1e-3))
                res = microlens_analyze(
                    self.lc.t, self.lc.flux, fe,
                    t_start=t_start, t_end=t_end, t0_guess=t0,
                )
                self.bus.submit(lambda: self._on_done(res))
            except Exception as exc:
                self.bus.submit(lambda e=exc: self._on_err(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, result):
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL)
        self.result = result
        fig = build_microlens_fit(result)
        self.canvas.figure = fig
        self.fig = fig
        self.canvas.draw()

        lines = [
            f"Verdict: {result.verdict.upper()}",
            f"Confidence: {result.confidence:.3f}",
            "",
            "BICs (lower = better):",
            f"  PSPL  = {result.pspl.bic:.2f}",
            f"  Flare = {result.flare.bic:.2f}",
            f"  Null  = {result.null.bic:.2f}",
            "",
            f"ΔBIC(null - PSPL)  = {result.delta_bic['null_minus_pspl']:.2f}",
            f"ΔBIC(flare - PSPL) = {result.delta_bic['flare_minus_pspl']:.2f}",
            f"Symmetry score = {result.symmetry_score:.3f}",
            "",
            "PSPL best fit:",
        ]
        for name in ("t0", "tE", "u0", "f_s", "f_b"):
            lines.append(f"  {name:<3} = {result.pspl.params.get(name, float('nan')):.5g} "
                         f"± {result.pspl.param_err.get(name, float('nan')):.3g}")
        if result.observables:
            o = result.observables
            lines.append("")
            lines.append("Observables:")
            lines.append(f"  A_max        = {o['peak_magnification']:.3f}")
            lines.append(f"  Δm (mag)     = {o['peak_brightening_mag']:.3f}")
            lines.append(f"  tE (d)       = {o['einstein_timescale_d']:.3f}")
            lines.append(f"  FWHM (d)     = {o['magnification_fwhm_d']:.3f}")
        self._set_text("\n".join(lines))
        self.png_btn.config(state=tk.NORMAL)
        self.pdf_btn.config(state=tk.NORMAL)

    def _on_err(self, exc):
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL)
        messagebox.showerror("Fit error", str(exc))
        self._set_text(f"Error: {exc}")

    def save_png(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile="vetstar_pi_microlens.png",
        )
        if not path:
            return
        self.fig.savefig(path, dpi=140, bbox_inches="tight")

    def save_pdf(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile="vetstar_pi_microlens.pdf",
        )
        if not path:
            return
        build_microlens_pdf(self.result, path, target_label=self.label_var.get() or None)
        messagebox.showinfo("Saved", f"Wrote {path}")


def run_gui():
    """Entrypoint: create the root window and start the Tk mainloop."""
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1050x680")

    style = ttk.Style()
    # 'clam' looks clean on Pi OS and doesn't depend on optional themes.
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    bus = _JobBus(root)

    header = ttk.Frame(root, padding=(8, 6))
    header.pack(fill=tk.X)
    ttk.Label(header, text=APP_TITLE, font=("TkDefaultFont", 12, "bold")).pack(side=tk.LEFT)
    ttk.Label(
        header,
        text="Local TESS vetting for Raspberry Pi OS  ·  offline  ·  1 GB RAM budget",
        foreground="grey",
    ).pack(side=tk.LEFT, padx=10)

    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
    notebook.add(TransitTab(notebook, bus), text="Transit")
    notebook.add(MicrolensTab(notebook, bus), text="Microlensing")

    root.mainloop()


if __name__ == "__main__":
    run_gui()
