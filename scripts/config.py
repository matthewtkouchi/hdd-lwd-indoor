#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py
=========

Single source of truth for tunable parameters, now backed by a JSON
profile store (settings.json).

Model
-----
settings.json holds:
    working  : the live, editable values the app actually runs on
    profiles : named presets you can Load from / Save to
    loaded_from : the profile name the working copy was last loaded from

On startup the app runs ``AppConfig.load()`` which reads the *working*
copy. The ribbon (settings_ribbon.py) edits working / profiles and writes
the file back, then the process re-execs to apply.

The dataclass defaults below are the built-in fallback used only when
settings.json is missing or a key is absent.
"""

from __future__ import annotations

import os
import sys
import json
from dataclasses import dataclass, field, fields

# ──────────────────────────────────────────────────────────────────────────────
# Valid Red Pitaya SDR-transceiver I/Q rates. Anything else is silently
# rejected by the driver (falls back to 100 kHz), so the ribbon only ever
# offers these.
# ──────────────────────────────────────────────────────────────────────────────
VALID_REDPITAYA_RATES = (20_000, 50_000, 100_000, 250_000, 500_000, 1_250_000)

# Where settings.json lives: project root = parent of this scripts/ folder.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(_PROJECT_ROOT, "settings.json")


@dataclass
class AppConfig:
    """All runtime-tunable parameters. Defaults are the built-in fallback."""

    # ── Hardware addresses ────────────────────────────────────────────────
    rx_addr: str = "169.254.9.106"          # RX Red Pitaya IP
    tx_addr: str = "169.254.102.116"        # TX Red Pitaya IP

    # ── Radio tuning ──────────────────────────────────────────────────────
    center_freq_hz: int = 1_000_000         # RX + TX centre freq
    samp_rate_hz:   int = 100_000           # master I/Q rate (must be a valid rate)
    band_hz: tuple = (-30_000, 30_000)      # ROI band [low, high]

    # ── Synthetic pipe injection (test aid) ───────────────────────────────
    # RX ch-1 stays REAL hardware data; when armed, a synthetic pipe echo
    # (a scaled/rotated copy of the TX reference with a Gaussian envelope
    # in distance) is ADDED to it. Disarmed = exact passthrough.
    synth_pipe_s_over_p_db: float = -50.0   # echo strength rel. to direct coupling P (dB)
    synth_pipe_phase_deg:   float = 90.0    # echo phase rel. to P (deg; eddy ~ quadrature)
    synth_pipe_distance_m:  float = 5.0     # pipe position along the bore (m)
    synth_pipe_sigma_m:     float = 0.5     # Gaussian half-width of the crossing (m)
    synth_pipe_speed_mps:   float = 0.0     # 0 = static pipe while armed; >0 = fly-by sim (m/s)
    synth_pipe_update_ms:   int   = 50      # injector gain-update cadence (ms)

    # ── Recording / file outputs ──────────────────────────────────────────
    note:          str = "RECORDING_NOTE"
    out_dir:       str = "captures"         # folder (relative to main.py) for saved files
    file_base:     str = "capture"          # CSV filename prefix
    remote_file_1: str = "rx1.bin"          # receiver-1 raw IQ
    remote_file_2: str = "rx2.bin"          # receiver-2 raw IQ

    # ── DSP / display tuning ──────────────────────────────────────────────
    fft_size:         int   = 4096
    ema_alpha:        float = 0.1           # amplitude smoothing (0<a<=1, lower=smoother)
    rolling_window_s: float = 60.0
    plot_fps:         int   = 30            # live plot refresh rate (frames per second)
    emit_interval_ms: int   = 100           # rolling-chart / recorder cadence (ms)

    # ── Derived (computed; not stored as profile values) ──────────────────
    tx_port_1: str = field(init=False)
    tx_port_2: str = field(init=False)
    plot_refresh_ms: int = field(init=False)

    def __post_init__(self) -> None:
        self.tx_port_1 = f"{self.tx_addr}:1001"
        self.tx_port_2 = f"{self.tx_addr}:1002"
        self.plot_refresh_ms = max(1, round(1000 / self.plot_fps))
        if self.samp_rate_hz not in VALID_REDPITAYA_RATES:
            print(
                f"[config] WARNING: samp_rate_hz={self.samp_rate_hz} is not a "
                f"valid Red Pitaya rate {VALID_REDPITAYA_RATES}; the hardware "
                f"will fall back to 100 kHz and the signal will be distorted.",
                file=sys.stderr,
            )

    # ── Profile (de)serialization ─────────────────────────────────────────
    @classmethod
    def profile_field_names(cls):
        """Names of the editable (init=True) fields a profile stores."""
        return [f.name for f in fields(cls) if f.init]

    def to_profile(self) -> dict:
        """Serialize this config's editable fields to a JSON-able dict."""
        out = {}
        for name in self.profile_field_names():
            val = getattr(self, name)
            if isinstance(val, tuple):
                val = list(val)         # JSON has no tuples
            out[name] = val
        return out

    @classmethod
    def from_profile(cls, profile: dict) -> "AppConfig":
        """Build an AppConfig from a profile dict, ignoring unknown keys and
        filling any missing key from the dataclass default."""
        valid = set(cls.profile_field_names())
        kwargs = {k: v for k, v in (profile or {}).items() if k in valid}
        if isinstance(kwargs.get("band_hz"), list):
            kwargs["band_hz"] = tuple(kwargs["band_hz"])
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str = SETTINGS_PATH) -> "AppConfig":
        """Build an AppConfig from the *working* copy in settings.json."""
        data = load_settings(path)
        return cls.from_profile(data.get("working", {}))


# ──────────────────────────────────────────────────────────────────────────────
# settings.json helpers
# ──────────────────────────────────────────────────────────────────────────────
def _default_settings() -> dict:
    base = AppConfig().to_profile()
    return {
        "loaded_from": "default",
        "working": dict(base),
        "profiles": {"default": dict(base)},
    }


def load_settings(path: str = SETTINGS_PATH) -> dict:
    """Read settings.json; create it from defaults if missing/corrupt."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "working" not in data or "profiles" not in data:
            raise ValueError("malformed settings.json")
        data.setdefault("loaded_from", "default")
        return data
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[config] settings.json unusable ({exc}); writing defaults.",
              file=sys.stderr)
        data = _default_settings()
        save_settings(data, path)
        return data


def save_settings(data: dict, path: str = SETTINGS_PATH) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)