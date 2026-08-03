#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py
===========

Safety net for the values the ribbon feeds into the radios.

``validate_profile(profile)`` is called before anything is applied to the
running app.  It returns

    (clean, errors, warnings)

``errors`` is a list of human-readable strings; if it is non-empty the
caller must NOT apply the profile.  ``warnings`` describe values that are
legal but likely a mistake, and ``clean`` is the profile with the
clamp-able fields clamped (display-only knobs like plot_fps).

Design rule: values that only affect the *display* are clamped silently
and reported as warnings.  Values that reach the *hardware* — the centre
frequency and the sample rate above all — are never silently corrected.
A mistyped centre frequency clamped to the band edge would transmit on
the wrong frequency without telling anyone, so those are hard errors and
the previous setting stays in force.
"""

from __future__ import annotations

from .config import VALID_REDPITAYA_RATES

# ──────────────────────────────────────────────────────────────────────────────
# Hardware limits — properties of the Red Pitaya, not user preferences, so
# they live here as constants rather than in settings.json.
#
# STEMlab 125-14: 125 MS/s ADC/DAC, so the usable tuning range runs from DC
# to the 62.5 MHz Nyquist limit. The SDR-transceiver FPGA image will accept
# a request outside that range without complaining and simply alias, which
# is exactly the silent-wrong-answer case this module exists to prevent.
# ──────────────────────────────────────────────────────────────────────────────
RP_FREQ_MIN_HZ = 0.0
RP_FREQ_MAX_HZ = 62_500_000.0

# Below this the coil/coax and the transceiver's DC blocking make the
# measurement meaningless long before anything is at risk; warn, don't block.
FREQ_SANITY_MIN_HZ = 10_000.0

FFT_SIZE_MIN = 256
FFT_SIZE_MAX = 65_536
PLOT_FPS_MIN = 1
PLOT_FPS_MAX = 120          # above this Qt just drops frames and burns CPU
EMIT_MS_MIN = 10
EMIT_MS_MAX = 10_000
ROLLING_WINDOW_MIN_S = 1.0
ROLLING_WINDOW_MAX_S = 3_600.0


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def validate_profile(profile: dict):
    """Check a settings profile. Returns (clean, errors, warnings)."""
    clean: dict = dict(profile or {})
    errors: list[str] = []
    warnings: list[str] = []

    def _num(key, cast, default=None):
        """Cast a field, recording an error if it is not a number."""
        if key not in clean:
            return default
        try:
            return cast(clean[key])
        except (TypeError, ValueError):
            errors.append(f"{key}: {clean[key]!r} is not a number.")
            return None

    # ── centre frequency: hardware, never silently corrected ──────────────
    fc = _num("center_freq_hz", float)
    if fc is not None:
        if not (RP_FREQ_MIN_HZ <= fc <= RP_FREQ_MAX_HZ):
            errors.append(
                f"center_freq_hz: {fc:,.0f} Hz is outside the Red Pitaya's "
                f"{RP_FREQ_MIN_HZ:,.0f}–{RP_FREQ_MAX_HZ:,.0f} Hz range "
                f"(125 MS/s converters, 62.5 MHz Nyquist). The radio would "
                f"alias instead of refusing, so this is not applied. "
                f"Did you mean {fc/1e3:,.0f} kHz?"
            )
        elif fc < FREQ_SANITY_MIN_HZ:
            warnings.append(
                f"center_freq_hz {fc:,.0f} Hz is very low; the transceiver's "
                f"DC path will dominate the measurement."
            )

    # ── sample rate: only the driver's discrete set really works ──────────
    sr = _num("samp_rate_hz", int)
    if sr is not None and sr not in VALID_REDPITAYA_RATES:
        errors.append(
            f"samp_rate_hz: {sr:,} is not one of the supported rates "
            f"{', '.join(f'{r:,}' for r in VALID_REDPITAYA_RATES)}. The driver "
            f"silently falls back to 100 kHz, which corrupts every frequency "
            f"axis and the LPF design."
        )
        sr = None

    # ── ROI band: must overlap the sampled bandwidth ──────────────────────
    band = clean.get("band_hz")
    if band is not None:
        try:
            lo, hi = float(band[0]), float(band[1])
        except (TypeError, ValueError, IndexError):
            errors.append(f"band_hz: {band!r} must be a [low, high] pair.")
            lo = hi = None
        if lo is not None:
            if lo >= hi:
                errors.append(f"band_hz: low ({lo:,.0f}) must be below high ({hi:,.0f}).")
            elif sr is not None:
                nyq = sr / 2.0
                if lo > nyq or hi < -nyq:
                    # The ROI mask would select no bins at all and the
                    # amplitude/phase readouts would silently stop updating.
                    errors.append(
                        f"band_hz [{lo:,.0f}, {hi:,.0f}] lies entirely outside "
                        f"the sampled band (±{nyq:,.0f} Hz at {sr:,} S/s). The "
                        f"ROI would contain no FFT bins and the equalizer would "
                        f"stop updating."
                    )
                elif lo < -nyq or hi > nyq:
                    warnings.append(
                        f"band_hz [{lo:,.0f}, {hi:,.0f}] extends past ±{nyq:,.0f} Hz; "
                        f"the ROI is effectively the whole sampled band."
                    )

    # ── receiver low-pass ─────────────────────────────────────────────────
    cut = _num("lpf_cutoff_hz", float)
    trans = _num("lpf_transition_hz", float)
    if cut is not None and sr is not None:
        nyq = sr / 2.0
        if cut <= 0:
            errors.append(f"lpf_cutoff_hz: {cut} must be positive.")
        elif cut >= nyq:
            errors.append(
                f"lpf_cutoff_hz: {cut:,.0f} Hz must be below Nyquist "
                f"({nyq:,.0f} Hz at {sr:,} S/s); firdes cannot design it.")
        elif trans is not None and cut + trans > nyq:
            errors.append(
                f"lpf_cutoff_hz + lpf_transition_hz ({cut:,.0f} + {trans:,.0f}) "
                f"exceeds Nyquist ({nyq:,.0f} Hz); the rolloff has no room.")
    if trans is not None and trans <= 0:
        errors.append(f"lpf_transition_hz: {trans} must be positive "
                      f"(it sets the filter length).")
    elif trans is not None and sr is not None and trans < sr / 20_000.0:
        warnings.append(
            f"lpf_transition_hz {trans:,.0f} Hz is very narrow for {sr:,} S/s; "
            f"the filter will need a great many taps and cost CPU.")

    # ── spectrum display span ─────────────────────────────────────────────
    span = _num("spectrum_span_hz", float)
    if span is not None and sr is not None:
        if span < 0:
            errors.append(f"spectrum_span_hz: {span} must be >= 0 "
                          f"(0 means the full sampled bandwidth).")
        elif span > sr / 2.0:
            clean["spectrum_span_hz"] = sr / 2.0
            warnings.append(
                f"spectrum_span_hz {span:,.0f} clamped to Nyquist "
                f"({sr/2.0:,.0f} Hz).")

    # ── fft_size: sizes the ring buffers and the FFT ──────────────────────
    n = _num("fft_size", int)
    if n is not None:
        if not _is_pow2(n):
            errors.append(f"fft_size: {n} must be a power of two.")
        elif not (FFT_SIZE_MIN <= n <= FFT_SIZE_MAX):
            errors.append(
                f"fft_size: {n} is outside {FFT_SIZE_MIN}–{FFT_SIZE_MAX}.")

    # ── display-only knobs: clamp and warn ────────────────────────────────
    a = _num("ema_alpha", float)
    if a is not None and not (0.0 < a <= 1.0):
        errors.append(
            f"ema_alpha: {a} must be in (0, 1]. 1 disables smoothing; values "
            f"at or below 0 would freeze or destabilise the amplitude readout."
        )

    for key, lo_lim, hi_lim, cast in (
        ("plot_fps", PLOT_FPS_MIN, PLOT_FPS_MAX, int),
        ("spectrum_fps", PLOT_FPS_MIN, PLOT_FPS_MAX, int),
        ("emit_interval_ms", EMIT_MS_MIN, EMIT_MS_MAX, int),
        ("rolling_window_s", ROLLING_WINDOW_MIN_S, ROLLING_WINDOW_MAX_S, float),
    ):
        v = _num(key, cast)
        if v is None:
            continue
        if v < lo_lim or v > hi_lim:
            clamped = cast(min(max(v, lo_lim), hi_lim))
            clean[key] = clamped
            warnings.append(f"{key}: {v} clamped to {clamped} "
                            f"(allowed {lo_lim}–{hi_lim}).")

    # ── receivers ─────────────────────────────────────────────────────────
    nrx = _num("num_receivers", int)
    if nrx is not None and nrx not in (1, 2):
        errors.append(f"num_receivers: {nrx} must be 1 or 2.")

    # ── recording note: it becomes part of a filename ─────────────────────
    note = clean.get("note")
    if note is not None:
        if not str(note).strip():
            errors.append("note: must not be empty; it names the capture files.")
        elif any(c in str(note) for c in '/\\'):
            warnings.append(
                "note: path separators are replaced with '_' in filenames.")

    return clean, errors, warnings
