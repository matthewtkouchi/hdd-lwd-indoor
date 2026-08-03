#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
panels.py
=========

The individual dashboard panels, each a self-contained QWidget that reads
from a RingBuffer and refreshes itself on a timer:

    FFTPanel        - live single-channel spectrum
    PhasePanel      - time-domain arg(rx*conj(tx)) plot
    EqualizerPanel  - ROI peak amplitude bar + phase bar + numeric lables
    RollingPanel    - rolling amp/phase + CSV recorder

Display/refresh tuning (ema_alpha, window length, refresh intervals) is
passed in via constructor arguments so it can be driven from config.py.
"""

import os
import csv
import time
from collections import deque

import numpy as np
import pyqtgraph as pg

from PyQt5 import Qt
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from .ui_kit import (
    _BG, _PANEL, _BORDER, _TEAL, _TEAL_DIM, _ORANGE, _ORG_DIM,
    _TEXT, _TEXT_DIM,
    _heading, _auto_toggle, _configure_pg_plot, _splitter,
)


class FFTPanel(QWidget):
    """Single-channel live spectrum (pyqtgraph).

    Reads complex baseband from a RingBuffer, windows + FFTs it each refresh,
    and plots magnitude in dB against RF frequency.
    """

    def __init__(self, title, ringbuffer,
                 fft_size=4096, samp_rate=100_000,
                 trace_color=_TEAL, refresh_ms=20, parent=None):
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
        t.setInterval(refresh_ms)
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


class PhasePanel(QWidget):
    """Time-domain plot of the per-sample phase difference arg(rx*conj(tx)).

    Reads the complex product stream from a RingBuffer fed by GR's
    multiply_conjugate_cc and plots its angle over the buffer's time span.
    """

    def __init__(self, title, ringbuffer_prod, samp_rate=100_000,
                 trace_color=_ORANGE, refresh_ms=20, parent=None):
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
        t.setInterval(refresh_ms)
        t.timeout.connect(self._update)
        t.start()

    def _update(self):
        data = self._rb.read()
        if len(data) != len(self._t):
            return
        # Per-sample phase difference: arg(rx · conj(tx)).
        phase = np.angle(data)
        self._curve.setData(self._t, phase)


class EqualizerPanel(QWidget):
    """ROI peak-amplitude bar + phase bar, with the live numeric readouts.

    Computes the peak amplitude (dB) inside the ROI band and the
    magnitude-weighted circular-mean phase difference, then drives the two
    bar graphs.  Emits (amplitude_dB, phase_deg) at ~``1000/emit_ms`` Hz for
    the rolling strip-chart and the CSV recorder.

    ``ema_alpha`` controls amplitude smoothing only (0 < a <= 1; lower is
    smoother).  Phase is not EMA-smoothed -- the complex mean already
    averages it.
    """

    # Emits (amplitude_dB, phase_deg) for the rolling plot + recorder.
    sample_ready = Qt.pyqtSignal(float, float)

    def __init__(self, ringbuffer1, ringbuffer_prod,
                 fft_size=4096, samp_rate=100_000,
                 ema_alpha=0.1, refresh_ms=20, emit_ms=100,
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
        self._alpha      = ema_alpha     # EMA factor for amplitude only
        self._s1         = 1           # smoothed amplitude (dB)
        self._ant1_amplitude_db = None    # peak amplitude (dB), antenna 1
        self._ant1_phase_deg    = None    # phase diff (deg), magnitude-weighted

        self._build_ui()

        # ~1000/refresh_ms Hz: live bars + label refresh
        t = QTimer(self)
        t.setInterval(refresh_ms)
        t.timeout.connect(self._update)
        t.start()

        # ~1000/emit_ms Hz: decimated emit for the rolling plot + CSV recorder
        self._emit_timer = QTimer(self)
        self._emit_timer.setInterval(emit_ms)
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


class RollingPanel(QWidget):
    """Always-on rolling amp/phase strip-chart + START/STOP CSV recorder.

    Displays amplitude (teal) and phase (orange) over a moving time window
    on a shared, scrolling time axis.  The record button gates whether each
    incoming sample is also appended to a CSV; the display runs regardless.

    Pass ``window_s`` to change the rolling window length; it shadows the
    class-level ``WINDOW_S`` default on this instance.
    """

    WINDOW_S    = 60.0               # rolling window length (seconds)
    AMP_FIXED   = (-50.0, -10.0)     # default amplitude y-range (dBFS)
    PHASE_FIXED = (-180.0, 180.0)    # default phase y-range (deg)

    def __init__(self, window_s=None, parent=None):
        super().__init__(parent)
        if window_s is not None:
            self.WINDOW_S = float(window_s)   # per-instance override
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
