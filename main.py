#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Trx Ssb
# GNU Radio version: 3.10.12.0
#
# ──────────────────────────────────────────────────────────────────────────────
# Layout Modifcations from "Burrow_antenna_capture_v1_2.py"
#
#   config.py          - tunable params + JSON profile loading (settings.json)
#   settings_ribbon.py - profile manager strip (Load/Save/Apply & Restart)
#   ui_kit.py          - palette, stylesheet, widget helpers
#   streaming.py       - RingBuffer + ReaderThread
#   panels.py          - FFTPanel / PhasePanel / EqualizerPanel / RollingPanel
#   dashboard.py       - UnifiedDashboard
#   this file          - trx_ssb GNU Radio top block + main()
# ──────────────────────────────────────────────────────────────────────────────

# ── Import PyQT5 Modules ────────────────────────────────────────────────
from PyQt5 import Qt
from gnuradio import qtgui

# ── Import GNU Modules ────────────────────────────────────────────────
from gnuradio import analog
from gnuradio import blocks
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window

# ── Import Misc Modules ────────────────────────────────────────────────
import sys
import signal
import gc
import osmosdr
import time
import threading
import numpy as np
import os

# ── Import Custom Modules ────────────────────────────────────────────────
from scripts.config import AppConfig
from scripts.streaming import RingBuffer, ReaderThread
from scripts.dashboard import UnifiedDashboard
from scripts.settings_ribbon import SettingsRibbon

# ── Embedded LWD plotter tab (PyQt5 port of lwd_plotter.py) ───────────────────
# Guarded so a problem in the plotter can never stop the SDR dashboard.
try:
    from scripts.lwd_plotter_tab import LWDPlotterWidget
except Exception as _lwd_exc:
    LWDPlotterWidget = None
    print("LWD plotter tab unavailable: " + str(_lwd_exc), file=sys.stderr)

print("RUNNING FILE:", __file__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

# ══════════════════════════════════════════════════════════════════════════════
#   trx_ssb  -  main GNU Radio top block
# ══════════════════════════════════════════════════════════════════════════════

class trx_ssb(gr.top_block, Qt.QWidget):
    """GNU Radio top block + Qt main window for the BURROW capture app.

    Builds the TX/RX flow graph (Red Pitaya osmosdr source/sink, FIR
    filters, the rx*conj(tx) phase chain) and the Qt UI (settings ribbon,
    SDR dashboard, optional LWD plotter tab).

    Parameters come from the *working* profile in settings.json via
    ``AppConfig.load()``; the ribbon edits that file and re-execs to apply.
    """

    def __init__(self, config=None):

        ##################################################
        # Variables  (working profile from settings.json -- edit via the ribbon)
        ##################################################
        cfg = self.cfg = config if config is not None else AppConfig.load(SETTINGS_PATH)
        self.note        = note        = cfg.note
        self.file_id     = file_id     = note + ".bin"
        out_dir = os.path.join(BASE_DIR, cfg.out_dir)
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir     = out_dir
        self.file_base   = file_base   = os.path.join(out_dir, cfg.file_base)
        self.tx_fc       = tx_fc       = cfg.center_freq_hz
        self.tx_samp_rate= tx_samp_rate= cfg.samp_rate_hz
        self.samp_rate   = samp_rate   = cfg.samp_rate_hz
        self.rx_samp_rate= rx_samp_rate= cfg.samp_rate_hz
        self.rem_file2   = rem_file2   = os.path.join(out_dir, cfg.remote_file_2)
        self.rem_file1   = rem_file1   = os.path.join(out_dir, cfg.remote_file_1)
        self.rec_button  = rec_button  = 0
        self.my_fc       = my_fc       = cfg.center_freq_hz
        self.loc_file    = loc_file    = file_base + "_local_" + file_id
        self.band        = band        = list(cfg.band_hz)
        self.addr_out1   = addr_out1   = cfg.tx_port_1
        self.addr        = addr        = cfg.rx_addr

        gr.top_block.__init__(self, "Trx Ssb", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Trx Ssb")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)

        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "trx_ssb")
        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        self.flowgraph_started = threading.Event()

        ##################################################
        # Ring buffers  (persist across flowgraph rebuilds)
        ##################################################
        # The dashboard panels keep references to these, so they must be the
        # same objects for the life of the window; _rebuild_flowgraph()
        # resizes them in place rather than replacing them.
        self.fft_size = cfg.fft_size
        self.rb_lpf_rx_meas1 = RingBuffer(size=self.fft_size,
                                          dtype=np.complex64)
        self.rb_multiply_conjugate_rx_txconj = RingBuffer(size=self.fft_size,
                                                          dtype=np.complex64)

        self._build_flowgraph()

        ##################################################
        # Settings ribbon (profile manager + Apply) on the MAIN window
        ##################################################
        self._ribbon = SettingsRibbon(SETTINGS_PATH, self.apply_settings,
                                      self._apply_restart)
        self.top_layout.addWidget(self._ribbon)

        ##################################################
        # Unified Dashboard (main window content)
        ##################################################
        self.dashboard = UnifiedDashboard(
            rb_lpf_rx_meas1=self.rb_lpf_rx_meas1,
            rb_multiply_conjugate_rx_txconj=self.rb_multiply_conjugate_rx_txconj,
            fft_size=self.fft_size,
            samp_rate=self.samp_rate,
            ema_alpha=cfg.ema_alpha,
            refresh_ms=cfg.plot_refresh_ms,
            emit_ms=cfg.emit_interval_ms,
            rolling_window_s=cfg.rolling_window_s,
        )
        self.dashboard.set_center_freq(self.my_fc)
        self.dashboard.set_band(self.band)
        self.dashboard.set_record_path_provider(self._make_record_path)

        # Wrap the SDR dashboard and the LWD plotter in a top-level tab bar.
        self.main_tabs = Qt.QTabWidget()
        self.main_tabs.addTab(self.dashboard, "SDR DASHBOARD")
        if LWDPlotterWidget is not None:
            try:
                self.lwd_plotter = LWDPlotterWidget()
                self.main_tabs.addTab(self.lwd_plotter, "LWD PLOTTER")
            except Exception as _lwd_tab_exc:
                print("Could not build LWD plotter tab: " + str(_lwd_tab_exc),
                      file=sys.stderr)
        self.top_layout.addWidget(self.main_tabs)

        self._start_readers()

    # ── Flowgraph construction (re-runnable) ──────────────────────────────
    def _build_flowgraph(self):
        """Create and wire every GR block from the current settings.

        Called once from __init__ and again by _rebuild_flowgraph() when a
        setting changes that the running blocks cannot absorb.  The ring
        buffers are NOT created here — the panels hold references to them.
        """
        cfg           = self.cfg
        addr          = self.addr
        addr_out1     = self.addr_out1
        samp_rate     = self.samp_rate
        rx_samp_rate  = self.rx_samp_rate
        tx_samp_rate  = self.tx_samp_rate
        my_fc         = self.my_fc
        tx_fc         = self.tx_fc

        ##################################################
        # GR Blocks
        ##################################################
        # ── Receiver chains ───────────────────────────────────────────────
        # One chain per connected receiver, built by _build_rx_chain():
        #   sdr_rx_measN -> lpf_rx_measN -> rec_rx_measN
        # Chain 1 (port 1001) is the measurement channel and gets the extra
        # panel/phase taps below.  Chain 2 (port 1002) is record-only and is
        # built only when num_receivers == 2 — with nothing plugged into RX
        # IN2 it would otherwise stream, filter and record pure noise.
        # All LPFs share one tap list: the designs are identical.
        self.num_receivers = num_receivers = cfg.num_receivers
        self.rx_lpf_taps = rx_lpf_taps = firdes.low_pass(
            1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING
        )
        for idx in range(1, num_receivers + 1):
            self._build_rx_chain(idx, f"{addr}:{1000 + idx}", rx_lpf_taps,
                                 rx_samp_rate, my_fc, tx_samp_rate)

        self.sdr_tx = osmosdr.sink(
            args="numchan=1 redpitaya=" + addr_out1
        )
        self.sdr_tx.set_clock_source('gpsdo', 0)
        self.sdr_tx.set_time_unknown_pps(osmosdr.time_spec_t())
        self.sdr_tx.set_sample_rate(tx_samp_rate)
        self.sdr_tx.set_center_freq(tx_fc, 0)
        self.sdr_tx.set_freq_corr(0, 0)
        self.sdr_tx.set_gain(1, 0)
        self.sdr_tx.set_if_gain(1, 0)
        self.sdr_tx.set_bb_gain(1, 0)
        self.sdr_tx.set_antenna('', 0)
        self.sdr_tx.set_bandwidth(tx_samp_rate, 0)

        # TX reference LPF — same taps as the receiver LPFs (see above).
        self.lpf_tx_ref = filter.fft_filter_ccc(1, rx_lpf_taps, 1)
        self.lpf_tx_ref.declare_sample_delay(0)

        self.siggen_tx_tone = analog.sig_source_c(
            samp_rate, analog.GR_COS_WAVE, samp_rate, 1, 0, 0
        )

        ##################################################
        # Vector sinks + reader threads
        ##################################################
        # Sig-1 baseband (complex) for the FFT panel and amplitude bar
        self.sink_lpf_rx_meas1   = blocks.vector_sink_c()
        self.reader_lpf_rx_meas1 = ReaderThread(self.sink_lpf_rx_meas1,
                                                self.rb_lpf_rx_meas1,
                                                chunk=self.fft_size)

        # ── Phase chain (method from trx_ssb_phase) ───────────────────────
        # multiply_conjugate_cc computes  rx · conj(tx)  — a complex stream
        # whose argument is the phase difference.  Store the complex product
        # directly so the dashboard can do a magnitude-weighted circular
        # mean (np.angle(np.mean(...))) for a noise-robust phase readout;
        # the per-sample arg() is computed in the PhasePanel for plotting.
        self.multiply_conjugate_rx_txconj = blocks.multiply_conjugate_cc(1)
        self.sink_multiply_conjugate_rx_txconj   = blocks.vector_sink_c()
        self.reader_multiply_conjugate_rx_txconj = ReaderThread(
            self.sink_multiply_conjugate_rx_txconj,
            self.rb_multiply_conjugate_rx_txconj,
            chunk=self.fft_size)

        ##################################################
        # GR Signal Connections
        ##################################################
        # Each receiver's own  sdr_rx_measN -> lpf_rx_measN -> rec_rx_measN
        # was already connected by _build_rx_chain(); what remains is the TX
        # path and the chain-1-only panel/phase taps.
        self.connect((self.siggen_tx_tone,  0), (self.lpf_tx_ref,          0))
        self.connect((self.lpf_tx_ref,      0), (self.sdr_tx,              0))
        self.connect((self.lpf_rx_meas1,    0), (self.sink_lpf_rx_meas1,   0))

        # Phase: rx · conj(tx)  — store complex product, angle is taken
        # in the dashboard (see PhasePanel / EqualizerPanel).
        # in0 = lpf_rx_meas1 output (RX), in1 = lpf_tx_ref output (TX reference);
        # block computes in0 * conj(in1); swapping ports negates all phase.
        self.connect((self.lpf_rx_meas1,     0),
                     (self.multiply_conjugate_rx_txconj, 0))
        self.connect((self.lpf_tx_ref, 0),
                     (self.multiply_conjugate_rx_txconj, 1))
        self.connect((self.multiply_conjugate_rx_txconj, 0),
                     (self.sink_multiply_conjugate_rx_txconj, 0))

    def _start_readers(self):
        self.reader_lpf_rx_meas1.start()
        self.reader_multiply_conjugate_rx_txconj.start()

    # ── Receiver chain factory ────────────────────────────────────────────
    def _build_rx_chain(self, idx: int, addr_port: str, lpf_taps,
                        rx_samp_rate, my_fc, tx_samp_rate):
        """Build sdr_rx_measN -> lpf_rx_measN -> rec_rx_measN.

        Creates the three blocks as ``self.<name>_meas{idx}``, connects them,
        and returns them as a dict.  The osmosdr set_* sequence below is the
        configuration every receiver has always used; only the address/port
        differs per chain.

        Chain-1-only extras (the vector-sink panel tap and the feed into
        multiply_conjugate_rx_txconj in0) stay in __init__ — this factory
        builds the record path that every receiver shares.
        """
        is_primary = (idx == 1)

        sdr = osmosdr.source(args="numchan=1 redpitaya=" + addr_port)
        sdr.set_sample_rate(rx_samp_rate)
        sdr.set_center_freq(my_fc, 0)
        sdr.set_freq_corr(0, 0)
        sdr.set_dc_offset_mode(0, 0)
        sdr.set_iq_balance_mode(0, 0)
        sdr.set_gain_mode(False, 0)
        sdr.set_gain(1, 0)
        sdr.set_if_gain(1, 0)
        sdr.set_bb_gain(1, 0)
        sdr.set_antenna('', 0)
        sdr.set_bandwidth(tx_samp_rate, 0)
        if is_primary:
            sdr.set_block_alias("16b Pitaya")

        lpf = filter.fft_filter_ccc(1, lpf_taps, 1)
        lpf.declare_sample_delay(0)

        # Raw-IQ file sink: starts at the null device so nothing is written
        # until RECORD (see set_rec_button).
        rec = blocks.file_sink(gr.sizeof_gr_complex * 1, os.devnull, False)
        rec.set_unbuffered(False)

        setattr(self, f"sdr_rx_meas{idx}", sdr)
        setattr(self, f"lpf_rx_meas{idx}", lpf)
        setattr(self, f"rec_rx_meas{idx}", rec)

        self.connect((sdr, 0), (lpf, 0))
        self.connect((lpf, 0), (rec, 0))
        return {"sdr": sdr, "lpf": lpf, "rec": rec}

    # ── Applying settings without restarting the app ──────────────────────
    # Fields the running app can absorb in place: each one is either a pure
    # display value or maps onto a runtime setter on a live GR block.
    LIVE_KEYS = frozenset({
        "center_freq_hz",   # set_my_fc + set_tx_fc retune the radios live
        "note",             # only read when a recording starts
        "band_hz",          # ROI mask inside EqualizerPanel
        "ema_alpha",        # smoothing constant inside EqualizerPanel
        "plot_fps",         # panel QTimer intervals
        "emit_interval_ms", # rolling-chart / recorder cadence
        "rolling_window_s", # rolling-chart span (applied on the next sample)
        "restart_settle_s", # only read during teardown
        "file_base", "remote_file_1", "remote_file_2",
    })
    # Everything else (samp_rate_hz, fft_size, num_receivers, the addresses)
    # changes how the blocks themselves are constructed, so the flowgraph is
    # torn down and rebuilt — the Qt window and the panels survive.

    def apply_settings(self, profile: dict) -> str:
        """Apply a settings profile to the running app.

        Returns a short description of what was done, for the ribbon's
        status label.  Runs on the Qt main thread, so the panel timers
        cannot fire in the middle of a rebuild.
        """
        new = AppConfig.from_profile(profile)
        old = self.cfg
        changed = [k for k in AppConfig.profile_field_names()
                   if getattr(new, k) != getattr(old, k)]
        if not changed:
            return "no changes"

        needs_rebuild = [k for k in changed if k not in self.LIVE_KEYS]
        self.cfg = new
        self._apply_live(new)
        if needs_rebuild:
            self._rebuild_flowgraph()
            return "flowgraph rebuilt: " + ", ".join(needs_rebuild)
        return "applied live: " + ", ".join(changed)

    def _apply_live(self, cfg) -> None:
        """Push the in-place-changeable settings into the running objects."""
        self.set_note(cfg.note)
        self.set_band(list(cfg.band_hz))
        if self.my_fc != cfg.center_freq_hz:
            self.set_my_fc(cfg.center_freq_hz)   # RX radios + panel axes
            self.set_tx_fc(cfg.center_freq_hz)   # TX radio
        self.file_base = os.path.join(self.out_dir, cfg.file_base)
        # Only reopen a raw-IQ sink if its path really changed: file_sink.open()
        # restarts the file and would truncate an in-progress recording.
        rem1 = os.path.join(self.out_dir, cfg.remote_file_1)
        if rem1 != self.rem_file1:
            self.set_rem_file1(rem1)
        rem2 = os.path.join(self.out_dir, cfg.remote_file_2)
        if rem2 != self.rem_file2:
            self.set_rem_file2(rem2)
        self.dashboard.set_ema_alpha(cfg.ema_alpha)
        self.dashboard.set_refresh_ms(cfg.plot_refresh_ms)
        self.dashboard.set_emit_ms(cfg.emit_interval_ms)
        self.dashboard.set_rolling_window_s(cfg.rolling_window_s)

    def _rebuild_flowgraph(self) -> None:
        """Tear the flowgraph down and build it again, in place.

        The Qt window, the dashboard and the ring buffers all survive; only
        the GR blocks and the reader threads are replaced.  This is what
        makes a sample-rate or fft_size change possible without restarting
        the application.
        """
        cfg = self.cfg
        # Refresh the derived variables the builder reads.
        self.samp_rate = self.rx_samp_rate = self.tx_samp_rate = cfg.samp_rate_hz
        self.my_fc     = self.tx_fc = cfg.center_freq_hz
        self.fft_size  = cfg.fft_size
        self.addr      = cfg.rx_addr
        self.addr_out1 = cfg.tx_port_1

        self._release_hardware()
        # Drop the previous chain-2 blocks so num_receivers=1 really has
        # none left over from a 2-receiver run.
        for name in ("sdr_rx_meas2", "lpf_rx_meas2", "rec_rx_meas2"):
            if hasattr(self, name):
                delattr(self, name)

        # Resize in place: the panels hold references to these objects.
        self.rb_lpf_rx_meas1.resize(self.fft_size)
        self.rb_multiply_conjugate_rx_txconj.resize(self.fft_size)

        self._build_flowgraph()

        # Panels cache fft_size / samp_rate for their windows and axes.
        self.dashboard.set_fft_size(self.fft_size)
        self.dashboard.set_samp_rate(self.samp_rate)
        self.dashboard.set_center_freq(self.my_fc)

        self.start()
        self._start_readers()
        # Recording follows the rebuilt sinks (they reopened at os.devnull).
        if self.rec_button == 1:
            self.set_rec_button(1)

    # ── Release the Red Pitaya sockets ────────────────────────────────────
    def _release_hardware(self, settle_s=None):
        """Stop the flowgraph and destroy the osmosdr blocks.

        Stopping the scheduler is not enough to hang up on the Pitaya: the
        osmosdr blocks own the TCP sockets and stay alive as long as
        anything references them.  os.execv keeps open file descriptors,
        so a socket still owned by a live block survives into the restarted
        process while the Pitaya still counts it as the connected client —
        the fresh source then gets nothing and the scheduler reports
        "thread body wrapper error: receiving samples failed".

        Dropping every reference lets the C++ destructors close the
        sockets; ``settle_s`` gives the Pitaya time to accept the
        disconnect before we ask for a new one.
        """
        if settle_s is None:
            settle_s = self.cfg.restart_settle_s
        for name in ("reader_lpf_rx_meas1", "reader_multiply_conjugate_rx_txconj"):
            reader = getattr(self, name, None)
            if reader is not None:
                try:
                    reader.stop()
                    reader.join(timeout=1.0)   # don't destroy sinks mid-read
                except Exception:
                    pass
        try:
            self.stop(); self.wait()
        except Exception:
            pass
        try:
            self.disconnect_all()
        except Exception:
            pass
        # Blocks holding hardware sockets: drop them so ~source()/~sink() run.
        for name in ("sdr_rx_meas1", "sdr_rx_meas2", "sdr_tx"):
            if getattr(self, name, None) is not None:
                setattr(self, name, None)
        gc.collect()
        time.sleep(settle_s)

    # ── Apply & Restart: write happened in the ribbon; tear down + re-exec ──
    def _apply_restart(self):
        try:
            self.settings.setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        self._release_hardware()
        # Replace this process with a fresh one; it reloads settings.json.
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ── Window close ──────────────────────────────────────────────────────
    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "trx_ssb")
        self.settings.setValue("geometry", self.saveGeometry())
        self._release_hardware()
        event.accept()

    # ── Recording path for the amp/phase CSV log ──────────────────────────
    def _make_record_path(self):
        ts = time.strftime('%Y%m%d_%H%M%S')
        safe_note = ''.join(
            c if (c.isalnum() or c in '-_') else '_' for c in str(self.note)
        )
        return f'{self.file_base}_ampphase_{safe_note}_{ts}.csv'

    # ── Live setters ──────────────────────────────────────────────────────
    # These reconfigure running GR blocks, so they take effect without a
    # restart.  Nothing calls them yet — the settings ribbon still goes
    # through Apply & Restart — but they are the hooks a live-update
    # control would use.  The trivial GRC getters were removed; read the
    # attributes directly (tb.my_fc, tb.band, ...).

    def set_note(self, note):
        self.note = note
        self.set_file_id(self.note + ".bin")

    def set_file_id(self, file_id):
        self.file_id = file_id
        self.set_loc_file(self.file_base + "_local_" + self.file_id)

    def set_file_base(self, file_base):
        self.file_base = file_base
        self.set_loc_file(self.file_base + "_local_" + self.file_id)

    def set_loc_file(self, loc_file):
        self.loc_file = loc_file

    def set_tx_samp_rate(self, tx_samp_rate):
        self.tx_samp_rate = tx_samp_rate
        self.sdr_tx.set_sample_rate(self.tx_samp_rate)
        self.sdr_tx.set_bandwidth(self.tx_samp_rate, 0)
        self.sdr_rx_meas1.set_bandwidth(self.tx_samp_rate, 0)
        if getattr(self, 'sdr_rx_meas2', None) is not None:
            self.sdr_rx_meas2.set_bandwidth(self.tx_samp_rate, 0)

    def set_tx_fc(self, tx_fc):
        self.tx_fc = tx_fc
        self.sdr_tx.set_center_freq(self.tx_fc, 0)

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.siggen_tx_tone.set_sampling_freq(self.samp_rate)
        if getattr(self, 'sdr_rx_meas2', None) is not None:
            self.sdr_rx_meas2.set_sample_rate(self.samp_rate)

    def set_rx_samp_rate(self, rx_samp_rate):
        self.rx_samp_rate = rx_samp_rate
        # One design shared by every filter, as at construction time.
        self.rx_lpf_taps = firdes.low_pass(
            1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING)
        self.lpf_rx_meas1.set_taps(self.rx_lpf_taps)
        if getattr(self, 'lpf_rx_meas2', None) is not None:
            self.lpf_rx_meas2.set_taps(self.rx_lpf_taps)
        self.lpf_tx_ref.set_taps(self.rx_lpf_taps)
        self.sdr_rx_meas1.set_sample_rate(self.rx_samp_rate)

    def set_rem_file1(self, rem_file1):
        self.rem_file1 = rem_file1
        self.rec_rx_meas1.open(
            self.rem_file1 if self.rec_button == 1 else os.devnull
        )

    def set_rem_file2(self, rem_file2):
        self.rem_file2 = rem_file2
        if getattr(self, 'rec_rx_meas2', None) is not None:
            self.rec_rx_meas2.open(
                self.rem_file2 if self.rec_button == 1 else os.devnull
            )

    def set_rec_button(self, rec_button):
        self.rec_button = rec_button
        self.rec_rx_meas1.open(
            self.rem_file1 if self.rec_button == 1 else os.devnull
        )
        if getattr(self, 'rec_rx_meas2', None) is not None:
            self.rec_rx_meas2.open(
                self.rem_file2 if self.rec_button == 1 else os.devnull
            )

    def set_my_fc(self, my_fc):
        self.my_fc = my_fc
        self.sdr_rx_meas1.set_center_freq(self.my_fc, 0)
        if getattr(self, 'sdr_rx_meas2', None) is not None:
            self.sdr_rx_meas2.set_center_freq(self.my_fc, 0)
        self.dashboard.set_center_freq(self.my_fc)

    def set_band(self, band):
        self.band = band
        self.dashboard.set_band(self.band)


# ══════════════════════════════════════════════════════════════════════════════
#   Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main(top_block_cls=trx_ssb, options=None):
    qapp = Qt.QApplication(sys.argv)
    tb   = top_block_cls()
    tb.start()
    tb.flowgraph_started.set()
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb._release_hardware()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT,  sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()


if __name__ == '__main__':
    main()