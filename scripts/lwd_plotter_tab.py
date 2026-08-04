#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lwd_plotter_tab.py  —  PyQt5 port of the LWD CSV plotter, packaged so it can be
embedded as a single widget (one tab) inside another PyQt5 application such as
the GNU Radio trx_ssb dashboard in main.py.

This is a direct port of lwd_plotter.py (originally PyQt6).  Only the Qt binding
and its enum namespacing changed:

    PyQt6.QtCore / PyQt6.QtWidgets   ->  PyQt5.QtCore / PyQt5.QtWidgets
    Qt.Orientation.Vertical          ->  Qt.Vertical
    Qt.CheckState.Checked            ->  Qt.Checked
    Qt.CheckState.Unchecked          ->  Qt.Unchecked
    Qt.ItemFlag.ItemIsUserCheckable  ->  Qt.ItemIsUserCheckable
    app.exec()                       ->  app.exec_()

The Smith-chart tab from the original has been removed; only the "Plots" and
"Compute" tabs are included.

Public API
----------
    LWDPlotterWidget(parent=None)
        A QWidget containing a QTabWidget with the "Plots" and "Compute" tabs,
        all sharing a single DataModel.  Drop it straight into a layout / tab.

Run standalone (PyQt5) for testing without GNU Radio:
    python lwd_plotter_tab.py
"""

from __future__ import annotations

import csv
import re
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter,
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

# Set this to e.g. ["elapsed_s", "amplitude_dB", "phase_deg"] to require/limit
# columns; leave None to read whatever is in the file.
EXPECTED_HEADERS = None

X_INDEX = "(row index)"

_PALETTE = [
    "#00ffcc", "#ff7040", "#4aa3ff", "#ffd24a", "#c479ff",
    "#7dff6b", "#ff5fa2", "#9fb4c0", "#ff9d4a", "#5be1ff",
]


# ══════════════════════════════════════════════════════════════════════════════
#   Data layer
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CSVData:
    path: str
    name: str
    headers: list
    columns: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    # Extra columns usable as an x-axis but NOT listed as plottable series
    # (used to carry a source file's elapsed_s into computed results).
    aux: dict = field(default_factory=dict)


@dataclass
class Series:
    """One numeric column belonging to one dataset."""
    dataset: CSVData
    header: str
    label: str          # display name (file-qualified when >1 file loaded)

    @property
    def y(self) -> np.ndarray:
        return self.dataset.columns[self.header]


def _to_float(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def load_csv(path: str, expected_headers=None) -> CSVData:
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"{path}: empty file")

    headers = [h.strip() for h in rows[0]]
    body = rows[1:]

    if expected_headers is not None:
        missing = [h for h in expected_headers if h not in headers]
        if missing:
            raise ValueError(f"{path}: missing expected column(s): {missing}")
        wanted = expected_headers
    else:
        wanted = headers

    data = CSVData(path=path, name=os.path.basename(path), headers=headers)
    for h in wanted:
        ci = headers.index(h)
        raw = [(r[ci] if ci < len(r) else "") for r in body]
        arr = np.array([_to_float(v) for v in raw], dtype=float)
        if np.isnan(arr).all():
            data.skipped.append(h)
        else:
            data.columns[h] = arr
    if not data.columns:
        raise ValueError(f"{path}: no numeric columns found")
    return data


class DataModel(QObject):
    """Holds loaded datasets; emits `changed` whenever they change."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.datasets = []
        # synthetic dataset holding derived (averaged / subtracted) columns
        self.computed = CSVData(path="", name="(computed)", headers=[])

    def load_paths(self, paths) -> list:
        errors = []
        for p in paths:
            try:
                self.datasets.append(load_csv(p, EXPECTED_HEADERS))
            except Exception as exc:                       # noqa: BLE001
                errors.append(f"{os.path.basename(p)}: {exc}")
        if paths:
            self.changed.emit()
        return errors

    def clear(self):
        self.datasets.clear()
        self.computed = CSVData(path="", name="(computed)", headers=[])
        self.changed.emit()

    # ── derived columns ─────────────────────────────────────────────────────
    def _all_column_names(self) -> set:
        names = set(self.computed.columns)
        for d in self.datasets:
            names |= set(d.columns)
        return names

    def add_computed(self, name: str, arr: np.ndarray,
                     source=None) -> str:
        """Register a derived column; returns the (uniquified) name used."""
        base = name.strip() or "computed"
        nm, i = base, 2
        existing = self._all_column_names()
        while nm in existing:
            nm = f"{base}_{i}"
            i += 1
        self.computed.columns[nm] = np.asarray(arr, dtype=float)
        self.computed.headers.append(nm)
        # carry the source's x-candidate columns so the result still plots
        # against e.g. elapsed_s instead of falling back to row index
        if source is not None:
            for h, col in list(source.columns.items()) + list(source.aux.items()):
                self.computed.aux.setdefault(h, col)
        self.changed.emit()
        return nm

    def clear_computed(self):
        self.computed = CSVData(path="", name="(computed)", headers=[])
        self.changed.emit()

    def series(self) -> list:
        multi = len(self.datasets) > 1
        out = []
        for d in self.datasets:
            for h in d.columns:
                label = f"{d.name}: {h}" if multi else h
                out.append(Series(d, h, label))
        # computed columns have unique, user-given names -> no prefix needed
        for h in self.computed.columns:
            out.append(Series(self.computed, h, h))
        return out

    def header_names(self) -> list:
        names = []
        for d in self.datasets:
            for h in d.columns:
                if h not in names:
                    names.append(h)
        for h in self.computed.columns:
            if h not in names:
                names.append(h)
        return names


def xy_for(dataset: CSVData, x_name: str, y: np.ndarray):
    """x/y arrays clipped to min length. x_name may be a header or X_INDEX."""
    if x_name == X_INDEX:
        x = np.arange(len(y), dtype=float)
    elif x_name in dataset.columns:
        x = dataset.columns[x_name]
    elif x_name in dataset.aux:                # computed result borrowing e.g. time
        x = dataset.aux[x_name]
    else:
        x = np.arange(len(y), dtype=float)
    n = min(len(x), len(y))
    return x[:n], y[:n]


# ══════════════════════════════════════════════════════════════════════════════
#   Domain-correct averaging / subtraction
# ══════════════════════════════════════════════════════════════════════════════

KINDS = ["Auto", "Linear", "dB", "Phase"]

# Naming convention for derived columns:  <name>_avg_<unit>
#   name : whatever the user typed
#   avg  : mandatory marker, so later steps can find averages
#   unit : the kind, so kind_of() infers it correctly downstream
AVG_TAG = "avg"
# Token written for each kind. Change LINEAR_UNIT to "W" if your linear
# columns really are watts; "lin" is neutral because a normalised ratio is
# linear too.
LINEAR_UNIT = "lin"
_UNIT_FOR_KIND = {"dB": "db", "Phase": "deg", "Linear": LINEAR_UNIT}

# Whole-token unit markers, checked from the end of the name so that the
# convention's trailing unit wins (e.g. "phase_db" is dB, not Phase).
_UNIT_TOKENS = {
    "db": "dB", "dbm": "dB", "dbfs": "dB", "dbv": "dB", "dbw": "dB",
    "deg": "Phase", "degs": "Phase", "degree": "Phase", "degrees": "Phase",
    "phase": "Phase", "rad": "Phase", "rads": "Phase",
    "lin": "Linear", "w": "Linear", "watt": "Linear", "watts": "Linear",
    "v": "Linear", "volt": "Linear", "volts": "Linear",
}


def _tokens(name: str) -> list:
    return [t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t]


def unit_token(kind: str) -> str:
    """The name token that records ``kind`` for later auto-detection."""
    return _UNIT_FOR_KIND.get(kind, LINEAR_UNIT)


def is_average(name: str) -> bool:
    """True if the column was produced by the Average step."""
    return AVG_TAG in _tokens(name)


def kind_of(name: str, override: str = "Auto") -> str:
    """Guess how a column should be combined from its name.

    Name-based only -- the data is never inspected -- so it is worth being
    careful about *how* the name is read:

    * whole tokens are matched before substrings, and the LAST unit token
      wins, so the "<name>_avg_<unit>" convention is authoritative and
      "phase_db" reads as dB rather than Phase;
    * the substring fallback requires a token to *start* with "db", so
      "feedback_amp" is no longer mistaken for a dB column.

    Anything with no recognisable unit falls back to Linear, which is the
    one genuinely dangerous case: averaging dB values arithmetically is
    several dB wrong.  Callers should surface the inferred kind rather
    than let it apply silently.
    """
    if override != "Auto":
        return override
    toks = _tokens(name)
    for t in reversed(toks):                 # trailing unit is the convention
        if t in _UNIT_TOKENS:
            return _UNIT_TOKENS[t]
    if any("phase" in t for t in toks):
        return "Phase"
    if any(t.startswith("db") for t in toks):
        return "dB"
    return "Linear"


def average_arrays(arrays, kind: str) -> np.ndarray:
    arrays = [np.asarray(a, float) for a in arrays if len(a)]
    if not arrays:
        return np.array([])
    n = min(len(a) for a in arrays)
    m = np.vstack([a[:n] for a in arrays])
    if kind == "dB":                           # mean of linear voltage, back to dB
        lin = 10.0 ** (m / 20.0)
        return 20.0 * np.log10(np.mean(lin, axis=0) + 1e-12)
    if kind == "Phase":                        # circular mean (unit-vector average)
        z = np.exp(1j * np.deg2rad(m))
        return np.rad2deg(np.angle(np.mean(z, axis=0)))
    return np.mean(m, axis=0)                   # plain arithmetic mean


def subtract_arrays(a: np.ndarray, b: np.ndarray, kind: str) -> np.ndarray:
    """data - memory, with no wrap artifacts or NaNs."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if kind == "Phase":                        # wrap difference into (-180, 180]
        return (a - b + 180.0) % 360.0 - 180.0
    return a - b                               # dB diff = ratio; linear diff


# ══════════════════════════════════════════════════════════════════════════════
#   Shared widgets / helpers
# ══════════════════════════════════════════════════════════════════════════════

class BaseTab(QWidget):
    TAB_NAME = "Tab"


class CheckList(QListWidget):
    """Checkable column list with real multi-select.

    The checkbox stays the persistent "is this included" state -- it is
    visible at a glance and survives the list being rebuilt.  Selection is
    the bulk-edit gesture on top of it: drag, shift-click and ctrl-click
    pick a range, then Space (or the Toggle button) flips every checkbox
    in that range at once, instead of clicking each small box.

    Item text carries the inferred kind for visibility; the real column
    label lives in Qt.UserRole, so every lookup goes through label_of()
    and the decoration can change without breaking callers.
    """

    checksChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setMinimumWidth(180)
        self.itemChanged.connect(lambda *_: self.checksChanged.emit())

    # ── keyboard ──────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            if self.selectedItems():
                self.toggle_selected()
                return
        super().keyPressEvent(event)

    # ── label <-> item ────────────────────────────────────────────────────
    @staticmethod
    def label_of(item) -> str:
        lbl = item.data(Qt.UserRole)
        return item.text() if lbl is None else lbl

    def _items(self):
        """Data rows only -- group headings carry no UserRole."""
        return [self.item(i) for i in range(self.count())
                if self.item(i).data(Qt.UserRole) is not None]

    def checked_labels(self) -> list:
        return [self.label_of(it) for it in self._items()
                if it.checkState() == Qt.Checked]

    def repopulate(self, labels, keep_checked=None, kind_getter=None,
                   group_getter=None):
        """Rebuild from ``labels``, preserving checks and selection.

        ``group_getter(label) -> str`` inserts a non-selectable heading
        before each block, so amplitudes, phases and axis columns stay
        together instead of interleaving file by file.  Headings carry no
        UserRole, which is how the bulk operations skip them.
        """
        if keep_checked is None:
            keep_checked = set(self.checked_labels())
        selected = {self.label_of(it) for it in self.selectedItems()}
        self.blockSignals(True)
        self.clear()
        current_group = None
        for lbl in labels:
            if group_getter is not None:
                g = group_getter(lbl)
                if g != current_group:
                    current_group = g
                    head = QListWidgetItem(f"── {g} ──")
                    head.setFlags(Qt.NoItemFlags)      # no check, no select
                    self.addItem(head)
            text = lbl
            if kind_getter is not None:
                text = f"{lbl}   [{kind_getter(lbl)}]"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, lbl)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if lbl in keep_checked else Qt.Unchecked)
            self.addItem(it)
            if lbl in selected:
                it.setSelected(True)
        self.blockSignals(False)

    # ── bulk edits ────────────────────────────────────────────────────────
    def _set_many(self, items, state):
        if not items:
            return
        self.blockSignals(True)
        for it in items:
            it.setCheckState(state)
        self.blockSignals(False)
        self.checksChanged.emit()

    def check_all(self):
        self._set_many(self._items(), Qt.Checked)

    def uncheck_all(self):
        self._set_many(self._items(), Qt.Unchecked)

    def invert(self):
        items = self._items()
        if not items:
            return
        self.blockSignals(True)
        for it in items:
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked
                             else Qt.Checked)
        self.blockSignals(False)
        self.checksChanged.emit()

    def toggle_selected(self):
        """Flip the selection as a block: if any is unchecked, check all."""
        sel = self.selectedItems()
        if not sel:
            return
        target = (Qt.Checked
                  if any(it.checkState() != Qt.Checked for it in sel)
                  else Qt.Unchecked)
        self._set_many(sel, target)


def _list_toolbar(lst: CheckList, on_axis_toggle=None) -> QHBoxLayout:
    """All / None / Invert / Toggle selected, wired to ``lst``.

    ``on_axis_toggle(bool)`` adds a checkbox that puts the time/distance
    columns back in the list; they are hidden by default because they are
    x-axes, not traces.
    """
    row = QHBoxLayout()
    row.setSpacing(4)
    for text, fn, tip in (
        ("All", lst.check_all, "Check every column"),
        ("None", lst.uncheck_all, "Uncheck every column"),
        ("Invert", lst.invert, "Flip every checkbox"),
        ("Toggle sel", lst.toggle_selected,
         "Flip the checkboxes of the selected rows.\n"
         "Select with drag, shift-click or ctrl-click; Space does the same."),
    ):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        row.addWidget(b)
    if on_axis_toggle is not None:
        cb = QCheckBox("axis cols")
        cb.setToolTip("Also list time / distance / index columns.\n"
                      "Hidden by default: they are x-axes, not traces.")
        cb.toggled.connect(on_axis_toggle)
        row.addWidget(cb)
    row.addStretch()
    return row


def _filter_combo() -> QComboBox:
    """Combo you can type into to filter, instead of scrolling a long list."""
    c = QComboBox()
    c.setEditable(True)
    c.setInsertPolicy(QComboBox.NoInsert)
    comp = c.completer()
    comp.setCompletionMode(QCompleter.PopupCompletion)
    comp.setFilterMode(Qt.MatchContains)
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    return c


def _fill_combo(combo: QComboBox, labels) -> None:
    """Repopulate a filter combo, keeping the previous choice if still there."""
    prev = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(list(labels))
    combo.setCurrentIndex(combo.findText(prev) if prev in labels else -1)
    if prev in labels:
        combo.setCurrentText(prev)
    else:
        combo.setEditText("")
    combo.blockSignals(False)


def _style_plot(pw: pg.PlotWidget):
    pw.setBackground("#0b0f0e")
    pw.showGrid(x=True, y=True, alpha=0.2)
    # Reserve padding inside the scene so axis labels/title aren't clipped
    # at the widget edge.
    pw.getPlotItem().layout.setContentsMargins(12, 12, 12, 12)


def export_plot_png(plot_widget: pg.PlotWidget, parent: QWidget):
    """Prompt for a path and save the plot widget as a PNG."""
    path, _ = QFileDialog.getSaveFileName(
        parent, "Save plot as PNG", "", "PNG image (*.png)"
    )
    if not path:
        return None
    if not path.lower().endswith(".png"):
        path += ".png"
    exporter = ImageExporter(plot_widget.getPlotItem())
    # render at ~2x the on-screen width for a crisper file
    try:
        w = int(plot_widget.getPlotItem().width() * 2) or 1600
        exporter.parameters()["width"] = w
    except Exception:
        pass
    exporter.export(path)
    return path


# column names that look like an x-axis (kept when exporting computed CSVs)
_AXIS_HINTS = ("time", "elapsed", "dist", "depth", "index", "sample", "freq")


def _looks_like_axis(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _AXIS_HINTS)


# Column lists are grouped in this order so amplitudes, phases and the
# time/distance axes stay together instead of interleaving per file.
GROUP_ORDER = ["Axis", "dB", "Phase", "Linear"]


def group_of(name: str) -> str:
    """Which block of the column list ``name`` belongs in."""
    if _looks_like_axis(name):
        return "Axis"
    return kind_of(name)


def sort_by_group(labels, key=None):
    """Stable sort into GROUP_ORDER, preserving file order within a group."""
    def rank(lbl):
        g = group_of(key(lbl) if key else lbl)
        return GROUP_ORDER.index(g) if g in GROUP_ORDER else len(GROUP_ORDER)
    return sorted(labels, key=rank)


# ══════════════════════════════════════════════════════════════════════════════
#   Tab 1 — one PlotPanel per chart, each with its own column picker
# ══════════════════════════════════════════════════════════════════════════════

class PlotPanel(QWidget):
    """A title + checkable column list on the left, a plot on the right."""

    NORM_MODES = ["None", "Shared peak", "Per-curve 0–1"]

    def __init__(self, model: DataModel, x_name_getter, on_remove, parent=None):
        super().__init__(parent)
        self._model = model
        self._get_x = x_name_getter
        self._series_by_label = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(6)

        # ── left column: title, checklist, normalize, remove ──────────────
        left = QVBoxLayout()
        left.setSpacing(4)
        self._title = QLineEdit("Plot")
        self._title.setPlaceholderText("Plot title")
        self._title.textChanged.connect(self._apply_title)
        left.addWidget(self._title)

        self._list = CheckList()
        self._list.setMaximumWidth(300)
        self._list.checksChanged.connect(self.replot)
        left.addWidget(self._list, stretch=1)
        self._show_axis = False
        left.addLayout(_list_toolbar(self._list, self._on_axis_toggle))

        norm_row = QHBoxLayout()
        norm_row.addWidget(QLabel("Norm:"))
        self._norm = QComboBox()
        self._norm.addItems(self.NORM_MODES)
        self._norm.currentTextChanged.connect(lambda *_: self.replot())
        norm_row.addWidget(self._norm, stretch=1)
        left.addLayout(norm_row)

        rm = QPushButton("Remove plot")
        rm.clicked.connect(lambda: on_remove(self))
        left.addWidget(rm)
        row.addLayout(left)

        # ── right column: view controls on top, plot below ────────────────
        right = QVBoxLayout()
        right.setSpacing(4)

        ctl = QHBoxLayout()
        ctl.setSpacing(4)
        home = QPushButton("Auto-fit")
        home.clicked.connect(self._auto_fit)
        ctl.addWidget(home)

        self._autoscale = QCheckBox("Autoscale")
        self._autoscale.setChecked(True)
        self._autoscale.toggled.connect(self._on_autoscale)
        ctl.addWidget(self._autoscale)

        ctl.addWidget(QLabel("X:"))
        self._xmin = self._num_field()
        self._xmax = self._num_field()
        ctl.addWidget(self._xmin)
        ctl.addWidget(self._xmax)
        ctl.addWidget(QLabel("Y:"))
        self._ymin = self._num_field()
        self._ymax = self._num_field()
        ctl.addWidget(self._ymin)
        ctl.addWidget(self._ymax)

        self._apply = QPushButton("Apply")
        self._apply.clicked.connect(self._apply_limits)
        ctl.addWidget(self._apply)

        save_png = QPushButton("Save PNG")
        save_png.clicked.connect(lambda: export_plot_png(self._pw, self))
        ctl.addWidget(save_png)
        ctl.addStretch()
        right.addLayout(ctl)

        self._pw = pg.PlotWidget()
        _style_plot(self._pw)
        self._legend = self._pw.addLegend(
            offset=(-10, 10),
            brush=pg.mkBrush(10, 15, 14, 180),
            pen=pg.mkPen("#1d3b38"),
        )
        right.addWidget(self._pw, stretch=1)
        row.addLayout(right, stretch=1)

        # crosshair + readout
        self._curves = []
        self._vline = pg.InfiniteLine(angle=90, movable=False,
                                      pen=pg.mkPen("#556", width=1))
        self._readout = pg.TextItem(anchor=(0, 1), color="#cde")
        self._pw.addItem(self._vline, ignoreBounds=True)
        self._pw.addItem(self._readout, ignoreBounds=True)
        self._pw.scene().sigMouseMoved.connect(self._on_mouse)

        self._set_manual_enabled(False)
        self.refresh_list()

    # ── small helpers ───────────────────────────────────────────────────────
    def _num_field(self) -> QLineEdit:
        e = QLineEdit()
        e.setFixedWidth(64)
        e.setPlaceholderText("auto")
        e.returnPressed.connect(self._apply_limits)
        return e

    @staticmethod
    def _parse(field: QLineEdit, default: float) -> float:
        try:
            return float(field.text())
        except (TypeError, ValueError):
            return default

    def _set_manual_enabled(self, on: bool):
        for w in (self._xmin, self._xmax, self._ymin, self._ymax, self._apply):
            w.setEnabled(on)

    def _apply_title(self, text):
        self._pw.setTitle(text, color="#cde", size="10pt")

    # ── view controls ───────────────────────────────────────────────────────
    def _auto_fit(self):
        """One-shot 'home': fit to data and reflect the result in the fields."""
        vb = self._pw.getViewBox()
        vb.autoRange()
        xr, yr = vb.viewRange()
        self._xmin.setText(f"{xr[0]:.4g}")
        self._xmax.setText(f"{xr[1]:.4g}")
        self._ymin.setText(f"{yr[0]:.4g}")
        self._ymax.setText(f"{yr[1]:.4g}")

    def _on_autoscale(self, on: bool):
        self._set_manual_enabled(not on)
        if on:
            self._pw.enableAutoRange(x=True, y=True)
        else:
            self._pw.disableAutoRange()
            # seed the empty fields with the current view, then apply
            if not self._xmin.text():
                self._auto_fit()
            self._apply_limits()

    def _apply_limits(self):
        if self._autoscale.isChecked():
            return
        vb = self._pw.getViewBox()
        xr, yr = vb.viewRange()
        xmin = self._parse(self._xmin, xr[0])
        xmax = self._parse(self._xmax, xr[1])
        ymin = self._parse(self._ymin, yr[0])
        ymax = self._parse(self._ymax, yr[1])
        if xmax > xmin:
            self._pw.setXRange(xmin, xmax, padding=0)
        if ymax > ymin:
            self._pw.setYRange(ymin, ymax, padding=0)

    # ── normalization ───────────────────────────────────────────────────────
    @staticmethod
    def _normalize(ys, mode: str):
        if mode == "Shared peak":
            peak = max((np.nanmax(np.abs(y)) for y in ys if len(y)), default=0.0)
            return [y / peak for y in ys] if peak > 0 else ys
        if mode == "Per-curve 0–1":
            out = []
            for y in ys:
                if len(y) == 0:
                    out.append(y)
                    continue
                lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
                out.append((y - lo) / (hi - lo) if hi > lo else np.zeros_like(y))
            return out
        return ys

    # ── data plumbing ───────────────────────────────────────────────────────
    def _on_axis_toggle(self, on: bool):
        self._show_axis = bool(on)
        self.refresh_list()

    def refresh_list(self):
        """Repopulate the checklist from the model, preserving checks by label."""
        series = self._model.series()
        self._series_by_label = {s.label: s for s in series}
        # List every series, not the deduplicated label set: two datasets can
        # legitimately produce the same label, and collapsing them would hide
        # a loaded file's columns entirely.
        labels = [s.label for s in series]
        if not self._show_axis:
            labels = [s.label for s in series
                      if not _looks_like_axis(s.header)]
        labels = sort_by_group(
            labels, key=lambda l: self._series_by_label[l].header)
        self._list.repopulate(
            labels, kind_getter=kind_of,
            group_getter=lambda l: group_of(self._series_by_label[l].header))
        self.replot()

    def replot(self):
        self._pw.clear()
        self._curves = []
        x_name = self._get_x()
        self._pw.setLabel("bottom", x_name)

        # gather checked series (x already truncated to min length)
        selected = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() != Qt.Checked:
                continue
            s = self._series_by_label.get(CheckList.label_of(item))
            if s is None:
                continue
            x, y = xy_for(s.dataset, x_name, s.y)
            selected.append((s, x, y))

        mode = self._norm.currentText()
        ys = self._normalize([y for _, _, y in selected], mode)
        self._pw.setLabel("left", "normalized" if mode != "None" else "")

        for ci, ((s, x, _), y) in enumerate(zip(selected, ys)):
            pen = pg.mkPen(_PALETTE[ci % len(_PALETTE)], width=1.5)
            self._curves.append(self._pw.plot(x, y, name=s.label, pen=pen))

        self._pw.addItem(self._vline, ignoreBounds=True)
        self._pw.addItem(self._readout, ignoreBounds=True)

        if self._autoscale.isChecked():
            self._pw.enableAutoRange(x=True, y=True)
        else:
            self._apply_limits()

    def _on_mouse(self, pos):
        vb = self._pw.getPlotItem().vb
        if not self._pw.sceneBoundingRect().contains(pos) or not self._curves:
            return
        mp = vb.mapSceneToView(pos)
        self._vline.setPos(mp.x())
        lines = [f"x = {mp.x():.4g}"]
        for c in self._curves:
            xd, yd = c.getData()
            if xd is None or len(xd) == 0:
                continue
            j = int(np.argmin(np.abs(xd - mp.x())))
            lines.append(f"{c.name()} = {yd[j]:.4g}")
        self._readout.setText("\n".join(lines))
        self._readout.setPos(mp.x(), mp.y())


class PlotTab(BaseTab):
    TAB_NAME = "Plots"

    def __init__(self, model: DataModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._panels = []
        model.changed.connect(self._on_data_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        ribbon = QHBoxLayout()
        ribbon.setSpacing(8)

        load = QPushButton("Load CSV…")
        load.clicked.connect(self._on_load)
        ribbon.addWidget(load)

        clear = QPushButton("Clear data")
        clear.clicked.connect(model.clear)
        ribbon.addWidget(clear)

        ribbon.addWidget(QLabel("X axis:"))
        self._x_combo = QComboBox()
        self._x_combo.addItem(X_INDEX)
        self._x_combo.currentTextChanged.connect(self._replot_all)
        ribbon.addWidget(self._x_combo)

        add = QPushButton("Add plot")
        add.clicked.connect(lambda: self._add_panel())
        ribbon.addWidget(add)

        ribbon.addStretch()
        self._status = QLabel("No file loaded.")
        ribbon.addWidget(self._status)
        root.addLayout(ribbon)

        self._split = QSplitter(Qt.Vertical)
        root.addWidget(self._split, stretch=1)

        self._add_panel()  # start with one

    # ribbon actions
    def _on_load(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if paths:
            errors = self._model.load_paths(paths)
            msg = f"Loaded {len(paths) - len(errors)} file(s)."
            if errors:
                msg += "  Errors: " + " | ".join(errors)
            self._status.setText(msg)

    def _current_x(self) -> str:
        return self._x_combo.currentText()

    def _add_panel(self):
        panel = PlotPanel(self._model, self._current_x, self._remove_panel)
        panel._title.setText(f"Plot {len(self._panels) + 1}")
        self._panels.append(panel)
        self._split.addWidget(panel)

    def _remove_panel(self, panel: PlotPanel):
        if len(self._panels) <= 1:
            return  # keep at least one
        self._panels.remove(panel)
        panel.setParent(None)
        panel.deleteLater()

    def _replot_all(self, *_):
        for p in self._panels:
            p.replot()

    def _on_data_changed(self):
        prev = self._x_combo.currentText()
        names = self._model.header_names()
        self._x_combo.blockSignals(True)
        self._x_combo.clear()
        self._x_combo.addItem(X_INDEX)
        self._x_combo.addItems(names)
        # default to the first real column; preserve an existing real-column pick
        if prev != X_INDEX and prev in names:
            self._x_combo.setCurrentText(prev)
        elif names:
            self._x_combo.setCurrentText(names[0])
        self._x_combo.blockSignals(False)
        for p in self._panels:
            p.refresh_list()


# ══════════════════════════════════════════════════════════════════════════════
#   Tab 2 — Compute (averaging + data − memory), correct per-domain math
# ══════════════════════════════════════════════════════════════════════════════

class ComputeTab(BaseTab):
    TAB_NAME = "Compute"

    def __init__(self, model: DataModel, parent=None):
        super().__init__(parent)
        self._model = model
        model.changed.connect(self._on_data_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        ribbon = QHBoxLayout()
        load = QPushButton("Load CSV…")
        load.clicked.connect(self._on_load)
        ribbon.addWidget(load)
        clr = QPushButton("Clear computed")
        clr.clicked.connect(model.clear_computed)
        ribbon.addWidget(clr)
        save = QPushButton("Save computed CSV…")
        save.clicked.connect(self._save_csv)
        ribbon.addWidget(save)
        ribbon.addStretch()
        self._status = QLabel("Build averages and differences from your columns.")
        ribbon.addWidget(self._status)
        root.addLayout(ribbon)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, stretch=1)

        # ── left: inputs (checkable) used by the Average box ──────────────
        left = QVBoxLayout()
        left.addWidget(QLabel(
            "Columns  —  drag / shift-click / ctrl-click to select, "
            "Space to toggle:"))
        self._inputs = CheckList()
        self._inputs.setMinimumWidth(240)
        left.addWidget(self._inputs, stretch=1)
        self._show_axis = False
        left.addLayout(_list_toolbar(self._inputs, self._on_axis_toggle))
        body.addLayout(left)

        # ── right: Average + Subtract boxes ───────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(10)

        # Average box
        avg_box = QGroupBox("Average checked columns")
        avg = QVBoxLayout(avg_box)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Kind:"))
        self._avg_kind = QComboBox()
        self._avg_kind.addItems(KINDS)
        r1.addWidget(self._avg_kind)
        r1.addStretch()
        avg.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Name:"))
        self._avg_name = QLineEdit()
        self._avg_name.setPlaceholderText("e.g. noDUT   ->  noDUT_avg_db")
        self._avg_name.setToolTip(
            "Base name only. The result is stored as <name>_avg_<unit>:\n"
            "the 'avg' tag is what the Subtract-averages box filters on,\n"
            "and the unit token makes Auto detect the kind correctly later.")
        r2.addWidget(self._avg_name)
        avg.addLayout(r2)
        avg_btn = QPushButton("Create average →")
        avg_btn.clicked.connect(self._make_average)
        avg.addWidget(avg_btn)
        right.addWidget(avg_box)

        # Subtract box
        sub_box = QGroupBox("Subtract  (A − B,  e.g. DUT − noDUT)")
        sub = QVBoxLayout(sub_box)
        sa = QHBoxLayout()
        sa.addWidget(QLabel("A:"))
        self._a_combo = _filter_combo()
        sa.addWidget(self._a_combo, stretch=1)
        sub.addLayout(sa)
        sb = QHBoxLayout()
        sb.addWidget(QLabel("B:"))
        self._b_combo = _filter_combo()
        sb.addWidget(self._b_combo, stretch=1)
        sub.addLayout(sb)
        sk = QHBoxLayout()
        sk.addWidget(QLabel("Kind:"))
        self._sub_kind = QComboBox()
        self._sub_kind.addItems(KINDS)
        sk.addWidget(self._sub_kind)
        sk.addStretch()
        sub.addLayout(sk)
        sn = QHBoxLayout()
        sn.addWidget(QLabel("Name:"))
        self._sub_name = QLineEdit()
        self._sub_name.setPlaceholderText("e.g. DUT_minus_noDUT")
        sn.addWidget(self._sub_name)
        sub.addLayout(sn)
        sub_btn = QPushButton("Create A − B →")
        sub_btn.clicked.connect(self._make_subtract)
        sub.addWidget(sub_btn)
        right.addWidget(sub_box)

        # Subtract-averages box: same operation, but the pickers only offer
        # columns carrying the "_avg_" tag, so the common baseline-removal
        # step is a short list instead of every column ever loaded.
        savg_box = QGroupBox("Subtract averages  (A_avg − B_avg)")
        savg = QVBoxLayout(savg_box)
        aa = QHBoxLayout()
        aa.addWidget(QLabel("A:"))
        self._aavg_combo = _filter_combo()
        aa.addWidget(self._aavg_combo, stretch=1)
        savg.addLayout(aa)
        bb = QHBoxLayout()
        bb.addWidget(QLabel("B:"))
        self._bavg_combo = _filter_combo()
        bb.addWidget(self._bavg_combo, stretch=1)
        savg.addLayout(bb)
        ak = QHBoxLayout()
        ak.addWidget(QLabel("Kind:"))
        self._savg_kind = QComboBox()
        self._savg_kind.addItems(KINDS)
        ak.addWidget(self._savg_kind)
        ak.addStretch()
        savg.addLayout(ak)
        an = QHBoxLayout()
        an.addWidget(QLabel("Name:"))
        self._savg_name = QLineEdit()
        self._savg_name.setPlaceholderText("blank = A_minus_B")
        an.addWidget(self._savg_name)
        savg.addLayout(an)
        savg_btn = QPushButton("Create A_avg − B_avg →")
        savg_btn.clicked.connect(self._make_subtract_avg)
        savg.addWidget(savg_btn)
        self._savg_hint = QLabel("")
        self._savg_hint.setWordWrap(True)
        savg.addWidget(self._savg_hint)
        right.addWidget(savg_box)

        right.addStretch()
        body.addLayout(right, stretch=1)

        self._on_data_changed()

    # ── plumbing ──────────────────────────────────────────────────────────
    def _on_load(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if paths:
            self._model.load_paths(paths)

    def _series_map(self) -> dict:
        return {s.label: s for s in self._model.series()}

    def _on_axis_toggle(self, on: bool):
        self._show_axis = bool(on)
        self._on_data_changed()

    def _on_data_changed(self):
        smap = self._series_map()
        labels = list(smap)
        if not self._show_axis:
            # elapsed_s and friends are x-axes; averaging or subtracting
            # them is never what is wanted, so keep them out of the way.
            labels = [l for l in labels if not _looks_like_axis(smap[l].header)]
        labels = sort_by_group(labels, key=lambda l: smap[l].header)
        self._inputs.repopulate(
            labels, kind_getter=kind_of,
            group_getter=lambda l: group_of(smap[l].header))

        for combo in (self._a_combo, self._b_combo):
            _fill_combo(combo, labels)

        # The averages-only pickers see just the "_avg_" columns.
        avg_labels = [l for l in labels if is_average(l)]
        for combo in (self._aavg_combo, self._bavg_combo):
            _fill_combo(combo, avg_labels)
        self._savg_hint.setText(
            "No averaged columns yet — create one above."
            if not avg_labels else
            f"{len(avg_labels)} averaged column(s) available.")

    def _checked_series(self) -> list:
        smap = self._series_map()
        return [smap[l] for l in self._inputs.checked_labels() if l in smap]

    # ── operations ────────────────────────────────────────────────────────
    def _make_average(self):
        chosen = self._checked_series()
        if len(chosen) < 2:
            self._status.setText("Check at least two columns to average.")
            return

        override = self._avg_kind.currentText()
        if override == "Auto":
            # Averaging mixes the columns together, so one rule is applied to
            # all of them.  Previously that rule came from the first checked
            # column alone, which silently circular-averaged dB data (or
            # power-averaged degrees) when the selection was mixed.
            kinds = {kind_of(s.header) for s in chosen}
            if len(kinds) > 1:
                groups = {}
                for s in chosen:
                    groups.setdefault(kind_of(s.header), []).append(s.label)
                detail = "; ".join(f"{k}: {', '.join(v)}"
                                   for k, v in sorted(groups.items()))
                self._status.setText(
                    f"Mixed kinds in the selection ({detail}). Average them "
                    f"separately, or set Kind explicitly to force one rule.")
                return
            kind = kinds.pop()
        else:
            kind = override

        result = average_arrays([s.y for s in chosen], kind)
        base = self._avg_name.text().strip() or "columns"
        # <name>_avg_<unit>: the avg tag is what the Subtract-averages box
        # filters on, and the unit token keeps Auto correct downstream.
        name = f"{base}_{AVG_TAG}_{unit_token(kind)}"
        used = self._model.add_computed(name, result, source=chosen[0].dataset)
        self._status.setText(
            f"Created '{used}' = {kind} mean of {len(chosen)} columns.")

    def _series_from(self, combo) -> object:
        """Look a series up from a filter combo, tolerating stray whitespace."""
        smap = self._series_map()
        txt = combo.currentText().strip()
        return smap.get(txt)

    def _subtract(self, a, b, kind_combo, name_field, tag: str):
        """Shared body of the two subtract boxes."""
        if a is None or b is None:
            self._status.setText(f"{tag}: pick both A and B.")
            return
        override = kind_combo.currentText()
        if override == "Auto":
            ka, kb = kind_of(a.header), kind_of(b.header)
            if ka != kb:
                self._status.setText(
                    f"{tag}: A is {ka} but B is {kb}. Set Kind explicitly if "
                    f"you really mean to subtract them.")
                return
            kind = ka
        else:
            kind = override
        result = subtract_arrays(a.y, b.y, kind)
        name = name_field.text().strip() or f"{a.header}_minus_{b.header}"
        used = self._model.add_computed(name, result, source=a.dataset)
        self._status.setText(
            f"Created '{used}' = ({a.label}) − ({b.label})  [{kind}].")

    def _make_subtract(self):
        self._subtract(self._series_from(self._a_combo),
                       self._series_from(self._b_combo),
                       self._sub_kind, self._sub_name, "Subtract")

    def _make_subtract_avg(self):
        self._subtract(self._series_from(self._aavg_combo),
                       self._series_from(self._bavg_combo),
                       self._savg_kind, self._savg_name, "Subtract averages")

    def _save_csv(self):
        comp = self._model.computed
        if not comp.columns:
            self._status.setText("Nothing computed to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save computed CSV", "", "CSV files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        # carried x-axis columns first (elapsed_s, distance_m, ...), then results
        cols = {}
        for h, arr in comp.aux.items():
            if _looks_like_axis(h):
                cols[h] = arr
        for h, arr in comp.columns.items():
            cols[h] = arr
        headers = list(cols)
        n = max(len(a) for a in cols.values())
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for i in range(n):
                w.writerow([
                    f"{cols[h][i]:.6f}" if i < len(cols[h]) else ""
                    for h in headers
                ])
        self._status.setText(
            f"Saved {len(comp.columns)} computed column(s) → {os.path.basename(path)}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#   Embeddable widget — the whole plotter as a single tab
# ══════════════════════════════════════════════════════════════════════════════

# Tabs included, in order. Smith chart intentionally omitted.
TABS = [PlotTab, ComputeTab]


class LWDPlotterWidget(QWidget):
    """Self-contained LWD plotter (Plots + Compute) for embedding as one tab.

    Builds its own internal QTabWidget and shares a single DataModel across the
    sub-tabs, so a CSV loaded in one is immediately available in the other.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = DataModel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        for tab_cls in TABS:
            self.tabs.addTab(tab_cls(self.model), tab_cls.TAB_NAME)
        layout.addWidget(self.tabs)


# ══════════════════════════════════════════════════════════════════════════════
#   Standalone runner (PyQt5) — handy for testing without GNU Radio
# ══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LWD Plotter")
        self.resize(1200, 760)
        self.setCentralWidget(LWDPlotterWidget())


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
