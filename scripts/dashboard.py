#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py
============

``UnifiedDashboard`` assembles the individual panels (panels.py) into the
single "SDR DASHBOARD" widget

"""

from PyQt5 import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout

from .ui_kit import _STYLESHEET, _ORANGE, _splitter
from .panels import PhasePanel, EqualizerPanel, RollingPanel


class UnifiedDashboard(QWidget):
    """Top-level dashboard widget: the FFT/phase/equalizer row plus the
    rolling amp/phase strip-chart, wired together.

    Display-tuning arguments (``ema_alpha``, ``refresh_ms``, ``emit_ms``,
    ``rolling_window_s``) are forwarded to the child panels so the whole UI
    can be driven from config.py.  
    """

    def __init__(self, rb_lpf_rx_meas1, rb_multiply_conjugate_rx_txconj,
                 fft_size=4096, samp_rate=100_000,
                 ema_alpha=0.1, refresh_ms=20, emit_ms=100,
                 rolling_window_s=60.0,
                 power_cal_offset_db=0.0, power_unit='dBFS',
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLESHEET)
        self.setMinimumSize(820, 420)

        # ── outer layout ──────────────────────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── top row: Phase(sig 1) + equalizer side by side ────────────────
        # The spectra live in their own SPECTRUM tab (spectrum_tab.py).
        top_split = _splitter(Qt.Qt.Horizontal)

        self._phase_panel = PhasePanel('◈ PHASE  rx · conj(tx)', rb_multiply_conjugate_rx_txconj,
                                       samp_rate, _ORANGE,
                                       refresh_ms=refresh_ms)

        self._eq = EqualizerPanel(rb_lpf_rx_meas1, rb_multiply_conjugate_rx_txconj,
                                  fft_size, samp_rate,
                                  ema_alpha=ema_alpha,
                                  refresh_ms=refresh_ms,
                                  emit_ms=emit_ms,
                                  power_cal_offset_db=power_cal_offset_db,
                                  power_unit=power_unit)

        top_split.addWidget(self._phase_panel)
        top_split.addWidget(self._eq)
        top_split.setSizes([2, 1])

        # ── bottom: rolling amplitude + phase vs time ─────────────────────
        self._rolling_panel = RollingPanel(window_s=rolling_window_s)
        self._eq.sample_ready.connect(self._rolling_panel.add_sample)

        # vertical splitter: top row | rolling time-series
        v_split = _splitter(Qt.Qt.Vertical)
        v_split.addWidget(top_split)
        v_split.addWidget(self._rolling_panel)
        v_split.setSizes([260, 360])

        outer.addWidget(v_split)

    # ── Public API ─────────────────────────────────────────────────────────
    def set_center_freq(self, fc: float):
        self._eq.set_center_freq(fc)

    def set_band(self, band):
        self._eq.set_band(band)

    def set_record_path_provider(self, fn):
        self._rolling_panel.set_path_provider(fn)

    # ── Live settings (applied without restarting the app) ────────────────
    def set_ema_alpha(self, alpha):
        self._eq.set_ema_alpha(alpha)

    def set_power_cal(self, offset_db, unit=None):
        self._eq.set_power_cal(offset_db, unit)

    def set_refresh_ms(self, refresh_ms):
        self._phase_panel.set_refresh_ms(refresh_ms)
        self._eq.set_refresh_ms(refresh_ms)

    def set_emit_ms(self, emit_ms):
        self._eq.set_emit_ms(emit_ms)

    def set_fft_size(self, fft_size):
        """Call AFTER the ring buffers have been resized."""
        self._phase_panel.set_fft_size(fft_size)
        self._eq.set_fft_size(fft_size)

    def set_samp_rate(self, samp_rate):
        self._phase_panel.set_samp_rate(samp_rate)
        self._eq.set_samp_rate(samp_rate)

    def set_rolling_window_s(self, window_s):
        self._rolling_panel.set_window_s(window_s)
