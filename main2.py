#!/usr/bin/env python3

# -*- coding: utf-8 -*-

 

#

# SPDX-License-Identifier: GPL-3.0

#

# GNU Radio Python Flow Graph

# Title: Trx Ssb

# GNU Radio version: 3.10.12.0

 

from PyQt5 import Qt

from gnuradio import qtgui

from PyQt5 import QtCore

from PyQt5.QtCore import QObject, pyqtSlot, QTimer

from PyQt5.QtWidgets import (

    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,

    QPushButton, QSplitter, QSizePolicy,

)

from PyQt5.QtGui import QFont

from gnuradio import analog

from gnuradio import blocks

from gnuradio import eng_notation

from gnuradio import filter

from gnuradio.filter import firdes

from gnuradio import gr

from gnuradio.fft import window

import sys

import signal

from argparse import ArgumentParser

from gnuradio.eng_arg import eng_float, intx

import osmosdr

import time

import sip

import threading

import trx_ssb_epy_module_0 as epy_module_0  # embedded python module

import numpy as np

import matplotlib

matplotlib.use("Qt5Agg")

from matplotlib.figure import Figure

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

import matplotlib.patches as mpatches

import matplotlib.image as _mpimg

import os

import pyqtgraph as pg

from typing import Callable, Tuple, List, Optional

import math

import csv

from collections import deque

# --- Embedded LWD plotter tab (PyQt5 port of lwd_plotter.py) ---
# Guarded so a problem in the plotter can never stop the SDR dashboard.
try:
    from lwd_plotter_tab import LWDPlotterWidget
except Exception as _lwd_exc:
    LWDPlotterWidget = None
    print("LWD plotter tab unavailable: " + str(_lwd_exc), file=sys.stderr)

 

print("RUNNING FILE:", __file__)

 

# ══════════════════════════════════════════════════════════════════════════════

#   Lock-Free Ring Buffer

# ══════════════════════════════════════════════════════════════════════════════

 

class RingBuffer:

    def __init__(self, size=4096, dtype=np.complex64):

        self.size   = size

        self.dtype  = dtype

        self.buffer = np.zeros(size, dtype=dtype)

        self.index  = 0

        self.lock   = threading.Lock()

 

    def push(self, samples):

        with self.lock:

            n = len(samples)

            if n >= self.size:

                self.buffer[:] = samples[-self.size:]

                self.index = 0

            else:

                end = self.index + n

                if end < self.size:

                    self.buffer[self.index:end] = samples

                else:

                    first = self.size - self.index

                    self.buffer[self.index:] = samples[:first]

                    self.buffer[:n - first] = samples[first:]

                self.index = (self.index + n) % self.size

 

    def read(self):

        with self.lock:

            idx = self.index

            return np.concatenate((self.buffer[idx:], self.buffer[:idx]))

 

# ══════════════════════════════════════════════════════════════════════════════

#   Reader Thread

# ══════════════════════════════════════════════════════════════════════════════

 

class ReaderThread(threading.Thread):

    def __init__(self, vector_sink, ringbuffer, chunk=4096):

        super().__init__(daemon=True)

        self.vector_sink = vector_sink

        self.ringbuffer  = ringbuffer

        self.chunk       = chunk

        self.running     = True

 

    def run(self):

        while self.running:

            try:

                raw = self.vector_sink.data()

                if len(raw) > 0:

                    data = np.array(raw, dtype=self.ringbuffer.dtype)

                    self.vector_sink.reset()

                    self.ringbuffer.push(data)

                else:

                    time.sleep(0.001)

            except Exception:

                time.sleep(0.001)

 

    def stop(self):

        self.running = False

 

# ══════════════════════════════════════════════════════════════════════════════

#   Dashboard palette & helpers

# ══════════════════════════════════════════════════════════════════════════════

 

_BG       = '#040f0e'

_PANEL    = '#071a17'

_BORDER   = '#0d3330'

_TEAL     = '#00ffcc'

_TEAL_DIM = '#005544'

_ORANGE   = '#ff7040'

_ORG_DIM  = '#5a2510'

_TEXT     = '#88ccbb'

_TEXT_DIM = '#2a6055'

 

_STYLESHEET = f"""

    QWidget {{

        background-color: {_BG};

        color: {_TEXT};

        font-family: 'Courier New';

    }}

    QFrame#panel {{

        background-color: {_PANEL};

        border: 1px solid {_BORDER};

    }}

    QLabel#heading {{

        color: {_TEAL};

        font-size: 9px;

        letter-spacing: 2px;

        font-family: 'Courier New';

    }}

    QLabel#value_teal {{

        color: {_TEAL};

        font-size: 13px;

        font-weight: bold;

        font-family: 'Courier New';

    }}

    QLabel#value_orange {{

        color: {_ORANGE};

        font-size: 13px;

        font-weight: bold;

        font-family: 'Courier New';

    }}

    QLabel#dist_big {{

        color: {_TEAL};

        font-size: 26px;

        font-weight: bold;

        font-family: 'Courier New';

    }}

    QPushButton {{

        background-color: {_TEAL_DIM};

        color: {_TEAL};

        border: 1px solid {_TEAL};

        padding: 3px 10px;

        font-family: 'Courier New';

        font-size: 9px;

        letter-spacing: 1px;

    }}

    QPushButton:hover {{

        background-color: {_TEAL};

        color: {_BG};

    }}

"""

 

def _heading(text: str, parent=None) -> QLabel:

    lbl = QLabel(text.upper(), parent)

    lbl.setObjectName('heading')

    lbl.setAlignment(Qt.Qt.AlignLeft | Qt.Qt.AlignVCenter)

    return lbl

 

def _auto_toggle(color: str, dim: str) -> 'QPushButton':

    """Small checkable AUTO button accented with the given plot color."""

    b = QPushButton('AUTO')

    b.setCheckable(True)

    b.setStyleSheet(f"""

        QPushButton {{

            background-color: {_BG};

            color: {color};

            border: 1px solid {color};

            padding: 2px 8px;

            font-family: 'Courier New';

            font-size: 9px;

            letter-spacing: 1px;

        }}

        QPushButton:hover           {{ background-color: {dim};   color: {color}; }}

        QPushButton:checked         {{ background-color: {color}; color: {_BG};   }}

        QPushButton:checked:hover   {{ background-color: {color}; color: {_BG};   }}

    """)

    return b

 

def _configure_pg_plot(pw: pg.PlotWidget, color: str):

    pw.setBackground(_BG)

    pw.getAxis('left').setTextPen(color)

    pw.getAxis('bottom').setTextPen(color)

    pw.getAxis('left').setPen(pg.mkPen(_BORDER))

    pw.getAxis('bottom').setPen(pg.mkPen(_BORDER))

    pw.showGrid(x=True, y=True, alpha=0.18)

 

def _splitter(orientation):

    sp = QSplitter(orientation)

    sp.setHandleWidth(4)

    sp.setStyleSheet(f'QSplitter::handle {{ background: {_BORDER}; }}')

    return sp

 

# ══════════════════════════════════════════════════════════════════════════════

#   FFTPanel  –  single-channel spectrum

# ══════════════════════════════════════════════════════════════════════════════

 

class FFTPanel(QWidget):

    def __init__(self, title, ringbuffer,

                 fft_size=4096, samp_rate=100_000,

                 trace_color=_TEAL, parent=None):

        super().__init__(parent)

        self._rb        = ringbuffer

        self._fft_size  = fft_size

        self._fc        = 0.0

        self._win       = np.hanning(fft_size).astype(np.float32)

        self._win_power = float(np.sum(self._win ** 2))

        self._bb_freqs  = np.fft.fftshift(

            np.fft.fftfreq(fft_size, 1.0 / samp_rate)

        )

 

        layout = QVBoxLayout(self)

        layout.setContentsMargins(6, 4, 6, 4)

        layout.setSpacing(2)

        layout.addWidget(_heading(title))

 

        pg.setConfigOptions(antialias=True, useOpenGL=False)

        self._pw = pg.PlotWidget()

        _configure_pg_plot(self._pw, trace_color)

        self._pw.setLabel('left',   'dB', **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw.setLabel('bottom', 'Hz', **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw.setYRange(-80, 20, padding=0)

        self._pw.setMinimumHeight(140)

        layout.addWidget(self._pw)

 

        self._curve = self._pw.plot(pen=pg.mkPen(color=trace_color, width=1.2))

 

        t = QTimer(self)

        t.setInterval(20)

        t.timeout.connect(self._update)

        t.start()

 

    def set_center_freq(self, fc: float):

        self._fc = fc

        rf = self._bb_freqs + fc

        self._pw.setXRange(rf[0], rf[-1], padding=0)

 

    def _update(self):

        data = self._rb.read()

        if len(data) != self._fft_size:

            return

        d_win   = data * self._win

        fft_out = np.fft.fftshift(np.fft.fft(d_win))

        mag     = 20.0 * np.log10(np.abs(fft_out) / np.sqrt(self._win_power) + 1e-12)

        self._curve.setData(self._bb_freqs + self._fc, mag)

 

# ══════════════════════════════════════════════════════════════════════════════

#   PhasePanel  –  time-domain phase plot

#

#   Reads the complex product  rx · conj(tx)  from a ring buffer fed by GR's

#   multiply_conjugate_cc, and plots its per-sample arg() (i.e. the phase

#   difference) over time.  Same calculation as the trx_ssb_phase script's

#   complex_to_arg → time_sink_f, just done in numpy at display time.

# ══════════════════════════════════════════════════════════════════════════════

 

class PhasePanel(QWidget):

    def __init__(self, title, ringbuffer_prod, samp_rate=100_000,

                 trace_color=_ORANGE, parent=None):

        super().__init__(parent)

        self._rb         = ringbuffer_prod

        self._samp_rate  = samp_rate

 

        layout = QVBoxLayout(self)

        layout.setContentsMargins(6, 4, 6, 4)

        layout.setSpacing(2)

        layout.addWidget(_heading(title))

 

        pg.setConfigOptions(antialias=True, useOpenGL=False)

        self._pw = pg.PlotWidget()

        _configure_pg_plot(self._pw, trace_color)

        self._pw.setLabel('left',   'rad', **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw.setLabel('bottom', 'ms',  **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw.setYRange(-np.pi, np.pi, padding=0.05)

        self._pw.setMinimumHeight(140)

        layout.addWidget(self._pw)

 

        self._curve = self._pw.plot(pen=pg.mkPen(color=trace_color, width=1.2))

 

        # Static time axis (ms) spanning the ring buffer.

        N = self._rb.size

        dt_ms = 1000.0 / float(samp_rate)

        self._t = np.arange(N, dtype=np.float32) * dt_ms

        self._pw.setXRange(float(self._t[0]), float(self._t[-1]), padding=0)

 

        t = QTimer(self)

        t.setInterval(20)

        t.timeout.connect(self._update)

        t.start()

 

    def _update(self):

        data = self._rb.read()

        if len(data) != len(self._t):

            return

        # Per-sample phase difference: arg(rx · conj(tx)).

        phase = np.angle(data)

        self._curve.setData(self._t, phase)

 

# ══════════════════════════════════════════════════════════════════════════════

#   EqualizerPanel  –  dual peak bars + distance gauge

# ══════════════════════════════════════════════════════════════════════════════

 

class EqualizerPanel(QWidget):

    # Emits (amplitude_dB, phase_deg) at ~10 Hz for the rolling plot + recorder.

    sample_ready = Qt.pyqtSignal(float, float)

 

    def __init__(self, ringbuffer1, ringbuffer_prod,

                 fft_size=4096, samp_rate=100_000,

                 parent=None):

        super().__init__(parent)

        self._rb1        = ringbuffer1

        self._rb_prod    = ringbuffer_prod   # complex stream: rx · conj(tx)

        self._fft_size   = fft_size

        self._win        = np.hanning(fft_size).astype(np.float32)

        self._win_power  = float(np.sum(self._win ** 2))

        self._bb_freqs   = np.fft.fftshift(

            np.fft.fftfreq(fft_size, 1.0 / samp_rate)

        )

        self._band       = [-30_000, 30_000]

        self._fc         = 0.0

        self._alpha      = 1.0           # EMA factor for amplitude only

        self._s1         = None           # smoothed amplitude (dB)

        self._ant1_amplitude_db = None    # peak amplitude (dB), antenna 1

        self._ant1_phase_deg    = None    # phase diff (deg), magnitude-weighted

 

        self._build_ui()

 

        # 50 Hz: live bars + label refresh

        t = QTimer(self)

        t.setInterval(20)

        t.timeout.connect(self._update)

        t.start()

 

        # ~10 Hz: decimated emit for the rolling plot + CSV recorder

        self._emit_timer = QTimer(self)

        self._emit_timer.setInterval(100)

        self._emit_timer.timeout.connect(self._emit_sample)

        self._emit_timer.start()

 

    def _build_ui(self):

            layout = QVBoxLayout(self)

            layout.setContentsMargins(6, 4, 6, 4)

            layout.setSpacing(4)

            layout.addWidget(_heading('Amplitude & Phase  /  ROI'))

 

            row = QHBoxLayout()

            self._lbl1 = QLabel('SIG 1:   ---   dB')

            self._lbl2 = QLabel('PHASE:   ---   deg')

            self._lbl1.setObjectName('value_teal')

            self._lbl2.setObjectName('value_orange')

            row.addWidget(self._lbl1)

            row.addStretch()

            row.addWidget(self._lbl2)

            layout.addLayout(row)

 

            pg.setConfigOptions(antialias=True, useOpenGL=False)

 

            plots_row = QHBoxLayout()

            plots_row.setSpacing(4)

 

            # ── Amplitude bar (SIG 1, dB) ─────────────────────────────────

            self._pw_amp = pg.PlotWidget()

            _configure_pg_plot(self._pw_amp, _TEAL)

            self._pw_amp.setLabel('left', 'dBFS', **{'color': _TEXT_DIM, 'font-size': '8pt'})

            self._pw_amp.setYRange(-50, -10, padding=0.05)

            self._pw_amp.setXRange(-0.6, 0.6, padding=0)

            self._pw_amp.getAxis('bottom').setTicks([[(0, 'SIG 1')]])

            self._pw_amp.setMinimumHeight(120)

            self._pw_amp.setMaximumHeight(200)

            plots_row.addWidget(self._pw_amp)

 

            # ── Phase bar (deg, bipolar around 0) ─────────────────────────

            self._pw_phase = pg.PlotWidget()

            _configure_pg_plot(self._pw_phase, _ORANGE)

            self._pw_phase.setLabel('left', 'deg', **{'color': _TEXT_DIM, 'font-size': '8pt'})

            self._pw_phase.setYRange(-180, 180, padding=0.05)

            self._pw_phase.setXRange(-0.6, 0.6, padding=0)

            self._pw_phase.getAxis('bottom').setTicks([[(0, 'PHASE')]])

            self._pw_phase.setMinimumHeight(120)

            self._pw_phase.setMaximumHeight(200)

            plots_row.addWidget(self._pw_phase)

 

            layout.addLayout(plots_row)

 

            self._bar1 = pg.BarGraphItem(x=[0], y0=-50, y1=-50, width=0.55,

                                         brush=pg.mkBrush(_TEAL_DIM),

                                         pen=pg.mkPen(_TEAL, width=1))

            self._bar2 = pg.BarGraphItem(x=[0], y0=0, y1=0, width=0.55,

                                         brush=pg.mkBrush(_ORG_DIM),

                                         pen=pg.mkPen(_ORANGE, width=1))

            self._pw_amp.addItem(self._bar1)

            self._pw_phase.addItem(self._bar2)

 

            btn = QPushButton('COPY VALUES')

            btn.clicked.connect(self._copy_values)

            layout.addWidget(btn)

 

    def set_center_freq(self, fc: float):

        self._fc = fc

 

    def set_band(self, band):

        self._band = band

 

    def _emit_sample(self):

        if self._ant1_amplitude_db is None or self._ant1_phase_deg is None:

            return

        self.sample_ready.emit(float(self._ant1_amplitude_db),

                               float(self._ant1_phase_deg))

 

    def _update(self):

        d1 = self._rb1.read()

        if len(d1) != self._fft_size:

            return

        d_prod = self._rb_prod.read()

        if len(d_prod) != self._fft_size:

            return

 

        # ── peak amplitude (dB) in ROI band, antenna 1 ────────────────────

        dw   = d1 * self._win

        fv   = np.fft.fftshift(np.fft.fft(dw))

        mag  = 20.0 * np.log10(np.abs(fv) / np.sqrt(self._win_power) + 1e-12)

        rf   = self._bb_freqs + self._fc

        lo   = self._fc + self._band[0]

        hi   = self._fc + self._band[1]

        mask = (rf >= lo) & (rf <= hi)

        if not np.any(mask):

            return

        peak_idx = int(np.argmax(mag[mask]))

        full_idx = np.where(mask)[0][peak_idx]

        amp_db   = float(mag[full_idx])

 

        # ── phase difference: angle of the complex mean of rx · conj(tx) ──

        # Magnitude-weighted circular mean — robust against ±π wrap and

        # against noisy low-amplitude samples (which contribute little to

        # the mean phasor).  This is the same calculation as in v10 /

        # trx_ssb_phase, just averaged over the ring buffer.

        mean_prod = np.mean(d_prod)

        phase_rad = float(np.angle(mean_prod))

        phase_deg = float(np.degrees(phase_rad))

 

        # ── amplitude smoothing (EMA); phase is already averaged ──────────

        if self._s1 is None:

            self._s1 = amp_db

        else:

            a = self._alpha

            self._s1 = a * amp_db + (1 - a) * self._s1

 

        # Stash raw-ish values for the COPY button (use smoothed amp,

        # unsmoothed phase since the complex-mean is itself a smoother).

        self._ant1_amplitude_db = self._s1

        self._ant1_phase_deg    = phase_deg

 

        p1 = self._s1

        p2 = phase_deg

 

        # ── update bars ───────────────────────────────────────────────────

        self._pw_amp.removeItem(self._bar1)

        self._pw_phase.removeItem(self._bar2)

        self._bar1 = pg.BarGraphItem(x=[0], y0=-50, y1=max(-50, min(-10, p1)),

                                     width=0.55,

                                     brush=pg.mkBrush(_TEAL_DIM),

                                     pen=pg.mkPen(_TEAL, width=1))

        # Bipolar phase bar centred at 0.

        p2_clip = max(-180.0, min(180.0, p2))

        bar_lo, bar_hi = (p2_clip, 0.0) if p2_clip < 0 else (0.0, p2_clip)

        self._bar2 = pg.BarGraphItem(x=[0], y0=bar_lo, y1=bar_hi,

                                     width=0.55,

                                     brush=pg.mkBrush(_ORG_DIM),

                                     pen=pg.mkPen(_ORANGE, width=1))

        self._pw_amp.addItem(self._bar1)

        self._pw_phase.addItem(self._bar2)

 

        self._lbl1.setText(f'SIG 1:  {p1:+.2f} dB')

        self._lbl2.setText(f'PHASE:  {p2:+.2f} deg')

 

    def _copy_values(self):

        if self._ant1_amplitude_db is None:

            text = 'Antenna 1 data not yet available.'

        else:

            text = (f'{self._ant1_amplitude_db:.4f} '

                    f'{self._ant1_phase_deg:.4f}')

        Qt.QApplication.clipboard().setText(text)

        print(f'[copy] {text}', flush=True)

 

# ══════════════════════════════════════════════════════════════════════════════

#   RollingPanel  –  live amp/phase strip-chart + START/STOP CSV recorder

#

#   Always-on rolling display of amplitude (dB, teal) and phase (deg, orange)

#   over a moving time window.  The two plots share a single time axis.

#   The START/STOP button gates whether each incoming sample is *also*

#   appended to a CSV (elapsed_s, amplitude_dB, phase_deg); the display

#   itself runs continuously regardless of recording state.

# ══════════════════════════════════════════════════════════════════════════════

 

class RollingPanel(QWidget):

    WINDOW_S    = 60.0               # rolling window length (seconds)

    AMP_FIXED   = (-50.0, -10.0)     # default amplitude y-range (dBFS)

    PHASE_FIXED = (-180.0, 180.0)    # default phase y-range (deg)

 

    def __init__(self, parent=None):

        super().__init__(parent)

        self._path_provider = None

 

        # rolling buffers (elapsed-since-first-sample, amp dB, phase deg)

        self._t0    = None

        self._t     = deque()

        self._amp   = deque()

        self._phase = deque()

 

        # recorder state

        self._rec_file   = None

        self._rec_writer = None

        self._rec_t0     = None

        self._rec_count  = 0

        self._rec_path   = None

 

        self._build_ui()

 

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):

        layout = QVBoxLayout(self)

        layout.setContentsMargins(6, 4, 6, 4)

        layout.setSpacing(4)

 

        hdr = QHBoxLayout()

        hdr.addWidget(_heading('Rolling  /  Amplitude & Phase vs Time'))

        hdr.addStretch()

        self._rec_btn = QPushButton('● START REC')

        self._rec_btn.clicked.connect(self._toggle_record)

        hdr.addWidget(self._rec_btn)

        self._rec_status = QLabel('idle')

        self._rec_status.setObjectName('heading')

        hdr.addWidget(self._rec_status)

        layout.addLayout(hdr)

 

        pg.setConfigOptions(antialias=True, useOpenGL=False)

 

        # amplitude (teal) — top, with its own AUTO toggle

        amp_row = QHBoxLayout()

        amp_cap = QLabel('AMPLITUDE')

        amp_cap.setObjectName('heading')

        amp_row.addWidget(amp_cap)

        amp_row.addStretch()

        self._amp_auto_btn = _auto_toggle(_TEAL, _TEAL_DIM)

        self._amp_auto_btn.toggled.connect(self._on_amp_auto)

        amp_row.addWidget(self._amp_auto_btn)

        layout.addLayout(amp_row)

 

        self._pw_amp = pg.PlotWidget()

        _configure_pg_plot(self._pw_amp, _TEAL)

        self._pw_amp.setLabel('left', 'dBFS', **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw_amp.setYRange(*self.AMP_FIXED, padding=0.05)

        self._pw_amp.setMinimumHeight(120)

        self._pw_amp.getAxis('bottom').setStyle(showValues=False)

        self._curve_amp = self._pw_amp.plot(pen=pg.mkPen(color=_TEAL, width=1.4))

        layout.addWidget(self._pw_amp)

 

        # phase (orange) — bottom, shares the time axis with amplitude

        phase_row = QHBoxLayout()

        phase_cap = QLabel('PHASE')

        phase_cap.setStyleSheet(

            f"color: {_ORANGE}; font-family: 'Courier New'; "

            f"font-size: 11px; font-weight: bold; letter-spacing: 2px;"

        )

        phase_row.addWidget(phase_cap)

        phase_row.addStretch()

        self._phase_auto_btn = _auto_toggle(_ORANGE, _ORG_DIM)

        self._phase_auto_btn.toggled.connect(self._on_phase_auto)

        phase_row.addWidget(self._phase_auto_btn)

        layout.addLayout(phase_row)

 

        self._pw_phase = pg.PlotWidget()

        _configure_pg_plot(self._pw_phase, _ORANGE)

        self._pw_phase.setLabel('left',   'deg', **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw_phase.setLabel('bottom', 's',   **{'color': _TEXT_DIM, 'font-size': '8pt'})

        self._pw_phase.setYRange(*self.PHASE_FIXED, padding=0.05)

        self._pw_phase.setMinimumHeight(120)

        self._curve_phase = self._pw_phase.plot(pen=pg.mkPen(color=_ORANGE, width=1.4))

        layout.addWidget(self._pw_phase)

 

        self._pw_phase.setXLink(self._pw_amp)   # one shared, scrolling time axis

        self._pw_amp.setXRange(0.0, self.WINDOW_S, padding=0)

 

    # ── Public API ──────────────────────────────────────────────────────────

    def set_path_provider(self, fn):

        """fn() -> str: returns a fresh output path each time recording starts."""

        self._path_provider = fn

 

    # ── Autoscale ─────────────────────────────────────────────────────────

    @staticmethod

    def _autoscale_y(plot, values, pad_frac=0.1, min_span=1.0):

        if values.size == 0:

            return

        lo, hi = float(values.min()), float(values.max())

        if hi - lo < min_span:               # don't collapse on a flat signal

            mid = 0.5 * (lo + hi)

            lo, hi = mid - min_span / 2.0, mid + min_span / 2.0

        pad = (hi - lo) * pad_frac

        plot.setYRange(lo - pad, hi + pad, padding=0)

 

    def _on_amp_auto(self, on):

        if not on:                           # restore the fixed frame

            self._pw_amp.setYRange(*self.AMP_FIXED, padding=0.05)

 

    def _on_phase_auto(self, on):

        if not on:

            self._pw_phase.setYRange(*self.PHASE_FIXED, padding=0.05)

 

    def add_sample(self, amp_db, phase_deg):

        """Slot for EqualizerPanel.sample_ready (~10 Hz)."""

        now = time.monotonic()

        if self._t0 is None:

            self._t0 = now

        t = now - self._t0

 

        self._t.append(t)

        self._amp.append(amp_db)

        self._phase.append(phase_deg)

 

        # drop points older than the window

        t_min = t - self.WINDOW_S

        while self._t and self._t[0] < t_min:

            self._t.popleft()

            self._amp.popleft()

            self._phase.popleft()

 

        tx      = np.fromiter(self._t,     dtype=float)

        amp_arr = np.fromiter(self._amp,   dtype=float)

        ph_arr  = np.fromiter(self._phase, dtype=float)

        self._curve_amp.setData(tx, amp_arr)

        self._curve_phase.setData(tx, ph_arr)

 

        # per-plot autoscale (only while that AUTO toggle is on)

        if self._amp_auto_btn.isChecked():

            self._autoscale_y(self._pw_amp, amp_arr, min_span=3.0)

        if self._phase_auto_btn.isChecked():

            self._autoscale_y(self._pw_phase, ph_arr, min_span=10.0)

 

        # scroll the (shared) x-axis

        if t < self.WINDOW_S:

            self._pw_amp.setXRange(0.0, self.WINDOW_S, padding=0)

        else:

            self._pw_amp.setXRange(t - self.WINDOW_S, t, padding=0)

 

        # append to CSV if recording

        if self._rec_writer is not None:

            elapsed = now - self._rec_t0

            try:

                self._rec_writer.writerow(

                    (f'{elapsed:.4f}', f'{amp_db:.4f}', f'{phase_deg:.4f}')

                )

                self._rec_file.flush()

                self._rec_count += 1

                self._rec_status.setText(f'REC {elapsed:6.1f}s  {self._rec_count} pts')

            except Exception as exc:

                print(f'[rec] write error: {exc}', flush=True)

 

    # ── Recording control ─────────────────────────────────────────────────

    def _toggle_record(self):

        if self._rec_writer is None:

            self._start_record()

        else:

            self._stop_record()

 

    def _start_record(self):

        if self._path_provider is None:

            self._rec_status.setText('no output path')

            return

        path = self._path_provider()

        try:

            d = os.path.dirname(path)

            if d:

                os.makedirs(d, exist_ok=True)

            self._rec_file = open(path, 'w', newline='')

        except Exception as exc:

            self._rec_status.setText('open failed')

            print(f'[rec] open error: {exc}', flush=True)

            self._rec_file = None

            return

        self._rec_writer = csv.writer(self._rec_file)

        self._rec_writer.writerow(('elapsed_s', 'amplitude_dB', 'phase_deg'))

        self._rec_t0    = time.monotonic()

        self._rec_count = 0

        self._rec_path  = path

        self._rec_btn.setText('■ STOP REC')

        self._rec_status.setText(f'REC → {os.path.basename(path)}')

        print(f'[rec] started: {path}', flush=True)

 

    def _stop_record(self):

        if self._rec_file is not None:

            try:

                self._rec_file.flush()

                self._rec_file.close()

            except Exception:

                pass

        n = self._rec_count

        self._rec_writer = None

        self._rec_file   = None

        self._rec_btn.setText('● START REC')

        self._rec_status.setText(f'saved {n} pts')

        print(f'[rec] stopped: {self._rec_path} ({n} pts)', flush=True)

 

# ══════════════════════════════════════════════════════════════════════════════

#   UnifiedDashboard

# ══════════════════════════════════════════════════════════════════════════════

 

class UnifiedDashboard(QWidget):

    def __init__(self, ringbuffer1, ringbuffer_prod,

                 fft_size=4096, samp_rate=100_000,

                 parent=None):

        super().__init__(parent)

        self.setStyleSheet(_STYLESHEET)

        self.setMinimumSize(900, 600)

 

        # ── outer layout: toggle button on top, content below ─────────────

        outer = QVBoxLayout(self)

        outer.setContentsMargins(0, 0, 0, 0)

        outer.setSpacing(0)

 

        self._gr_toggle_btn = QPushButton('⚙  GR CONTROLS')

        self._gr_toggle_btn.setFixedHeight(24)

        self._gr_toggle_btn.clicked.connect(self._toggle_gr_window)

        self._gr_win_ref = None

        outer.addWidget(self._gr_toggle_btn)

 

        # ── top row: FFT(sig 1) + Phase(sig 1) + equalizer side by side ──

        top_split = _splitter(Qt.Qt.Horizontal)

 

        spectra_split = _splitter(Qt.Qt.Horizontal)

        self._fft1 = FFTPanel('◈ FFT  SIGNAL 1', ringbuffer1,

                              fft_size, samp_rate, _TEAL)

        self._phase_panel = PhasePanel('◈ PHASE  rx · conj(tx)', ringbuffer_prod,

                                       samp_rate, _ORANGE)

        spectra_split.addWidget(self._fft1)

        spectra_split.addWidget(self._phase_panel)

        spectra_split.setSizes([1, 1])

 

        self._eq = EqualizerPanel(ringbuffer1, ringbuffer_prod,

                                  fft_size, samp_rate)

 

        top_split.addWidget(spectra_split)

        top_split.addWidget(self._eq)

        top_split.setSizes([2, 1])

 

        # ── bottom: rolling amplitude + phase vs time ─────────────────────

        self._rolling_panel = RollingPanel()

        self._eq.sample_ready.connect(self._rolling_panel.add_sample)

 

        # vertical splitter: top row | rolling time-series

        v_split = _splitter(Qt.Qt.Vertical)

        v_split.addWidget(top_split)

        v_split.addWidget(self._rolling_panel)

        v_split.setSizes([300, 520])

 

        outer.addWidget(v_split)

 

    # ── Public API ─────────────────────────────────────────────────────────

    def set_center_freq(self, fc: float):

        self._fft1.set_center_freq(fc)

        self._eq.set_center_freq(fc)

 

    def set_band(self, band):

        self._eq.set_band(band)

 

    def set_record_path_provider(self, fn):

        self._rolling_panel.set_path_provider(fn)

 

    def set_gr_window(self, win):

        self._gr_win_ref = win

 

    def _toggle_gr_window(self):

        if self._gr_win_ref is None:

            return

        if self._gr_win_ref.isVisible():

            self._gr_win_ref.hide()

        else:

            self._gr_win_ref.show()

            self._gr_win_ref.raise_()

 

# ══════════════════════════════════════════════════════════════════════════════

#   trx_ssb  –  main GNU Radio top block

# ══════════════════════════════════════════════════════════════════════════════

 

class trx_ssb(gr.top_block, Qt.QWidget):

 

    def __init__(self):

 

        ##################################################

        # Variables

        ##################################################

        self.note        = note        = 'RECORDING_NOTE'

        self.file_id     = file_id     = note + ".bin"

        self.file_base   = file_base   = r"C:\Users\Temp\Documents\Gophur\capture_data"

        self.tx_sdr_addr = tx_sdr_addr = "169.254.102.116"

        self.tx_fc       = tx_fc       = 800000

        self.tx_samp_rate= tx_samp_rate= 16000

        self.samp_rate   = samp_rate   = tx_samp_rate

        self.rx_samp_rate= rx_samp_rate= tx_samp_rate

        self.rem_file2   = rem_file2   = "remote2.bin"

        self.rem_file1   = rem_file1   = "remote1.bin"

        self.rec_button  = rec_button  = 0

        self.my_fc       = my_fc       = tx_fc

        self.loc_file    = loc_file    = file_base + "_local_" + file_id

        self.band        = band        = [-30000, 30000]

        self.addr_out2   = addr_out2   = "169.254.102.116:1002"

        self.addr_out1   = addr_out1   = "169.254.102.116:1001"

        self.addr_0      = addr_0      = "169.254.9.106"

        self.addr        = addr        = "169.254.9.106"

 

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

        # Matplotlib FFT (kept for _update_fft, not shown in main window)

        ##################################################

        self.fft_fs          = samp_rate

        self.fft_N           = 4096

        self.fft_dtype       = np.complex64

        self.fft_window      = np.hanning(self.fft_N)

        self.fft_window_power= np.sum(self.fft_window**2)

 

        self._fft_fig    = Figure(figsize=(6, 3))

        self._fft_ax     = self._fft_fig.add_subplot(111)

        self._fft_canvas = FigureCanvas(self._fft_fig)

        # (not added to top_layout – goes into GR controls window below)

 

        fft_freqs = np.fft.fftshift(np.fft.fftfreq(self.fft_N, 1.0 / self.fft_fs))

        self._fft_line, = self._fft_ax.plot(fft_freqs, np.zeros(self.fft_N))

        self._fft_ax.set_ylim(-80, 20)

        self._fft_ax.set_xlim(fft_freqs[0], fft_freqs[-1])

        self._fft_ax.set_xlabel("Frequency (Hz)")

        self._fft_ax.set_ylabel("Magnitude (dB)")

        self._fft_ax.set_title("Real-Time FFT (20 FPS)")

        self._fft_ax.grid(True)

        self._fft_canvas.draw()

 

        self._fft_filename = self.rem_file1

        try:

            if not os.path.exists(self._fft_filename):

                open(self._fft_filename, "wb").close()

            self._fft_file = open(self._fft_filename, "rb")

            self._fft_file.seek(0, os.SEEK_END)

        except Exception as e:

            print(f"FFT file open error: {e}", file=sys.stderr)

            self._fft_file = None

 

        self._fft_timer = Qt.QTimer(self)

        self._fft_timer.setInterval(50)

        self._fft_timer.timeout.connect(self._update_fft)

        self._fft_timer.start()

 

        ##################################################

        # GR Blocks

        ##################################################

 

        self._tx_fc_range = qtgui.Range(100000, 200000000, 1000, 3000000, 200)

        self._tx_fc_win   = qtgui.RangeWidget(

            self._tx_fc_range, self.set_tx_fc,

            "'tx_fc'", "counter_slider", int, QtCore.Qt.Horizontal

        )

 

        _rec_button_push_button = Qt.QPushButton('RECORD')

        self._rec_button_choices = {'Pressed': 1, 'Released': 0}

        _rec_button_push_button.pressed.connect(

            lambda: self.set_rec_button(self._rec_button_choices['Pressed']))

        _rec_button_push_button.released.connect(

            lambda: self.set_rec_button(self._rec_button_choices['Released']))

 

        self._my_fc_range = qtgui.Range(100000, 200000000, 1000, 3000000, 200)

        self._my_fc_win   = qtgui.RangeWidget(

            self._my_fc_range, self.set_my_fc,

            "'my_fc'", "counter_slider", int, QtCore.Qt.Horizontal

        )

 

        self.qtgui_freq_sink_x_1 = qtgui.freq_sink_c(

            1024, window.WIN_BLACKMAN_hARRIS, my_fc, samp_rate, "", 1, None

        )

        self.qtgui_freq_sink_x_1.set_update_time(0.10)

        self.qtgui_freq_sink_x_1.set_y_axis(-80, 10)

        self.qtgui_freq_sink_x_1.set_y_label('Relative Gain', 'dB')

        self.qtgui_freq_sink_x_1.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")

        self.qtgui_freq_sink_x_1.enable_autoscale(False)

        self.qtgui_freq_sink_x_1.enable_grid(False)

        self.qtgui_freq_sink_x_1.set_fft_average(1.0)

        self.qtgui_freq_sink_x_1.enable_axis_labels(True)

        self.qtgui_freq_sink_x_1.enable_control_panel(False)

        self.qtgui_freq_sink_x_1.set_fft_window_normalized(False)

        for i in range(1):

            self.qtgui_freq_sink_x_1.set_line_label(i, "Data {0}".format(i))

            self.qtgui_freq_sink_x_1.set_line_width(i, 1)

            self.qtgui_freq_sink_x_1.set_line_color(i, "blue")

            self.qtgui_freq_sink_x_1.set_line_alpha(i, 1.0)

        self._qtgui_freq_sink_x_1_win = sip.wrapinstance(

            self.qtgui_freq_sink_x_1.qwidget(), Qt.QWidget

        )

 

        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(

            1024, window.WIN_HAMMING, my_fc, samp_rate,

            'QT GUI Remote Rx Plot', 1, None

        )

        self.qtgui_freq_sink_x_0.set_update_time(0.10)

        self.qtgui_freq_sink_x_0.set_y_axis(-80, 10)

        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')

        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")

        self.qtgui_freq_sink_x_0.enable_autoscale(False)

        self.qtgui_freq_sink_x_0.enable_grid(False)

        self.qtgui_freq_sink_x_0.set_fft_average(1.0)

        self.qtgui_freq_sink_x_0.enable_axis_labels(True)

        self.qtgui_freq_sink_x_0.enable_control_panel(False)

        self.qtgui_freq_sink_x_0.set_fft_window_normalized(False)

        for i in range(1):

            self.qtgui_freq_sink_x_0.set_line_label(i, "Data {0}".format(i))

            self.qtgui_freq_sink_x_0.set_line_width(i, 1)

            self.qtgui_freq_sink_x_0.set_line_color(i, "blue")

            self.qtgui_freq_sink_x_0.set_line_alpha(i, 1.0)

        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(

            self.qtgui_freq_sink_x_0.qwidget(), Qt.QWidget

        )

 

        self.osmosdr_source_1 = osmosdr.source(

            args="numchan=1 redpitaya=" + addr + ":1002"

        )

        self.osmosdr_source_1.set_sample_rate(samp_rate)

        self.osmosdr_source_1.set_center_freq(my_fc, 0)

        self.osmosdr_source_1.set_freq_corr(0, 0)

        self.osmosdr_source_1.set_dc_offset_mode(0, 0)

        self.osmosdr_source_1.set_iq_balance_mode(0, 0)

        self.osmosdr_source_1.set_gain_mode(False, 0)

        self.osmosdr_source_1.set_gain(1, 0)

        self.osmosdr_source_1.set_if_gain(1, 0)

        self.osmosdr_source_1.set_bb_gain(1, 0)

        self.osmosdr_source_1.set_antenna('', 0)

        self.osmosdr_source_1.set_bandwidth(tx_samp_rate, 0)

 

        self.osmosdr_source_0 = osmosdr.source(

            args="numchan=1 redpitaya=" + addr + ":1001"

        )

        self.osmosdr_source_0.set_sample_rate(rx_samp_rate)

        self.osmosdr_source_0.set_center_freq(my_fc, 0)

        self.osmosdr_source_0.set_freq_corr(0, 0)

        self.osmosdr_source_0.set_dc_offset_mode(0, 0)

        self.osmosdr_source_0.set_iq_balance_mode(0, 0)

        self.osmosdr_source_0.set_gain_mode(False, 0)

        self.osmosdr_source_0.set_gain(1, 0)

        self.osmosdr_source_0.set_if_gain(1, 0)

        self.osmosdr_source_0.set_bb_gain(1, 0)

        self.osmosdr_source_0.set_antenna('', 0)

        self.osmosdr_source_0.set_bandwidth(tx_samp_rate, 0)

        self.osmosdr_source_0.set_block_alias("16b Pitaya")

 

        self.osmosdr_sink_0_0 = osmosdr.sink(

            args="numchan=1 redpitaya=" + addr_out1

        )

        self.osmosdr_sink_0_0.set_clock_source('gpsdo', 0)

        self.osmosdr_sink_0_0.set_time_unknown_pps(osmosdr.time_spec_t())

        self.osmosdr_sink_0_0.set_sample_rate(tx_samp_rate)

        self.osmosdr_sink_0_0.set_center_freq(tx_fc, 0)

        self.osmosdr_sink_0_0.set_freq_corr(0, 0)

        self.osmosdr_sink_0_0.set_gain(1, 0)

        self.osmosdr_sink_0_0.set_if_gain(1, 0)

        self.osmosdr_sink_0_0.set_bb_gain(1, 0)

        self.osmosdr_sink_0_0.set_antenna('', 0)

        self.osmosdr_sink_0_0.set_bandwidth(tx_samp_rate, 0)

 

        self._note_tool_bar = Qt.QToolBar(self)

        self._note_tool_bar.addWidget(

            Qt.QLabel("RECORDING NOTE (press enter to update): ")

        )

        self._note_line_edit = Qt.QLineEdit(str(self.note))

        self._note_tool_bar.addWidget(self._note_line_edit)

        self._note_line_edit.editingFinished.connect(

            lambda: self.set_note(str(self._note_line_edit.text()))

        )

 

        self.fft_filter_xxx_0_0_0 = filter.fft_filter_ccc(

            1, firdes.low_pass(1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING), 1

        )

        self.fft_filter_xxx_0_0_0.declare_sample_delay(0)

        self.fft_filter_xxx_0_0 = filter.fft_filter_ccc(

            1, firdes.low_pass(1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING), 1

        )

        self.fft_filter_xxx_0_0.declare_sample_delay(0)

        self.fft_filter_xxx_0 = filter.fft_filter_ccc(

            1, firdes.low_pass(1, rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING), 1

        )

        self.fft_filter_xxx_0.declare_sample_delay(0)

 

        self.blocks_file_sink_0_0 = blocks.file_sink(

            gr.sizeof_gr_complex * 1, rem_file2, False

        )

        self.blocks_file_sink_0_0.set_unbuffered(False)

        self.blocks_file_sink_0 = blocks.file_sink(

            gr.sizeof_gr_complex * 1, rem_file1, False

        )

        self.blocks_file_sink_0.set_unbuffered(False)

 

        self._band_options  = [[-30000, 30000], [-30000, 30000]]

        self._band_labels   = ['lsb', 'usb']

        self._band_tool_bar = Qt.QToolBar(self)

        self._band_tool_bar.addWidget(Qt.QLabel("'band': "))

        self._band_combo_box = Qt.QComboBox()

        self._band_tool_bar.addWidget(self._band_combo_box)

        for _label in self._band_labels:

            self._band_combo_box.addItem(_label)

        self._band_callback = lambda i: Qt.QMetaObject.invokeMethod(

            self._band_combo_box, "setCurrentIndex",

            Qt.Q_ARG("int", self._band_options.index(i))

        )

        self._band_callback(self.band)

        self._band_combo_box.currentIndexChanged.connect(

            lambda i: self.set_band(self._band_options[i])

        )

 

        self.analog_sig_source_x_0_0 = analog.sig_source_c(

            samp_rate, analog.GR_COS_WAVE, samp_rate, 1, 0, 0

        )

 

        ##################################################

        # Ring buffers + reader threads

        ##################################################

        self.fft_size = 4096

        # Sig-1 baseband (complex) for the FFT panel and amplitude bar

        self.sink1    = blocks.vector_sink_c()

        self.rb1      = RingBuffer(size=self.fft_size, dtype=np.complex64)

        self.reader1  = ReaderThread(self.sink1, self.rb1, chunk=self.fft_size)

 

        # ── Phase chain (method from trx_ssb_phase) ───────────────────────

        # multiply_conjugate_cc computes  rx · conj(tx)  — a complex stream

        # whose argument is the phase difference.  Store the complex product

        # directly so the dashboard can do a magnitude-weighted circular

        # mean (np.angle(np.mean(...))) for a noise-robust phase readout;

        # the per-sample arg() is computed in the PhasePanel for plotting.

        self.blocks_multiply_conjugate_cc_0 = blocks.multiply_conjugate_cc(1)

        self.sink_prod   = blocks.vector_sink_c()

        self.rb_prod     = RingBuffer(size=self.fft_size, dtype=np.complex64)

        self.reader_prod = ReaderThread(self.sink_prod, self.rb_prod,

                                        chunk=self.fft_size)

 

        ##################################################

        # GR Signal Connections

        ##################################################

        self.connect((self.analog_sig_source_x_0_0, 0), (self.fft_filter_xxx_0_0_0, 0))

        self.connect((self.fft_filter_xxx_0,     0), (self.blocks_file_sink_0,    0))

        self.connect((self.fft_filter_xxx_0,     0), (self.qtgui_freq_sink_x_0,   0))

        self.connect((self.fft_filter_xxx_0_0,   0), (self.blocks_file_sink_0_0,  0))

        self.connect((self.fft_filter_xxx_0_0,   0), (self.qtgui_freq_sink_x_1,   0))

        self.connect((self.fft_filter_xxx_0_0_0, 0), (self.osmosdr_sink_0_0,      0))

        self.connect((self.osmosdr_source_0,     0), (self.fft_filter_xxx_0,      0))

        self.connect((self.osmosdr_source_1,     0), (self.fft_filter_xxx_0_0,    0))

        self.connect((self.fft_filter_xxx_0,     0), (self.sink1,                 0))

 

        # Phase: rx · conj(tx)  — store complex product, angle is taken

        # in the dashboard (see PhasePanel / EqualizerPanel).

        # input 0 = rx, input 1 = tx-ref  (multiply_conjugate = in0 * conj(in1))

        self.connect((self.fft_filter_xxx_0,     0),

                     (self.blocks_multiply_conjugate_cc_0, 0))

        self.connect((self.fft_filter_xxx_0_0_0, 0),

                     (self.blocks_multiply_conjugate_cc_0, 1))

        self.connect((self.blocks_multiply_conjugate_cc_0, 0),

                     (self.sink_prod, 0))

 

        ##################################################

        # GR Controls — floating background window

        ##################################################

        self._gr_window = Qt.QWidget()

        self._gr_window.setWindowTitle("GR Controls")

        self._gr_window.setMinimumSize(700, 500)

        gr_outer  = Qt.QVBoxLayout(self._gr_window)

        gr_grid   = Qt.QGridLayout()

        gr_outer.addLayout(gr_grid)

 

        # frequency sliders

        gr_grid.addWidget(self._my_fc_win,  0, 0, 1, 1)

        gr_grid.addWidget(self._tx_fc_win,  0, 1, 1, 1)

        # GR freq sink displays

        gr_grid.addWidget(self._qtgui_freq_sink_x_0_win, 1, 0, 1, 1)

        gr_grid.addWidget(self._qtgui_freq_sink_x_1_win, 1, 1, 1, 1)

        # band toolbar

        gr_grid.addWidget(self._band_tool_bar, 2, 0, 1, 1)

        # record button + note toolbar

        gr_outer.addWidget(_rec_button_push_button)

        gr_outer.addWidget(self._note_tool_bar)

        # matplotlib FFT

        gr_outer.addWidget(self._fft_canvas)

 

        self._gr_window.resize(800, 600)

        self._gr_window.show()   # visible on launch; minimize/move as needed

 

        ##################################################

        # Unified Dashboard (main window content)

        ##################################################

        self.dashboard = UnifiedDashboard(

            ringbuffer1=self.rb1,

            ringbuffer_prod=self.rb_prod,

            fft_size=self.fft_size,

            samp_rate=self.samp_rate,

        )

        self.dashboard.set_center_freq(self.my_fc)

        self.dashboard.set_band(self.band)

        self.dashboard.set_gr_window(self._gr_window)

        self.dashboard.set_record_path_provider(self._make_record_path)

        # Wrap the SDR dashboard and the LWD plotter in a top-level tab bar.
        self.main_tabs = Qt.QTabWidget()
        self.main_tabs.addTab(self.dashboard, "SDR DASHBOARD")
        if LWDPlotterWidget is not None:
            try:
                self.lwd_plotter = LWDPlotterWidget()
                self.main_tabs.addTab(self.lwd_plotter, "LWD PLOTTER")
            except Exception as _lwd_tab_exc:
                print("Could not build LWD plotter tab: " + str(_lwd_tab_exc), file=sys.stderr)
        self.top_layout.addWidget(self.main_tabs)

 

        # start reader threads

        self.reader1.start()

        self.reader_prod.start()

 

    # ── Window close ──────────────────────────────────────────────────────

    def closeEvent(self, event):

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "trx_ssb")

        self.settings.setValue("geometry", self.saveGeometry())

        self.reader1.stop()

        self.reader_prod.stop()

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

        Qt.QMetaObject.invokeMethod(

            self._note_line_edit, "setText",

            Qt.Q_ARG("QString", str(self.note))

        )

 

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

        self.osmosdr_sink_0_0.set_sample_rate(self.tx_samp_rate)

        self.osmosdr_sink_0_0.set_bandwidth(self.tx_samp_rate, 0)

        self.osmosdr_source_0.set_bandwidth(self.tx_samp_rate, 0)

        self.osmosdr_source_1.set_bandwidth(self.tx_samp_rate, 0)

 

    def get_tx_fc(self):

        return self.tx_fc

 

    def set_tx_fc(self, tx_fc):

        self.tx_fc = tx_fc

        self.osmosdr_sink_0_0.set_center_freq(self.tx_fc, 0)

 

    def get_samp_rate(self):

        return self.samp_rate

 

    def set_samp_rate(self, samp_rate):

        self.samp_rate = samp_rate

        self.analog_sig_source_x_0_0.set_sampling_freq(self.samp_rate)

        self.osmosdr_source_1.set_sample_rate(self.samp_rate)

        self.qtgui_freq_sink_x_1.set_frequency_range(self.my_fc, self.samp_rate)

 

    def get_rx_samp_rate(self):

        return self.rx_samp_rate

 

    def set_rx_samp_rate(self, rx_samp_rate):

        self.rx_samp_rate = rx_samp_rate

        self.fft_filter_xxx_0.set_taps(

            firdes.low_pass(1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING))

        self.fft_filter_xxx_0_0.set_taps(

            firdes.low_pass(1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING))

        self.fft_filter_xxx_0_0_0.set_taps(

            firdes.low_pass(1, self.rx_samp_rate, 1e3, 1e2, window.WIN_HAMMING))

        self.osmosdr_source_0.set_sample_rate(self.rx_samp_rate)

 

    def get_rem_file2(self):

        return self.rem_file2

 

    def set_rem_file2(self, rem_file2):

        self.rem_file2 = rem_file2

        self.blocks_file_sink_0_0.open(

            self.rem_file2 if self.rec_button == 1 else "NUL"

        )

 

    def get_rem_file1(self):

        return self.rem_file1

 

    def set_rem_file1(self, rem_file1):

        self.rem_file1 = rem_file1

        self.blocks_file_sink_0.open(

            self.rem_file1 if self.rec_button == 1 else "NUL"

        )

 

    def get_rec_button(self):

        return self.rec_button

 

    def set_rec_button(self, rec_button):

        self.rec_button = rec_button

        self.blocks_file_sink_0.open(

            self.rem_file1 if self.rec_button == 1 else "NUL"

        )

        self.blocks_file_sink_0_0.open(

            self.rem_file2 if self.rec_button == 1 else "NUL"

        )

 

    def get_my_fc(self):

        return self.my_fc

 

    def set_my_fc(self, my_fc):

        self.my_fc = my_fc

        self.osmosdr_source_0.set_center_freq(self.my_fc, 0)

        self.osmosdr_source_1.set_center_freq(self.my_fc, 0)

        self.qtgui_freq_sink_x_0.set_frequency_range(self.my_fc, self.samp_rate)

        self.qtgui_freq_sink_x_1.set_frequency_range(self.my_fc, self.samp_rate)

        self.dashboard.set_center_freq(self.my_fc)

 

    def get_loc_file(self):

        return self.loc_file

 

    def set_loc_file(self, loc_file):

        self.loc_file = loc_file

 

    def get_band(self):

        return self.band

 

    def set_band(self, band):

        self.band = band

        self._band_callback(self.band)

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

 

    def _update_fft(self):

        if self._fft_file is None:

            return

        bytes_needed = self.fft_N * self.fft_dtype().nbytes

        raw = self._fft_file.read(bytes_needed)

        if len(raw) < bytes_needed:

            return

        data      = np.frombuffer(raw, dtype=self.fft_dtype)

        data_win  = data * self.fft_window

        fft_vals  = np.fft.fftshift(np.fft.fft(data_win, self.fft_N))

        fft_mag   = 20 * np.log10(

            np.abs(fft_vals) / np.sqrt(self.fft_window_power) + 1e-12

        )

        self._fft_line.set_ydata(fft_mag)

        self._fft_canvas.draw_idle()

 

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