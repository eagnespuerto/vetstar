"""Tkinter GUI for VetStar Pi.

Two top-level tabs:

* **Transit** — file open or *Fetch from MAST* (TIC + sector), run the
  transit vetting pipeline, embedded LC / zoom plots, verdict pane, and
  one-click PNG / PDF / JSON export. PDF includes HCI, POE, TLCM,
  ExoMiner, DVT and FFI cutout (all recomputed server-side style).
* **Microlensing** — two sub-tabs:
    - **Classifier**: CSV/JSON light curve, drag/entry window, 3-way BIC
      fit, embedded plot, PDF export.
    - **Coverage**: CSV of events → tess-point sector overlap → per-event
      observability table + summary bar.

Pipelines run in background threads via :class:`_JobBus` so the GUI stays
responsive on a 1 GB Pi.  The MAST / ExoFOP / TESScut / Gaia-alert calls
fail soft and surface as warnings, keeping the local pipeline usable
offline.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib
matplotlib.use("TkAgg")  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from . import __version__


APP_TITLE = f"VetStar Pi v{__version__}"


class _JobBus:
    """Bridge worker-thread callables onto the Tk main loop."""

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


# --------------------------------------------------------------------------
# Transit tab
# --------------------------------------------------------------------------
class TransitTab(ttk.Frame):
    def __init__(self, master, bus: _JobBus):
        super().__init__(master)
        self.bus = bus
        self.parsed = None
        self.result = None
        self.extras = {}
        self._t_clean = None
        self._f_clean = None

        # ---- Top row: file loaders ----------------------------------------
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(top, text="Open FITS/JSON…", command=self.open_file).pack(side=tk.LEFT)
        ttk.Button(top, text="Fetch from MAST…", command=self.fetch_mast).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="List sectors…", command=self.list_sectors).pack(side=tk.LEFT, padx=6)
        self.file_lbl = ttk.Label(top, text="(no file)", foreground="grey")
        self.file_lbl.pack(side=tk.LEFT, padx=8)

        # ---- Detection sensitivity ---------------------------------------
        params = ttk.LabelFrame(self, text="Detection sensitivity")
        params.pack(fill=tk.X, padx=8, pady=4)
        self.thr_var = tk.StringVar(value="0.997")
        self.snr_var = tk.StringVar(value="4.0")
        self.sec_var = tk.StringVar(value="3.0")
        self.oe_var = tk.StringVar(value="3.0")
        self.high_var = tk.BooleanVar(value=False)
        self.rot_var = tk.StringVar(value="")
        self.known_var = tk.StringVar(value="")
        entries = [
            ("Threshold", self.thr_var, 7),
            ("Min SNR", self.snr_var, 6),
            ("Secondary σ", self.sec_var, 6),
            ("Odd/even σ", self.oe_var, 6),
            ("Rotation P (d)", self.rot_var, 8),
            ("Known P (d)", self.known_var, 8),
        ]
        for i, (label, var, w) in enumerate(entries):
            ttk.Label(params, text=label).grid(row=i // 3, column=2 * (i % 3),
                                               padx=(6, 2), pady=3, sticky="e")
            ttk.Entry(params, textvariable=var, width=w).grid(
                row=i // 3, column=2 * (i % 3) + 1, padx=(0, 6), sticky="w"
            )
        ttk.Checkbutton(params, text="High-variability detrend",
                        variable=self.high_var).grid(row=2, column=0, columnspan=3,
                                                     padx=6, pady=2, sticky="w")

        # ---- Extras toggles ----------------------------------------------
        extras = ttk.LabelFrame(self, text="PDF extras (fetched over the network — fail soft)")
        extras.pack(fill=tk.X, padx=8, pady=4)
        self.hci_var = tk.BooleanVar(value=True)
        self.exominer_var = tk.BooleanVar(value=True)
        self.ffi_var = tk.BooleanVar(value=True)
        self.dvt_var = tk.BooleanVar(value=True)
        for i, (label, var) in enumerate([
            ("HCI + POE + TLCM (ExoFOP + Gaia/SIMBAD/NEA)", self.hci_var),
            ("ExoMiner feature views", self.exominer_var),
            ("TESScut FFI cutout", self.ffi_var),
            ("SPOC DVT phase-fold + fitted geometry", self.dvt_var),
        ]):
            ttk.Checkbutton(extras, text=label, variable=var).grid(
                row=i // 2, column=i % 2, padx=6, pady=2, sticky="w"
            )

        # ---- Actions ------------------------------------------------------
        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=8, pady=4)
        self.run_btn = ttk.Button(actions, text="Run vetting", command=self.run,
                                  state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT)
        self.png_btn = ttk.Button(actions, text="Save plot PNG…", command=self.save_png,
                                  state=tk.DISABLED)
        self.png_btn.pack(side=tk.LEFT, padx=6)
        self.pdf_btn = ttk.Button(actions, text="Save PDF report…", command=self.save_pdf,
                                  state=tk.DISABLED)
        self.pdf_btn.pack(side=tk.LEFT, padx=6)
        self.json_btn = ttk.Button(actions, text="Save JSON…", command=self.save_json,
                                   state=tk.DISABLED)
        self.json_btn.pack(side=tk.LEFT, padx=6)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, padx=8, pady=(2, 0))
        self.stage_lbl = ttk.Label(self, text="", foreground="grey")
        self.stage_lbl.pack(fill=tk.X, padx=8, pady=(0, 4))

        # ---- Body: plot + text panes -------------------------------------
        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        plot_frame = ttk.Frame(body)
        body.add(plot_frame, weight=3)
        self.fig = Figure(figsize=(6, 5), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(body)
        body.add(text_frame, weight=2)
        self.text = tk.Text(text_frame, width=44, height=28, wrap="word",
                            font=("TkFixedFont", 9))
        self.text.pack(fill=tk.BOTH, expand=True)
        self._set_text(
            "Open a TESS/Kepler SPOC FITS file, JSON light curve, or click "
            "'Fetch from MAST…' to download by TIC + sector. Then run vetting."
        )

    def _set_text(self, msg):
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, msg)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open light curve",
            filetypes=[("Light curve", "*.fits *.fits.gz *.json"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self._load_from(path)

    def _load_from(self, path):
        try:
            from .parsers import parse_upload
            self.parsed = parse_upload(path, os.path.basename(path))
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        if self.parsed.get("metadata_only"):
            messagebox.showerror("Metadata-only",
                                 "ExoFOP metadata-only JSON — no time series. "
                                 "Upload a FITS light curve instead.")
            return
        star = self.parsed["star"]
        self.file_lbl.config(text=os.path.basename(path), foreground="black")
        self.run_btn.config(state=tk.NORMAL)
        self.result = None
        for b in (self.png_btn, self.pdf_btn, self.json_btn):
            b.config(state=tk.DISABLED)
        self._set_text(
            f"Loaded {os.path.basename(path)}\n"
            f"  cadences: {len(self.parsed['t'])}\n"
            f"  TIC: {star.tic_id}\n"
            f"  sector: {star.sector}\n"
            f"  Tmag: {star.tmag}\n"
            "Ready to run."
        )

    def fetch_mast(self):
        tic = simpledialog.askinteger("Fetch from MAST", "TIC ID:", parent=self)
        if tic is None:
            return
        sec = simpledialog.askinteger("Fetch from MAST", "Sector:", parent=self)
        if sec is None:
            return
        self._set_text(f"Downloading SPOC LC for TIC {tic} sector {sec}…")

        def worker():
            try:
                from .mast_fetch import fetch_spoc_lightcurve
                info = fetch_spoc_lightcurve(tic, sec)
                path = info.get("path")
                if not path or not os.path.exists(path):
                    raise RuntimeError("FITS file not found after fetch")
                self.bus.submit(lambda: self._load_from(path))
            except Exception as exc:
                self.bus.submit(lambda e=exc: messagebox.showerror("MAST fetch failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def list_sectors(self):
        tic = simpledialog.askinteger("List sectors", "TIC ID:", parent=self)
        if tic is None:
            return

        def worker():
            try:
                from .mast_fetch import list_available_sectors
                sectors = list_available_sectors(tic)
                lines = [f"TIC {tic}: {len(sectors)} sector(s)"]
                for s in sectors:
                    lines.append(f"  S{s['sector']:03d}  cam={s.get('camera')} "
                                 f"ccd={s.get('ccd')}  author={s.get('author')}  "
                                 f"exp={s.get('exptime')}s")
                self.bus.submit(lambda: self._set_text("\n".join(lines)))
            except Exception as exc:
                self.bus.submit(lambda e=exc: messagebox.showerror("MAST sector list failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _reporter(self):
        """Return a stage→percent callback that pumps updates to the Tk loop."""
        def rep(stage: str, pct: float):
            def apply():
                self.progress["value"] = 100.0 * pct
                self.stage_lbl.config(text=f"{stage}  {pct * 100:.0f}%")
            self.bus.submit(apply)
        return rep

    def run(self):
        if self.parsed is None:
            return
        try:
            thr = float(self.thr_var.get())
            snr = float(self.snr_var.get())
            sec = float(self.sec_var.get())
            oe = float(self.oe_var.get())
            rot = float(self.rot_var.get()) if self.rot_var.get().strip() else None
            known = float(self.known_var.get()) if self.known_var.get().strip() else None
        except ValueError:
            messagebox.showerror("Bad input", "Sensitivity fields must be numeric.")
            return

        self.run_btn.config(state=tk.DISABLED)
        self.progress["value"] = 0
        self._set_text("Running pipeline… (BLS / Lomb-Scargle / events / verdict)")

        def worker():
            try:
                from .pipeline import clean_lightcurve, run_full_vetting
                result = run_full_vetting(
                    t=self.parsed["t"], flux=self.parsed["flux"],
                    flux_err=self.parsed["flux_err"], quality=self.parsed["quality"],
                    mom_x=self.parsed["mom_x"], mom_y=self.parsed["mom_y"],
                    star=self.parsed["star"],
                    detect_threshold=thr, detect_min_snr=snr,
                    high_variability=self.high_var.get(),
                    rotation_period_days=rot,
                    known_period_days=known,
                    secondary_sigma=sec, odd_even_sigma=oe,
                    reporter=self._reporter(),
                )
                t_c, f_c, _ = clean_lightcurve(
                    self.parsed["t"], self.parsed["flux"],
                    self.parsed["flux_err"], self.parsed["quality"],
                )
                extras = self._build_extras(result)
                self.bus.submit(lambda: self._on_done(result, t_c, f_c, extras))
            except Exception as exc:
                self.bus.submit(lambda e=exc: self._on_err(e))

        threading.Thread(target=worker, daemon=True).start()

    def _build_extras(self, result) -> dict:
        """Best-effort HCI / ExoMiner / FFI / DVT for the PDF."""
        import logging
        log = logging.getLogger("vetstar-pi.extras")
        extras = {}
        tic = result.star.tic_id
        period = (result.bls or {}).get("period")

        if self.dvt_var.get() and tic and result.star.sector is not None:
            try:
                from .dvt_fetch import fetch_dvt
                extras["dvt"] = fetch_dvt(tic, result.star.sector)
            except Exception as e:
                log.warning("DVT fetch failed: %s", e)

        if self.hci_var.get() and tic:
            try:
                from .cli import _hci_bundle  # reuse the helper
                extras["hci_bundle"] = _hci_bundle(result, extras.get("dvt"))
            except Exception as e:
                log.warning("HCI failed: %s", e)

        if self.exominer_var.get() and tic and period:
            try:
                from .exominer import run_exominer
                from .pipeline import clean_lightcurve
                t_c, f_c, _ = clean_lightcurve(
                    self.parsed["t"], self.parsed["flux"],
                    self.parsed["flux_err"], self.parsed["quality"],
                )
                t0 = (result.bls or {}).get("t0") or 0.0
                dur = (result.bls or {}).get("duration") or 0.0
                extras["exominer"] = run_exominer(
                    t=t_c, f=f_c,
                    mom_x=self.parsed.get("mom_x"), mom_y=self.parsed.get("mom_y"),
                    period=period, t0=t0, duration=dur,
                    crowdsap=getattr(result.star, "crowdsap", None),
                )
            except Exception as e:
                log.warning("ExoMiner failed: %s", e)

        if self.ffi_var.get() and tic and result.star.ra is not None:
            try:
                from .ffi_cutout import make_ffi_cutout
                extras["ffi_cutout"] = make_ffi_cutout(
                    ra=result.star.ra, dec=result.star.dec,
                    sector=result.star.sector, tic_id=tic, size_px=15,
                )
            except Exception as e:
                log.warning("FFI cutout failed: %s", e)

        return extras

    def _on_done(self, result, t, f, extras):
        self.run_btn.config(state=tk.NORMAL)
        self.progress["value"] = 100
        self.stage_lbl.config(text="done.")
        self.result = result
        self.extras = extras
        self._t_clean = t
        self._f_clean = f

        from .plots import build_transit_overview
        fig = build_transit_overview(result, t, f)
        self.canvas.figure = fig
        self.fig = fig
        self.canvas.draw()

        v = result.verdict
        lines = [
            f"Verdict: {v.get('headline', '—')}",
            f"Category: {v.get('category', '—')}   confidence: {v.get('confidence', 0):.2f}",
            "",
            f"BLS period : {result.bls.get('period', 0):.6f} d",
            f"BLS depth  : {result.bls.get('depth', 0):.5f}",
            f"BLS SDE    : {result.bls.get('sde', 0):.2f}",
            f"LS  period : {result.lomb_scargle.get('top_period', 0):.6f} d",
            f"Events     : {len(result.events)}",
            "",
        ]
        ph = result.physics
        if ph.get("available"):
            lines.append(f"R_companion: {ph['R_companion_Rjup']:.2f} R_Jup ({ph['category']})")
        oe = result.odd_even
        if oe.get("available"):
            lines.append(f"Odd/even   : Δ={oe['difference']:.5f}  σ={oe['sigma']:.2f}  "
                         f"flag_eb={oe['flag_eb']}")
        sec = result.secondary
        if sec.get("available"):
            lines.append(f"Secondary  : σ={sec['sigma']:.2f}  detected={sec['detected']}")
        cen = result.centroid
        if cen.get("available"):
            lines.append(f"Centroid   : Δcol={cen['shift_col_sigma']:.2f}σ  "
                         f"Δrow={cen['shift_row_sigma']:.2f}σ  on_target={cen['on_target']}")
        # Known-object cross-match (Gaia + SIMBAD + NEA)
        ko = getattr(result, "known_object", None)
        if ko and ko.get("available") and ko.get("matched"):
            lines.append("")
            lines.append(f"Known object: {ko.get('headline')}  ({ko.get('name')})")
            if ko.get("distance_arcsec") is not None:
                lines.append(f"  match distance: {ko['distance_arcsec']:.2f}\"")
        # HCI headline
        hci_bundle = (self.extras or {}).get("hci_bundle") or {}
        hci = hci_bundle.get("hci") or {}
        if hci.get("score") is not None:
            lines.append("")
            lines.append(f"HCI: {hci['score']:.1f}/100  tier={hci.get('tier', '—')}")
        lines.append("")
        lines.append("Reasons:")
        for r in v.get("reasons", []):
            lines.append(" • " + r)

        self._set_text("\n".join(lines))
        for b in (self.png_btn, self.pdf_btn, self.json_btn):
            b.config(state=tk.NORMAL)

    def _on_err(self, exc):
        self.run_btn.config(state=tk.NORMAL)
        self.progress["value"] = 0
        self.stage_lbl.config(text="")
        messagebox.showerror("Pipeline error", str(exc))
        self._set_text(f"Error: {exc}")

    def save_png(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile=self._basename("overview.png"),
        )
        if not path:
            return
        self.fig.savefig(path, dpi=140, bbox_inches="tight")

    def save_pdf(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile=self._basename("report.pdf"),
        )
        if not path:
            return

        def worker():
            try:
                from .report import build_pdf
                pdf = build_pdf(
                    self.result,
                    hci_bundle=(self.extras or {}).get("hci_bundle"),
                    exominer=(self.extras or {}).get("exominer"),
                    ffi_cutout=(self.extras or {}).get("ffi_cutout"),
                    dvt=(self.extras or {}).get("dvt"),
                )
                with open(path, "wb") as fh:
                    fh.write(pdf)
                self.bus.submit(lambda: messagebox.showinfo("Saved", f"Wrote {path}"))
            except Exception as exc:
                self.bus.submit(lambda e=exc: messagebox.showerror("PDF failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def save_json(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile=self._basename("result.json"),
        )
        if not path:
            return
        d = self.result.to_dict()
        d.pop("plots", None)
        for key in ("bls", "lomb_scargle"):
            if isinstance(d.get(key), dict):
                d[key].pop("_periodogram", None)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, default=str)

    def _basename(self, suffix):
        tic = self.result.star.tic_id if self.result else None
        return f"TIC{tic}_{suffix}" if tic else f"vetstar_pi_{suffix}"


# --------------------------------------------------------------------------
# Microlensing classifier sub-tab
# --------------------------------------------------------------------------
class MicrolensClassifierTab(ttk.Frame):
    def __init__(self, master, bus: _JobBus):
        super().__init__(master)
        self.bus = bus
        self.lc_t = None
        self.lc_f = None
        self.lc_fe = None
        self.result = None

        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(top, text="Open CSV/JSON…", command=self.open_file).pack(side=tk.LEFT)
        ttk.Button(top, text="Fetch Gaia Alert…", command=self.fetch_alert).pack(side=tk.LEFT, padx=6)
        self.file_lbl = ttk.Label(top, text="(no file)", foreground="grey")
        self.file_lbl.pack(side=tk.LEFT, padx=8)

        win = ttk.LabelFrame(self, text="Fit window")
        win.pack(fill=tk.X, padx=8, pady=4)
        self.t_start_var = tk.StringVar()
        self.t_end_var = tk.StringVar()
        self.t0_var = tk.StringVar()
        self.label_var = tk.StringVar()
        for i, (label, var, w) in enumerate([
            ("t_start", self.t_start_var, 12),
            ("t_end", self.t_end_var, 12),
            ("t0 guess", self.t0_var, 12),
            ("event label", self.label_var, 18),
        ]):
            ttk.Label(win, text=label).grid(row=0, column=2 * i, padx=(6, 2), pady=4)
            ttk.Entry(win, textvariable=var, width=w).grid(row=0, column=2 * i + 1, padx=(0, 6))

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=8, pady=4)
        self.run_btn = ttk.Button(actions, text="Fit PSPL / Flare / Null",
                                  command=self.run, state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT)
        self.png_btn = ttk.Button(actions, text="Save plot PNG…",
                                  command=self.save_png, state=tk.DISABLED)
        self.png_btn.pack(side=tk.LEFT, padx=6)
        self.pdf_btn = ttk.Button(actions, text="Save PDF report…",
                                  command=self.save_pdf, state=tk.DISABLED)
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
        self.text = tk.Text(text_frame, width=44, height=28, wrap="word",
                            font=("TkFixedFont", 9))
        self.text.pack(fill=tk.BOTH, expand=True)
        self._set_text(
            "Open a CSV (time,flux,flux_err) / JSON light curve or fetch a "
            "Gaia Alert G-band curve directly, then enter t_start / t_end "
            "around the excursion and click Fit."
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
            from .cli import _load_flat
            d = _load_flat(path)
            self.lc_t, self.lc_f, self.lc_fe = d["t"], d["flux"], d["flux_err"]
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.file_lbl.config(text=os.path.basename(path), foreground="black")
        self._prefill_and_show(os.path.basename(path))

    def fetch_alert(self):
        alert = simpledialog.askstring("Fetch Gaia Alert",
                                        "Alert id (e.g. Gaia23bra):", parent=self)
        if not alert:
            return
        self._set_text(f"Fetching Gaia Alert {alert}…")

        def worker():
            try:
                from .gaia_photometry import fetch_alert_lightcurve
                lc = fetch_alert_lightcurve(alert)
                import numpy as np
                # Convert G mag → relative flux F/F_ref = 10^(-0.4·(G - G_ref))
                mag = np.asarray(lc.mag, dtype=float)
                mag_err = np.asarray(lc.mag_err, dtype=float)
                ref = float(np.nanmedian(mag))
                f = np.power(10.0, -0.4 * (mag - ref))
                fe = f * 0.4 * np.log(10.0) * mag_err
                t = np.asarray(lc.time_jd, dtype=float) - 2_457_000.0  # → BTJD-ish
                self.lc_t, self.lc_f, self.lc_fe = t, f, fe
                self.bus.submit(lambda: (self.file_lbl.config(text=f"Gaia Alert {alert}"),
                                          self._prefill_and_show(f"Gaia Alert {alert}")))
            except Exception as exc:
                self.bus.submit(lambda e=exc: messagebox.showerror("Gaia Alert fetch failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _prefill_and_show(self, source_label):
        import numpy as np
        t = self.lc_t
        self.t_start_var.set(f"{float(np.min(t)):.4f}")
        self.t_end_var.set(f"{float(np.max(t)):.4f}")
        self.t0_var.set(f"{0.5 * (float(np.min(t)) + float(np.max(t))):.4f}")
        self.run_btn.config(state=tk.NORMAL)
        self.result = None
        for b in (self.png_btn, self.pdf_btn):
            b.config(state=tk.DISABLED)
        self._set_text(
            f"Loaded {source_label}\n"
            f"  points: {len(t)}\n"
            f"  time range: [{float(np.min(t)):.3f}, {float(np.max(t)):.3f}]\n"
            "Adjust the window and click Fit."
        )
        from .plots import build_raw_lc
        fig = build_raw_lc(self.lc_t, self.lc_f, self.lc_fe)
        self.canvas.figure = fig
        self.fig = fig
        self.canvas.draw()

    def run(self):
        if self.lc_t is None:
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
        import numpy as np
        fe = self.lc_fe
        if fe is None or not np.any(np.isfinite(fe)):
            fe = np.full_like(self.lc_f, float(np.nanstd(self.lc_f) or 1e-3))

        self.run_btn.config(state=tk.DISABLED)
        self.progress.start(60)
        self._set_text("Fitting PSPL / Davenport-2014 flare / null…")

        def worker():
            try:
                from .microlensing import analyze_event
                res = analyze_event(self.lc_t, self.lc_f, fe,
                                    t_start=t_start, t_end=t_end, t0_guess=t0)
                self.bus.submit(lambda: self._on_done(res))
            except Exception as exc:
                self.bus.submit(lambda e=exc: self._on_err(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, result):
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL)
        self.result = result
        from .plots import build_microlens_fit
        fig = build_microlens_fit(result)
        self.canvas.figure = fig
        self.fig = fig
        self.canvas.draw()

        pspl = result["models"]["pspl"]
        flare = result["models"]["flare"]
        null_ = result["models"]["null"]
        lines = [
            f"Verdict: {result['verdict'].upper()}   confidence={result.get('confidence', 0):.3f}",
            "",
            "BICs (lower = better):",
            f"  PSPL  = {pspl.get('bic'):.2f}",
            f"  Flare = {flare.get('bic'):.2f}",
            f"  Null  = {null_.get('bic'):.2f}",
            "",
            f"ΔBIC(null - PSPL)  = {result['delta_bic']['null_minus_pspl']:.2f}",
            f"ΔBIC(flare - PSPL) = {result['delta_bic']['flare_minus_pspl']:.2f}",
            f"Symmetry score = {result.get('symmetry_score', float('nan')):.3f}",
            "",
            "PSPL best fit:",
        ]
        for name in ("t0", "tE", "u0", "f_s", "f_b"):
            v = (pspl.get("params") or {}).get(name)
            e = (pspl.get("param_err") or {}).get(name)
            lines.append(f"  {name:<3} = {v:.5g}  ± {e:.3g}"
                         if v is not None and e is not None else f"  {name:<3} = —")
        obs = result.get("observables") or {}
        if obs:
            lines += [
                "",
                "Observables:",
                f"  A_max    = {obs.get('peak_magnification', float('nan')):.3f}",
                f"  Δm (mag) = {obs.get('peak_brightening_mag', float('nan')):.3f}",
                f"  tE (d)   = {obs.get('einstein_timescale_d', float('nan')):.3f}",
                f"  FWHM (d) = {obs.get('magnification_fwhm_d', float('nan')):.3f}",
            ]
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
            initialfile="microlens_fit.png",
        )
        if not path:
            return
        self.fig.savefig(path, dpi=140, bbox_inches="tight")

    def save_pdf(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile="microlens_report.pdf",
        )
        if not path:
            return

        def worker():
            try:
                from .microlensing_report import build_microlensing_pdf
                # Grab a base64 PNG of the current plot for the PDF.
                import io
                buf = io.BytesIO()
                self.fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                meta = {"event_id": self.label_var.get() or None}
                pdf = build_microlensing_pdf(self.result, metadata=meta,
                                              plot_png_b64=b64)
                with open(path, "wb") as fh:
                    fh.write(pdf)
                self.bus.submit(lambda: messagebox.showinfo("Saved", f"Wrote {path}"))
            except Exception as exc:
                self.bus.submit(lambda e=exc: messagebox.showerror("PDF failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()


# --------------------------------------------------------------------------
# Microlensing coverage sub-tab
# --------------------------------------------------------------------------
class MicrolensCoverageTab(ttk.Frame):
    def __init__(self, master, bus: _JobBus):
        super().__init__(master)
        self.bus = bus
        self.result = None

        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(top, text="Open events CSV…", command=self.open_csv).pack(side=tk.LEFT)
        ttk.Label(top, text=("Columns: event_id, ra, dec, t0, tE"),
                  foreground="grey").pack(side=tk.LEFT, padx=8)

        params = ttk.Frame(self)
        params.pack(fill=tk.X, padx=8, pady=4)
        self.margin_var = tk.StringVar(value="0.0")
        ttk.Label(params, text="Wing margin (×tE):").pack(side=tk.LEFT)
        ttk.Entry(params, textvariable=self.margin_var, width=6).pack(side=tk.LEFT, padx=(4, 12))
        self.save_btn = ttk.Button(params, text="Save JSON…",
                                    command=self.save_json, state=tk.DISABLED)
        self.save_btn.pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=8, pady=(2, 4))

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Table pane
        table_frame = ttk.Frame(body)
        body.add(table_frame, weight=3)
        cols = ("event_id", "ra", "dec", "t0", "tE", "sectors", "observable",
                "ecl_lat", "bulge_zone")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                                  height=18)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=90 if c not in ("sectors",) else 140,
                             anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Chart pane
        chart_frame = ttk.Frame(body)
        body.add(chart_frame, weight=2)
        self.fig = Figure(figsize=(5, 4), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def open_csv(self):
        path = filedialog.askopenfilename(
            title="Open events CSV", filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            margin = float(self.margin_var.get())
        except ValueError:
            margin = 0.0

        self.progress.start(60)

        def worker():
            try:
                from .microlensing_coverage import evaluate_catalog, parse_events_csv
                with open(path, "rb") as fh:
                    text = fh.read().decode("utf-8", errors="replace")
                events = parse_events_csv(text)
                out = evaluate_catalog(events, margin_te=margin)
                self.bus.submit(lambda: self._on_done(out))
            except Exception as exc:
                self.bus.submit(lambda e=exc: self._on_err(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, coverage):
        self.progress.stop()
        self.result = coverage
        self.tree.delete(*self.tree.get_children())
        for ev in coverage.get("events") or []:
            sectors = ",".join(str(s.get("sector")) for s in ev.get("sectors") or [])
            self.tree.insert("", "end", values=(
                ev.get("event_id"),
                f"{ev.get('ra'):.4f}" if ev.get("ra") is not None else "—",
                f"{ev.get('dec'):.4f}" if ev.get("dec") is not None else "—",
                ev.get("t0"), ev.get("tE"),
                sectors or "—",
                "YES" if ev.get("observable") else "no",
                f"{ev.get('ecliptic_lat_deg', 0):.1f}"
                if ev.get("ecliptic_lat_deg") is not None else "—",
                "YES" if ev.get("in_bulge_blind_zone") else "no",
            ))
        from .plots import build_coverage_summary
        fig = build_coverage_summary(coverage) or Figure(figsize=(5, 4), dpi=90)
        self.canvas.figure = fig
        self.fig = fig
        self.canvas.draw()
        self.save_btn.config(state=tk.NORMAL)

    def _on_err(self, exc):
        self.progress.stop()
        messagebox.showerror("Coverage error", str(exc))

    def save_json(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile="coverage.json",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.result, fh, indent=2, default=str)


# --------------------------------------------------------------------------
# Microlensing container: two sub-tabs
# --------------------------------------------------------------------------
class MicrolensTab(ttk.Frame):
    def __init__(self, master, bus: _JobBus):
        super().__init__(master)
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)
        nb.add(MicrolensClassifierTab(nb, bus), text="Classifier")
        nb.add(MicrolensCoverageTab(nb, bus), text="Coverage")


# --------------------------------------------------------------------------
# Root
# --------------------------------------------------------------------------
def run_gui():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1150x740")
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass

    bus = _JobBus(root)

    header = ttk.Frame(root, padding=(8, 6))
    header.pack(fill=tk.X)
    ttk.Label(header, text=APP_TITLE, font=("TkDefaultFont", 12, "bold")).pack(side=tk.LEFT)
    ttk.Label(
        header,
        text="TESS vetting for Raspberry Pi OS  ·  Transit + Microlensing  ·  MAST-aware",
        foreground="grey",
    ).pack(side=tk.LEFT, padx=10)

    nb = ttk.Notebook(root)
    nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
    nb.add(TransitTab(nb, bus), text="Transit")
    nb.add(MicrolensTab(nb, bus), text="Microlensing")

    root.mainloop()


if __name__ == "__main__":
    run_gui()
