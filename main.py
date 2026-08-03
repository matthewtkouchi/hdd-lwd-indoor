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
from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QSplitter, QSizePolicy,
)

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
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
import osmosdr
import time
import threading
import numpy as np
import os
import pyqtgraph as pg
from typing import Callable, Tuple, List, Optional
import csv

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
        self.tx_sdr_addr = tx_sdr_addr = cfg.tx_addr
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
        self.addr_out2   = addr_out2   = cfg.tx_port_2
        self.addr_out1   = addr_out1   = cfg.tx_port_1
        self.addr_0      = addr_0      = cfg.rx_addr
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
        # GR Blocks
        ##################################################
        self.sdr_rx_meas2 = osmosdr.source(
            args="numchan=1 redpitaya=" + addr + ":1002"
        )
        self.sdr_rx_meas2.set_sample_rate(samp_rate)
        self.sdr_rx_meas2.set_center_freq(my_fc, 0)
        self.sdr_rx_meas2.set_freq_corr(0, 0)
        self.sdr_rx_meas2.set_dc_offset_mode(0, 0)
        self.sdr_rx_meas2.set_iq_balance_mode(0, 0)
        self.sdr_rx_meas2.set_gain_mode(False, 0)
        self.sdr_rx_meas2.set_gain(1, 0)
        self.sdr_rx_meas2.set_if_gain(1, 0)
        self.sdr_rx_meas2.set_bb_gain(1, 0)
        self.sdr_rx_meas2.set_antenna('', 0)
        self.sdr_rx_meas2.set_bandwidth(tx_samp_rate, 0)

        self.sdr_rx_meas1 = osmosdr.source(
            args="numchan=1 redpitaya=" + addr + ":1001"
        )
        self.sdr_rx_meas1.set_sample_rate(rx_samp_rate)
        self.sdr_rx_meas1.set_center_freq(my_fc, 0)
        self.sdr_rx_meas1.set_freq_corr(0, 0)
        self.sdr_rx_meas1.set_dc_offset_mode(0, 0)
        self.sdr_rx_meas1.set_iq_balance_mode(0, 0)
        self.sdr_rx_meas1.set_gain_mode(False, 0)
        self.sdr_rx_meas1.set_gain(1, 0)
        self.sdr_rx_meas1.set_if_gain(1, 0)
        self.sdr_rx_meas1.set_bb_gain(1, 0)
        self.sdr_rx_meas1.set_antenna('', 0)
        self.sdr_rx_meas1.set_bandwidth(tx_samp_rate, 0)
        self.sdr_rx_meas1.set_block_alias("16b Pitaya")

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

        self.lpf_tx_ref = filter.fft_filter_ccc(
            1, firdes.low_pass(1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING), 1
        )
        self.lpf_tx_ref.declare_sample_delay(0)
        self.lpf_rx_meas2 = filter.fft_filter_ccc(
            1, firdes.low_pass(1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING), 1
        )
        self.lpf_rx_meas2.declare_sample_delay(0)
        self.lpf_rx_meas1 = filter.fft_filter_ccc(
            1, firdes.low_pass(1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING), 1
        )
        self.lpf_rx_meas1.declare_sample_delay(0)

        # ── Raw-IQ file sinks ─────────────────────────────────────────────
        # Start at the null device so nothing is written until RECORD.
        self.rec_rx_meas2 = blocks.file_sink(
            gr.sizeof_gr_complex * 1, os.devnull, False
        )
        self.rec_rx_meas2.set_unbuffered(False)
        self.rec_rx_meas1 = blocks.file_sink(
            gr.sizeof_gr_complex * 1, os.devnull, False
        )
        self.rec_rx_meas1.set_unbuffered(False)

        self.siggen_tx_tone = analog.sig_source_c(
            samp_rate, analog.GR_COS_WAVE, samp_rate, 1, 0, 0
        )

        ##################################################
        # Ring buffers + reader threads
        ##################################################
        self.fft_size = cfg.fft_size
        # Sig-1 baseband (complex) for the FFT panel and amplitude bar
        self.sink_lpf_rx_meas1   = blocks.vector_sink_c()
        self.rb_lpf_rx_meas1     = RingBuffer(size=self.fft_size, dtype=np.complex64)
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
        self.rb_multiply_conjugate_rx_txconj     = RingBuffer(size=self.fft_size, dtype=np.complex64)
        self.reader_multiply_conjugate_rx_txconj = ReaderThread(
            self.sink_multiply_conjugate_rx_txconj,
            self.rb_multiply_conjugate_rx_txconj,
            chunk=self.fft_size)

        ##################################################
        # GR Signal Connections
        ##################################################
        self.connect((self.siggen_tx_tone,  0), (self.lpf_tx_ref,          0))
        self.connect((self.lpf_rx_meas1,    0), (self.rec_rx_meas1,        0))
        self.connect((self.lpf_rx_meas2,    0), (self.rec_rx_meas2,        0))
        self.connect((self.lpf_tx_ref,      0), (self.sdr_tx,              0))
        self.connect((self.sdr_rx_meas1,    0), (self.lpf_rx_meas1,        0))
        self.connect((self.sdr_rx_meas2,    0), (self.lpf_rx_meas2,        0))
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

        ##################################################
        # Settings ribbon (profile manager + Apply & Restart) on the MAIN window
        ##################################################
        self._ribbon = SettingsRibbon(SETTINGS_PATH, self._apply_restart)
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

        # start reader threads
        self.reader_lpf_rx_meas1.start()
        self.reader_multiply_conjugate_rx_txconj.start()

    # ── Apply & Restart: write happened in the ribbon; tear down + re-exec ──
    def _apply_restart(self):
        try:
            self.settings.setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        try:
            self.reader_lpf_rx_meas1.stop(); self.reader_multiply_conjugate_rx_txconj.stop()
        except Exception:
            pass
        try:
            self.stop(); self.wait()
        except Exception:
            pass
        # Replace this process with a fresh one; it reloads settings.json.
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ── Window close ──────────────────────────────────────────────────────
    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "trx_ssb")
        self.settings.setValue("geometry", self.saveGeometry())
        self.reader_lpf_rx_meas1.stop()
        self.reader_multiply_conjugate_rx_txconj.stop()
        time.sleep(0.1)
        self.stop()
        self.wait()
        event.accept()

    # ── Recording path for the amp/phase CSV log ──────────────────────────
    def _make_record_path(self):
        ts = time.strftime('%Y%m%d_%H%M%S')
        safe_note = ''.join(
            c if (c.isalnum() or c in '-_') else '_' for c in str(self.note)
        )
        return f'{self.file_base}_ampphase_{safe_note}_{ts}.csv'

    # ── Getters / Setters ─────────────────────────────────────────────────
    def get_note(self):
        return self.note

    def set_note(self, note):
        self.note = note
        self.set_file_id(self.note + ".bin")

    def get_file_id(self):
        return self.file_id

    def set_file_id(self, file_id):
        self.file_id = file_id
        self.set_loc_file(self.file_base + "_local_" + self.file_id)

    def get_file_base(self):
        return self.file_base

    def set_file_base(self, file_base):
        self.file_base = file_base
        self.set_loc_file(self.file_base + "_local_" + self.file_id)

    def get_tx_sdr_addr(self):
        return self.tx_sdr_addr

    def set_tx_sdr_addr(self, tx_sdr_addr):
        self.tx_sdr_addr = tx_sdr_addr

    def get_tx_samp_rate(self):
        return self.tx_samp_rate

    def set_tx_samp_rate(self, tx_samp_rate):
        self.tx_samp_rate = tx_samp_rate
        self.sdr_tx.set_sample_rate(self.tx_samp_rate)
        self.sdr_tx.set_bandwidth(self.tx_samp_rate, 0)
        self.sdr_rx_meas1.set_bandwidth(self.tx_samp_rate, 0)
        self.sdr_rx_meas2.set_bandwidth(self.tx_samp_rate, 0)

    def get_tx_fc(self):
        return self.tx_fc

    def set_tx_fc(self, tx_fc):
        self.tx_fc = tx_fc
        self.sdr_tx.set_center_freq(self.tx_fc, 0)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.siggen_tx_tone.set_sampling_freq(self.samp_rate)
        self.sdr_rx_meas2.set_sample_rate(self.samp_rate)

    def get_rx_samp_rate(self):
        return self.rx_samp_rate

    def set_rx_samp_rate(self, rx_samp_rate):
        self.rx_samp_rate = rx_samp_rate
        self.lpf_rx_meas1.set_taps(
            firdes.low_pass(1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING))
        self.lpf_rx_meas2.set_taps(
            firdes.low_pass(1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING))
        self.lpf_tx_ref.set_taps(
            firdes.low_pass(1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING))
        self.sdr_rx_meas1.set_sample_rate(self.rx_samp_rate)

    def get_rem_file2(self):
        return self.rem_file2

    def set_rem_file2(self, rem_file2):
        self.rem_file2 = rem_file2
        self.rec_rx_meas2.open(
            self.rem_file2 if self.rec_button == 1 else os.devnull
        )

    def get_rem_file1(self):
        return self.rem_file1

    def set_rem_file1(self, rem_file1):
        self.rem_file1 = rem_file1
        self.rec_rx_meas1.open(
            self.rem_file1 if self.rec_button == 1 else os.devnull
        )

    def get_rec_button(self):
        return self.rec_button

    def set_rec_button(self, rec_button):
        self.rec_button = rec_button
        self.rec_rx_meas1.open(
            self.rem_file1 if self.rec_button == 1 else os.devnull
        )
        self.rec_rx_meas2.open(
            self.rem_file2 if self.rec_button == 1 else os.devnull
        )

    def get_my_fc(self):
        return self.my_fc

    def set_my_fc(self, my_fc):
        self.my_fc = my_fc
        self.sdr_rx_meas1.set_center_freq(self.my_fc, 0)
        self.sdr_rx_meas2.set_center_freq(self.my_fc, 0)
        self.dashboard.set_center_freq(self.my_fc)

    def get_loc_file(self):
        return self.loc_file

    def set_loc_file(self, loc_file):
        self.loc_file = loc_file

    def get_band(self):
        return self.band

    def set_band(self, band):
        self.band = band
        self.dashboard.set_band(self.band)

    def get_addr_out2(self):
        return self.addr_out2

    def set_addr_out2(self, addr_out2):
        self.addr_out2 = addr_out2

    def get_addr_out1(self):
        return self.addr_out1

    def set_addr_out1(self, addr_out1):
        self.addr_out1 = addr_out1

    def get_addr_0(self):
        return self.addr_0

    def set_addr_0(self, addr_0):
        self.addr_0 = addr_0

    def get_addr(self):
        return self.addr

    def set_addr(self, addr):
        self.addr = addr


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
        tb.stop()
        tb.wait()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT,  sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()


if __name__ == '__main__':
    main()