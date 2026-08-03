#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py
============

``UnifiedDashboard`` assembles the individual panels (panels.py) into the
single "SDR DASHBOARD" widget

"""

from PyQt5 import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton

from .ui_kit import _STYLESHEET, _TEAL, _ORANGE, _splitter
from .panels import FFTPanel, PhasePanel, EqualizerPanel, RollingPanel


class UnifiedDashboard(QWidget):
    """Top-level dashboard widget: the FFT/phase/equalizer row plus the
    rolling amp/phase strip-chart, wired together.

    Display-tuning arguments (``ema_alpha``, ``refresh_ms``, ``emit_ms``,
    ``rolling_window_s``) are forwarded to the child panels so the whole UI
    can be driven from config.py.  
    """

    def __init__(self, ringbuffer1, ringbuffer_prod,
                 fft_size=4096, samp_rate=100_000,
                 ema_alpha=0.1, refresh_ms=20, emit_ms=100,
                 rolling_window_s=60.0,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLESHEET)
        self.setMinimumSize(900, 600)

        # ── outer layout: toggle button on top, content below ─────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(0)

        self._gr_toggle_btn = QPushButton('⚙  GR CONTROLS')
        self._gr_toggle_btn.setFixedHeight(24)
        self._gr_toggle_btn.clicked.connect(self._toggle_gr_window)
        self._gr_win_ref = None
        btn_row.addWidget(self._gr_toggle_btn)

        # Synthetic pipe toggle — wired to a SynthPipeInjector via
        # set_synth_pipe(); disabled until the injector is attached.
        self._synth_btn = QPushButton('⦿ SYNTH PIPE: OFF')
        self._synth_btn.setFixedHeight(24)
        self._synth_btn.setEnabled(False)
        self._synth_pipe = None
        self._synth_btn.clicked.connect(
            lambda: self._synth_pipe and self._synth_pipe.toggle())
        btn_row.addWidget(self._synth_btn)

        outer.addLayout(btn_row)

        # ── top row: FFT(sig 1) + Phase(sig 1) + equalizer side by side ──
        top_split = _splitter(Qt.Qt.Horizontal)

        spectra_split = _splitter(Qt.Qt.Horizontal)
        self._fft1 = FFTPanel('◈ FFT  SIGNAL 1', ringbuffer1,
                              fft_size, samp_rate, _TEAL,
                              refresh_ms=refresh_ms)
        self._phase_panel = PhasePanel('◈ PHASE  rx · conj(tx)', ringbuffer_prod,
                                       samp_rate, _ORANGE,
                                       refresh_ms=refresh_ms)
        spectra_split.addWidget(self._fft1)
        spectra_split.addWidget(self._phase_panel)
        spectra_split.setSizes([1, 1])

        self._eq = EqualizerPanel(ringbuffer1, ringbuffer_prod,
                                  fft_size, samp_rate,
                                  ema_alpha=ema_alpha,
                                  refresh_ms=refresh_ms,
                                  emit_ms=emit_ms)

        top_split.addWidget(spectra_split)
        top_split.addWidget(self._eq)
        top_split.setSizes([2, 1])

        # ── bottom: rolling amplitude + phase vs time ─────────────────────
        self._rolling_panel = RollingPanel(window_s=rolling_window_s)
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

    def set_synth_pipe(self, injector):
        """Attach a SynthPipeInjector; its status drives the toggle button."""
        self._synth_pipe = injector
        self._synth_btn.setEnabled(True)
        injector.status_changed.connect(self._on_synth_status)

    def _on_synth_status(self, text, armed):
        self._synth_btn.setText(text)
        self._synth_btn.setStyleSheet(
            'background-color: #7a3300;' if armed else '')

    def _toggle_gr_window(self):
        if self._gr_win_ref is None:
            return
        if self._gr_win_ref.isVisible():
            self._gr_win_ref.hide()
        else:
            self._gr_win_ref.show()
            self._gr_win_ref.raise_()
