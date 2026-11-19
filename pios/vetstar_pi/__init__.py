"""VetStar Pi — Raspberry Pi OS port of the Vetstar TESS vetting studio.

The science lives in the shared ``vetstar_core`` package, also used by the
vetstar site. This package is only the Tkinter GUI, the CLI and plots.
Pi users get both from the release bundle (see pios/fetch.sh).

Two features are intentionally missing versus the cloud studio:

* multi-sector orchestration — Pi RAM budget can't hold two BLS sweeps in
  parallel; run each sector separately.
* joint TESS + Gaia PSPL fit — needs the Gaia Alerts fetch pipeline
  running online; single-band single-lens fits still work.

Command-line entry: ``python -m vetstar_pi …`` (see :mod:`vetstar_pi.cli`).
GUI entry: :func:`vetstar_pi.gui.run_gui`.
"""
__version__ = "0.3.0"
