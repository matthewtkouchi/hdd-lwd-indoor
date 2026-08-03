#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
settings_ribbon.py
==================

A strip that sits above the dashboard tabs and lets the user manage
parameter *profiles* and apply them to the running app.

Model (see config.py):
  - "working" is the live editable copy the app runs on.
  - "profiles" are named presets you Load from / Save to.
  - Editing fields changes only the working copy, never the loaded preset.
  - Save / Save As are the only actions that write a named profile.
  - APPLY writes the working copy and hands it to the host, which applies
    each changed field either with a live setter or by rebuilding the
    flowgraph in place. The window stays open either way.
  - RESTART APP writes the working copy and re-execs the process; it is
    only an escape hatch.

The ribbon talks only to settings.json; it owns no GNU Radio state.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QInputDialog, QMessageBox,
)
from PyQt5.QtGui import QIntValidator, QDoubleValidator

from scripts.config import (
    load_settings, save_settings, AppConfig, VALID_REDPITAYA_RATES,
)

try:
    from scripts.ui_kit import _BG, _PANEL, _BORDER, _TEAL, _TEAL_DIM, _TEXT, _TEXT_DIM
except Exception:                       # ui_kit optional; fall back to neutral colors
    _BG = _PANEL = "#101010"; _BORDER = "#333"; _TEAL = "#00ffcc"
    _TEAL_DIM = "#005544"; _TEXT = "#cccccc"; _TEXT_DIM = "#888888"

# Editable fields the ribbon exposes (subset of the full profile).
RIBBON_KEYS = ["samp_rate_hz", "center_freq_hz", "fft_size",
               "plot_fps", "band_hz", "note", "ema_alpha"]
FFT_SIZES = [1024, 2048, 4096, 8192, 16384]


def _fmt_rate(hz):
    return f"{hz/1e6:g} MHz" if hz >= 1e6 else f"{hz/1e3:g} kHz"


class SettingsRibbon(QWidget):
    """Profile strip above the dashboard.

    ``on_apply(profile_dict) -> str`` applies the working copy to the
    running app and returns a short description of what it did; the host
    decides per field whether that means a live setter or an in-place
    flowgraph rebuild.  ``on_apply_restart`` is the escape hatch that
    re-execs the whole process.
    """

    def __init__(self, settings_path, on_apply, on_apply_restart, parent=None):
        super().__init__(parent)
        self._path = settings_path
        self._apply_cb = on_apply
        self._on_apply_restart = on_apply_restart

        data = load_settings(self._path)
        self._working = dict(data.get("working", {}))
        self._loaded_name = data.get("loaded_from", "default")
        # Placeholder so _update_dirty (fired by textChanged during
        # _set_fields below) never sees the attribute missing; the real
        # baseline is assigned right after.
        self._loaded_ribbon = None

        self._build_ui()
        self._refresh_profile_combo(select=self._loaded_name)
        self._set_fields(self._working)
        # baseline for the "edited" asterisk = the profile we claim to come from
        base = data.get("profiles", {}).get(self._loaded_name, self._working)
        self._loaded_ribbon = self._ribbon_subset(base)
        self._update_dirty()

    # ── UI construction ───────────────────────────────────────────────────
    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{ background:{_PANEL}; color:{_TEXT}; font-family:'Courier New'; font-size:11px; }}
            QLabel  {{ color:{_TEXT_DIM}; }}
            QPushButton {{ background:{_TEAL_DIM}; color:{_TEAL}; border:1px solid {_TEAL};
                           padding:3px 8px; }}
            QPushButton:hover {{ background:{_TEAL}; color:{_BG}; }}
            QComboBox, QLineEdit {{ background:{_BG}; color:{_TEAL};
                                    border:1px solid {_BORDER}; padding:2px 4px; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        # ── Row 1: profile management ─────────────────────────────────────
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("PROFILE:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(140)
        r1.addWidget(self.profile_combo)
        for txt, fn in [("Load", self._on_load), ("Save", self._on_save),
                        ("Save As", self._on_saveas), ("Delete", self._on_delete)]:
            b = QPushButton(txt); b.clicked.connect(fn); r1.addWidget(b)
        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet(f"color:{_TEAL};")
        r1.addWidget(self._dirty_lbl)
        r1.addStretch()
        outer.addLayout(r1)

        # ── Row 2: parameter fields + apply ───────────────────────────────
        r2 = QHBoxLayout()

        r2.addWidget(QLabel("samp_rate:"))
        self.samp_combo = QComboBox()
        for hz in VALID_REDPITAYA_RATES:
            self.samp_combo.addItem(_fmt_rate(hz), hz)
        r2.addWidget(self.samp_combo)

        r2.addWidget(QLabel("center_freq (Hz):"))
        self.freq_edit = QLineEdit(); self.freq_edit.setValidator(QIntValidator(1, 2_000_000_000))
        self.freq_edit.setFixedWidth(90); r2.addWidget(self.freq_edit)

        r2.addWidget(QLabel("fft_size:"))
        self.fft_combo = QComboBox()
        for n in FFT_SIZES:
            self.fft_combo.addItem(str(n), n)
        r2.addWidget(self.fft_combo)

        r2.addWidget(QLabel("plot_fps:"))
        self.fps_edit = QLineEdit(); self.fps_edit.setValidator(QIntValidator(1, 240))
        self.fps_edit.setFixedWidth(45); r2.addWidget(self.fps_edit)

        r2.addWidget(QLabel("ema_alpha:"))
        self.alpha_edit = QLineEdit()
        av = QDoubleValidator(0.0001, 1.0, 4); self.alpha_edit.setValidator(av)
        self.alpha_edit.setFixedWidth(55); r2.addWidget(self.alpha_edit)

        r2.addWidget(QLabel("band (Hz):"))
        self.band_lo = QLineEdit(); self.band_lo.setValidator(QIntValidator(-1_000_000_000, 1_000_000_000))
        self.band_lo.setFixedWidth(70); r2.addWidget(self.band_lo)
        r2.addWidget(QLabel("..."))
        self.band_hi = QLineEdit(); self.band_hi.setValidator(QIntValidator(-1_000_000_000, 1_000_000_000))
        self.band_hi.setFixedWidth(70); r2.addWidget(self.band_hi)

        r2.addWidget(QLabel("note:"))
        self.note_edit = QLineEdit(); self.note_edit.setFixedWidth(140); r2.addWidget(self.note_edit)

        r2.addStretch()
        self.apply_btn = QPushButton("APPLY")
        self.apply_btn.setToolTip(
            "Apply to the running app. Frequency, note, band, smoothing and\n"
            "refresh rate take effect immediately; sample rate and FFT size\n"
            "rebuild the flowgraph in place. The window stays open either way."
        )
        self.apply_btn.clicked.connect(self._on_apply)
        r2.addWidget(self.apply_btn)
        self.restart_btn = QPushButton("RESTART APP")
        self.restart_btn.setToolTip(
            "Save and re-exec the whole process. Only needed if the radios\n"
            "get into a state an in-place rebuild cannot clear."
        )
        self.restart_btn.clicked.connect(self._on_restart)
        r2.addWidget(self.restart_btn)
        outer.addLayout(r2)

        # status line for what APPLY actually did
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color:{_TEXT_DIM};")
        outer.addWidget(self._status_lbl)

        # mark dirty on any edit
        for w in (self.samp_combo, self.fft_combo):
            w.currentIndexChanged.connect(self._update_dirty)
        for w in (self.freq_edit, self.fps_edit, self.alpha_edit,
                  self.band_lo, self.band_hi, self.note_edit):
            w.textChanged.connect(self._update_dirty)

    # ── Field <-> dict helpers ────────────────────────────────────────────
    def _ribbon_subset(self, profile):
        """Pull just the ribbon-editable keys out of a full profile dict,
        filling any missing key from AppConfig defaults."""
        d = AppConfig().to_profile()
        d.update(profile or {})
        out = {k: d[k] for k in RIBBON_KEYS}
        if isinstance(out["band_hz"], tuple):
            out["band_hz"] = list(out["band_hz"])
        return out

    def _set_fields(self, profile):
        d = self._ribbon_subset(profile)
        i = self.samp_combo.findData(int(d["samp_rate_hz"]))
        self.samp_combo.setCurrentIndex(i if i >= 0 else 0)
        j = self.fft_combo.findData(int(d["fft_size"]))
        self.fft_combo.setCurrentIndex(j if j >= 0 else self.fft_combo.findData(4096))
        self.freq_edit.setText(str(int(d["center_freq_hz"])))
        self.fps_edit.setText(str(int(d["plot_fps"])))
        self.alpha_edit.setText(str(d["ema_alpha"]))
        band = d["band_hz"]
        self.band_lo.setText(str(int(band[0])))
        self.band_hi.setText(str(int(band[1])))
        self.note_edit.setText(str(d["note"]))

    def _get_fields(self):
        """Read + validate the ribbon fields. Raises ValueError on bad input."""
        try:
            freq = int(self.freq_edit.text())
            fps = int(self.fps_edit.text())
            alpha = float(self.alpha_edit.text())
            lo = int(self.band_lo.text())
            hi = int(self.band_hi.text())
        except (ValueError, TypeError):
            raise ValueError("center_freq, plot_fps, ema_alpha and band must be numbers.")
        if not (0 < alpha <= 1.0):
            raise ValueError("ema_alpha must be in (0, 1].")
        if fps < 1:
            raise ValueError("plot_fps must be >= 1.")
        if lo >= hi:
            raise ValueError("band low must be less than band high.")
        return {
            "samp_rate_hz": int(self.samp_combo.currentData()),
            "center_freq_hz": freq,
            "fft_size": int(self.fft_combo.currentData()),
            "plot_fps": fps,
            "ema_alpha": alpha,
            "band_hz": [lo, hi],
            "note": self.note_edit.text(),
        }

    def _get_fields_safe(self):
        try:
            return self._get_fields()
        except ValueError:
            return None

    # ── Dirty indicator ───────────────────────────────────────────────────
    def _update_dirty(self, *_):
        cur = self._get_fields_safe()
        dirty = (cur is None) or (cur != self._loaded_ribbon)
        self._dirty_lbl.setText("● edited (unsaved)" if dirty else "")

    # ── Profile combo ─────────────────────────────────────────────────────
    def _refresh_profile_combo(self, select=None):
        data = load_settings(self._path)
        names = list(data.get("profiles", {}).keys())
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)
        if select and select in names:
            self.profile_combo.setCurrentText(select)
        self.profile_combo.blockSignals(False)

    # ── Button handlers ───────────────────────────────────────────────────
    def _on_load(self):
        name = self.profile_combo.currentText()
        data = load_settings(self._path)
        profile = data.get("profiles", {}).get(name)
        if profile is None:
            return
        self._working = dict(profile)        # silently overwrite working copy
        self._loaded_name = name
        self._set_fields(profile)
        self._loaded_ribbon = self._ribbon_subset(profile)
        self._update_dirty()

    def _on_save(self):
        name = self.profile_combo.currentText()
        fields = self._get_fields_safe()
        if fields is None:
            QMessageBox.warning(self, "Invalid value", "Fix the highlighted fields first.")
            return
        profile = dict(self._working); profile.update(fields)
        data = load_settings(self._path)
        data["profiles"][name] = profile
        save_settings(data, self._path)
        self._working = profile
        self._loaded_name = name
        self._loaded_ribbon = self._ribbon_subset(profile)
        self._update_dirty()
        QMessageBox.information(self, "Saved", f"Saved profile '{name}'.")

    def _on_saveas(self):
        fields = self._get_fields_safe()
        if fields is None:
            QMessageBox.warning(self, "Invalid value", "Fix the highlighted fields first.")
            return
        name, ok = QInputDialog.getText(self, "Save As", "New profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        profile = dict(self._working); profile.update(fields)
        data = load_settings(self._path)
        data["profiles"][name] = profile
        save_settings(data, self._path)
        self._working = profile
        self._loaded_name = name
        self._loaded_ribbon = self._ribbon_subset(profile)
        self._refresh_profile_combo(select=name)
        self._update_dirty()

    def _on_delete(self):
        name = self.profile_combo.currentText()
        data = load_settings(self._path)
        if len(data.get("profiles", {})) <= 1:
            QMessageBox.warning(self, "Cannot delete", "At least one profile must remain.")
            return
        if QMessageBox.question(self, "Delete profile", f"Delete '{name}'?") != QMessageBox.Yes:
            return
        data["profiles"].pop(name, None)
        save_settings(data, self._path)
        self._refresh_profile_combo()

    def _save_working(self):
        """Validate the fields and persist them as the working copy.

        Returns the working dict, or None if a field is invalid.
        """
        fields = self._get_fields_safe()
        if fields is None:
            QMessageBox.warning(self, "Invalid value", "Fix the highlighted fields first.")
            return None
        self._working.update(fields)
        data = load_settings(self._path)
        data["working"] = self._working
        data["loaded_from"] = self._loaded_name
        save_settings(data, self._path)
        return self._working

    def _on_apply(self):
        working = self._save_working()
        if working is None:
            return
        self.apply_btn.setEnabled(False)
        self._status_lbl.setText("applying...")
        self._status_lbl.repaint()           # a rebuild blocks this thread
        try:
            result = self._apply_cb(dict(working))
        except Exception as exc:
            self._status_lbl.setText(f"apply failed: {exc}")
            print(f"[ribbon] apply failed: {exc}", flush=True)
            raise
        finally:
            self.apply_btn.setEnabled(True)
        self._status_lbl.setText(str(result))
        # what is on screen is now what is saved
        self._loaded_ribbon = self._ribbon_subset(self._working)
        self._update_dirty()

    def _on_restart(self):
        if self._save_working() is None:
            return
        self._on_apply_restart()             # host stops flowgraph + re-execs