#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
synth_pipe.py
=============

``SynthPipeInjector`` — arms/disarms a synthetic pipe echo that is ADDED
to the *real* RX channel-1 stream inside the running flowgraph.

How it works
------------
main.py inserts two extra GR blocks into the ch-1 path:

    T2 (fft_filter_xxx_0_0_0) ──► multiply_const_cc(k) ──┐
                                                         ▼
    R2 (fft_filter_xxx_0) ────────────────────────► add_cc ──► sink1 /
                                                        multiply_conjugate / file sink

so the measured stream becomes  R2 + k·T2.  While DISARMED k = 0 and the
adder is an exact passthrough (x + 0 == x): the GUI sees pure hardware
data.  While ARMED this class runs a Qt timer that sweeps

    k(t) = P̂ · 10^(s_over_p_db/20) · e^(j·phase_deg) · exp(-(x(t)-d)²/(2σ²))

where x(t) = speed·(time since arm) is the simulated bit position and
P̂ = mean(rb_prod) measured once at arm time — i.e. the echo is scaled
relative to the *actual* direct coupling P seen by the hardware at that
moment, and stays coherent with the TX reference because it is literally
built from T2.

Thread safety: ``multiply_const_cc.set_k`` is a runtime-safe GR setter
(the same mechanism GRC callbacks use), and ``RingBuffer.read`` is
lock-guarded, so everything here may run on the Qt main thread while the
GR scheduler streams.
"""

import time

import numpy as np
from PyQt5 import QtCore


class SynthPipeInjector(QtCore.QObject):
    """Drives the k of the injection multiply_const_cc from a Qt timer."""

    # (status_text, armed) — for the dashboard button label/colour.
    status_changed = QtCore.pyqtSignal(str, bool)

    def __init__(self, mult_block, rb_prod, cfg, parent=None):
        super().__init__(parent)
        self._mult    = mult_block
        self._rb_prod = rb_prod

        self.s_over_p_db = float(cfg.synth_pipe_s_over_p_db)
        self.phase_deg   = float(cfg.synth_pipe_phase_deg)
        self.distance_m  = float(cfg.synth_pipe_distance_m)
        self.sigma_m     = float(cfg.synth_pipe_sigma_m)
        self.speed_mps   = float(cfg.synth_pipe_speed_mps)

        self.armed  = False
        self._t0    = 0.0
        self._p_hat = 1.0 + 0.0j

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(max(10, int(cfg.synth_pipe_update_ms)))
        self._timer.timeout.connect(self._tick)

    # ── public ────────────────────────────────────────────────────────────
    def toggle(self):
        self.disarm() if self.armed else self.arm()

    def arm(self):
        # Reference the echo to the direct coupling actually present NOW,
        # then freeze it — the injected dip must not chase itself.
        p = np.mean(self._rb_prod.read())
        if not np.isfinite(p) or abs(p) < 1e-12:
            print("[synth_pipe] WARNING: no measurable direct coupling; "
                  "using P=1+0j reference")
            p = 1.0 + 0.0j
        self._p_hat = complex(p)
        self._t0    = time.monotonic()
        self.armed  = True
        self._timer.start()
        self._tick()

    def disarm(self):
        self._timer.stop()
        self.armed = False
        self._mult.set_k(0)
        self.status_changed.emit("⦿ SYNTH PIPE: OFF", False)

    # ── timer tick ────────────────────────────────────────────────────────
    def _tick(self):
        if self.speed_mps <= 0.0:
            # Static pipe: full-strength echo added constantly while armed.
            env, status = 1.0, "⦿ SYNTH PIPE: ON (static)"
        else:
            # Fly-by: bit advances from 0 at speed_mps toward the pipe.
            x   = self.speed_mps * (time.monotonic() - self._t0)
            env = np.exp(-((x - self.distance_m) ** 2)
                         / (2.0 * self.sigma_m ** 2))
            status = (f"⦿ SYNTH PIPE: x={x:5.1f} m   "
                      f"pipe@{self.distance_m:.1f} m   env={env:.3f}")
        k = (self._p_hat
             * 10.0 ** (self.s_over_p_db / 20.0)
             * np.exp(1j * np.deg2rad(self.phase_deg))
             * env)
        self._mult.set_k(complex(k))
        self.status_changed.emit(status, True)
