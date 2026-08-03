#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spectrum_tab.py
===============

``SpectrumWidget`` — the "SPECTRUM" tab: the frequency-domain views of
receiver 1, kept together and away from the measurement dashboard.

Three plots, all of RX meas-1, at three points in the chain:

    sdr_rx_meas1 ──► [PRE-LPF]  what the antenna actually delivers:
         │                      noise floor, interferers, spurs.
         ▼
    lpf_rx_meas1 ──► [POST-LPF] the same signal after the 1 kHz low-pass,
         │                      i.e. what the measurement consumes.
         │                      Comparing the two shows what the filter
         │                      is doing (and will show the effect of the
         │                      filter parameters once they are tunable).
         └────────► [PEAK SEARCH] post-LPF again, with the ribbon's search
                                band shaded and a marker on the bin the
                                amplitude readout is reporting.
"""

from PyQt5 import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout

from .ui_kit import _STYLESHEET, _TEAL, _ORANGE, _splitter
from .panels import FFTPanel, PeakSearchPanel


class SpectrumWidget(QWidget):
    """The three spectrum views, wired to the same live settings as the
    dashboard (see trx_ssb._apply_live / _rebuild_flowgraph)."""

    def __init__(self, rb_sdr_rx_meas1, rb_lpf_rx_meas1,
                 fft_size=4096, samp_rate=100_000, refresh_ms=20,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLESHEET)

        self._pre = FFTPanel('◈ RX MEAS1  —  PRE-LPF  (raw from the radio)',
                             rb_sdr_rx_meas1, fft_size, samp_rate, _TEAL,
                             refresh_ms=refresh_ms)
        self._post = FFTPanel('◈ RX MEAS1  —  POST-LPF  (what is measured)',
                              rb_lpf_rx_meas1, fft_size, samp_rate, _TEAL,
                              refresh_ms=refresh_ms)
        self._peak = PeakSearchPanel('◈ PEAK SEARCH  —  band + located peak',
                                     rb_lpf_rx_meas1, fft_size, samp_rate,
                                     _ORANGE, refresh_ms=refresh_ms)

        split = _splitter(Qt.Qt.Vertical)
        for w in (self._pre, self._post, self._peak):
            split.addWidget(w)
        split.setSizes([1, 1, 1])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(split)

    # ── Live settings (mirrors UnifiedDashboard's API) ────────────────────
    def _each(self):
        return (self._pre, self._post, self._peak)

    def set_center_freq(self, fc):
        for w in self._each():
            w.set_center_freq(fc)

    def set_band(self, band):
        self._peak.set_band(band)

    def set_fft_size(self, fft_size):
        """Call AFTER the ring buffers have been resized."""
        for w in self._each():
            w.set_fft_size(fft_size)

    def set_samp_rate(self, samp_rate):
        for w in self._each():
            w.set_samp_rate(samp_rate)

    def set_refresh_ms(self, refresh_ms):
        for w in self._each():
            w.set_refresh_ms(refresh_ms)
