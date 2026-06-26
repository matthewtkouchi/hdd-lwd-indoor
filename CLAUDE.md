# Project context for Claude Code

This repo is an SDR data-acquisition + visualization tool built on **GNU Radio
3.10.12.0**. It does two jobs:

1. **Live acquisition + dashboard** (`main.py`) — runs a GNU Radio flowgraph that
   talks to two Ethernet-connected SDRs, and shows a real-time PyQt5 dashboard
   (FFT, phase, amplitude/phase bars, rolling strip-chart, CSV recorder).
2. **Post-processing** (`lwd_plotter_tab.py`) — a CSV plotter/compute tool,
   embedded as a tab inside the dashboard, for analyzing the amplitude/phase
   logs the dashboard records.

If a `CLAUDE.md`-style briefing and the live code ever disagree, **the code wins** —
read the files before making changes.

---

## ⛔ The one rule that matters most: do NOT migrate to PyQt6

`main.py` uses **PyQt5**, and it must stay that way. This is not a style choice.

- The flowgraph uses GNU Radio's `gnuradio.qtgui` sinks (`qtgui.freq_sink_c`)
  and bridges them into the GUI with
  `sip.wrapinstance(self.qtgui_freq_sink_x_0.qwidget(), Qt.QWidget)`.
- On GNU Radio 3.10.x, **gr-qtgui is built against Qt5 and is not compatible
  with Qt6.** A Qt5 C++ widget cannot be wrapped into a PyQt6 `QWidget`.
- Therefore moving the GUI to PyQt6 breaks the flowgraph **everywhere GR 3.10 is
  installed**, not just on some machines. It is not a per-environment issue.

Do not "modernize" imports to PyQt6, do not suggest PySide6, and do not mix Qt
bindings in one process. If a task seems to require Qt6, stop and flag it instead
of doing it. (Qt6 support is only landing in GNU Radio's *development* branch,
which this project is not on.)

The original standalone plotter (`lwd_plotter.py`, if present in the repo) was
written in **PyQt6**. It has been superseded by `lwd_plotter_tab.py`, which is
the **PyQt5 port** and the only version that should be imported by `main.py`.
Do not re-import the PyQt6 file.

---

## File map

### `main.py` — GNU Radio top block + PyQt5 dashboard
- Class `trx_ssb(gr.top_block, Qt.QWidget)` is the flowgraph + main window.
- Imports Qt via GNU Radio's aggregator style: `from PyQt5 import Qt`, then
  `Qt.QApplication`, `Qt.QWidget`, `Qt.QTabWidget`, `Qt.QTimer`, etc. Keep this
  convention in `main.py`.
- SDR I/O is via `osmosdr` source/sink blocks. **osmosdr has no Qt dependency** —
  the SDR hardware does not care about the Qt version. The only Qt5 constraint
  comes from `gr-qtgui`.
- Custom visualization panels compute everything in **numpy** from lock-free ring
  buffers fed by `ReaderThread`s: `FFTPanel`, `PhasePanel`, `EqualizerPanel`,
  `RollingPanel`. These do **not** use gr-qtgui. The only gr-qtgui widgets are
  the `qtgui_freq_sink_x_0` / `qtgui_freq_sink_x_1` blocks.
- `UnifiedDashboard` is the main dashboard widget (spectra + equalizer + rolling
  panel, plus a "GR CONTROLS" toggle for the GNU Radio controls window).

### `lwd_plotter_tab.py` — PyQt5 CSV plotter (embeddable)
- Public entry point: `LWDPlotterWidget(parent=None)` — a `QWidget` containing a
  `QTabWidget` with two sub-tabs that share one `DataModel`:
  - **Plots**: load CSV(s), pick a shared x-axis, add independent plot panels
    each with its own checkable column list, normalization, autoscale, crosshair
    readout, and PNG export.
  - **Compute**: domain-correct averaging and A−B subtraction of columns, plus a
    "Save computed CSV…" button.
- The Smith-chart tab from the original was intentionally **removed**. Do not add
  it back unless explicitly asked.
- Also has a standalone `main()` so the file can be run by itself
  (`python lwd_plotter_tab.py`) to test the plotter without GNU Radio.

---

## How the two files are wired together

`main.py` was modified in exactly two places:

1. A **guarded import** near the top:
   ```python
   try:
       from lwd_plotter_tab import LWDPlotterWidget
   except Exception as _lwd_exc:
       LWDPlotterWidget = None
       print("LWD plotter tab unavailable: " + str(_lwd_exc), file=sys.stderr)
   ```
2. The dashboard and plotter are wrapped in a **top-level `Qt.QTabWidget`** (in
   `trx_ssb.__init__`, where `self.dashboard` used to be added directly to
   `self.top_layout`):
   ```python
   self.main_tabs = Qt.QTabWidget()
   self.main_tabs.addTab(self.dashboard, "SDR DASHBOARD")
   if LWDPlotterWidget is not None:
       try:
           self.lwd_plotter = LWDPlotterWidget()
           self.main_tabs.addTab(self.lwd_plotter, "LWD PLOTTER")
       except Exception as _lwd_tab_exc:
           print("Could not build LWD plotter tab: " + str(_lwd_tab_exc), file=sys.stderr)
   self.top_layout.addWidget(self.main_tabs)
   ```

**Keep this guarded pattern.** A failure in the plotter must never prevent the
SDR dashboard from launching. `lwd_plotter_tab.py` must sit next to `main.py` so
the import resolves.

---

## Things to preserve / gotchas

- **`main.py` uses Windows CRLF (`\r\n`) line endings and is "double-spaced"**
  (a blank line between every code line). This is cosmetic and it parses fine.
  When editing, match the existing line endings; do not bulk-reformat or convert
  to LF unless explicitly asked, to keep diffs small and reviewable.
- **Don't touch the GNU Radio flowgraph logic** (block creation, `connect()`
  calls, sample rates, osmosdr device args, recording sinks) unless that's the
  explicit task. The dashboard and tab layer sit on top of it.
- **Domain-correct math in the Compute tab must stay correct.** Don't "simplify"
  these:
  - dB averaging converts to linear voltage, means, then back to dB — NOT a
    naive mean of dB values.
  - Phase averaging uses a circular (unit-vector) mean so ±180° wrap is handled.
  - Phase subtraction wraps the difference into (−180, 180].
- The SDR addresses are **link-local APIPA** addresses (e.g. `169.254.x.x`),
  consistent with a direct Ethernet connection.

---

## Testing limitations (important)

- The maintainer often **cannot run `main.py` locally** — GNU Radio + a working
  PyQt5 install + the actual SDRs are required. Do not assume you can execute it
  in CI or that "it ran for me" is possible here.
- A frequent install failure is **mixing a pip-installed PyQt5 with a conda /
  radioconda GNU Radio that already bundles its own PyQt5.** If diagnosing GUI
  import/DLL errors, suspect this first. Don't `pip install PyQt5` into a conda
  GR env.
- For changes you can't run, prefer: (a) `python -m py_compile` on edited files,
  (b) unit-testing the **pure** functions in `lwd_plotter_tab.py` (the data layer:
  `load_csv`, `average_arrays`, `subtract_arrays`, `xy_for`, `kind_of`,
  `DataModel`) which depend only on numpy/csv and need no Qt, and (c) keeping
  changes minimal and clearly explained so they can be reviewed by reading.

---

## Future direction (do NOT silently implement)

There has been discussion of a possible **browser-based version** (Flask backend
+ JS frontend). Key points if this comes up:

- A web app would mean **dropping PyQt entirely** (both 5 and 6) — the browser
  becomes the GUI. "PyQt6 + Flask" is not a coherent architecture; do not build
  that hybrid.
- The backend would run GNU Radio **headless** (no gr-qtgui sinks) and stream the
  existing numpy FFT/phase arrays to the browser over WebSocket.
- The CSV plotter half ports to web easily; the **live** dashboard is the hard
  part (streaming throughput, downsampling, latency).
- SDR connection detection in a web app must run **server-side** in Flask (the
  browser is sandboxed and cannot probe Ethernet devices); the server polls the
  SDR IPs / NIC status and reports to the UI.

Treat all of the above as a roadmap, not a standing instruction. Only work on it
when explicitly asked.

---

## Working agreement

- Make the **smallest change that solves the task**; don't refactor adjacent code
  unprompted.
- If a fix appears to require violating the PyQt5 rule or rewriting the
  flowgraph, **stop and ask** rather than proceeding.
- When you change `main.py`, preserve its import style (`Qt.<thing>`), CRLF
  endings, and the guarded-tab pattern.
- Explain non-obvious changes briefly in the PR/commit description, since the
  maintainer may not be able to run the result directly.
