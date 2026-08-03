#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui_kit.py
=========

Controls the dashboard color palette, the global
Qt stylesheet, and the widget helpers used by every panel.

Style tweaks here propagates to all panels.

Public names (imported explicitly by the panels):
    palette constants  - _BG, _PANEL, _BORDER, _TEAL, _TEAL_DIM,
                         _ORANGE, _ORG_DIM, _TEXT, _TEXT_DIM
    _STYLESHEET        - app-wide QSS string
    _heading()         - section-heading QLabel
    _auto_toggle()     - small checkable AUTO button
    _configure_pg_plot - apply palette to a pyqtgraph PlotWidget
    _splitter()        - themed QSplitter
"""

from PyQt5 import Qt
from PyQt5.QtWidgets import QWidget, QLabel, QPushButton, QSplitter
import pyqtgraph as pg


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

def configure_plot_backend(antialias: bool = False, opengl: bool = False):
    """Set the pyqtgraph global draw options once, at startup.

    Antialiasing is the single most expensive option for line plots: Qt's
    raster painter pays for it per segment, so it scales with the number
    of points drawn.  Off by default (pyqtgraph's own default) because the
    spectra draw thousands of segments per frame.
    """
    pg.setConfigOptions(antialias=bool(antialias), useOpenGL=bool(opengl))


def decimate_for_display(pw: pg.PlotWidget):
    """Draw at most about one point per pixel column, peak-preserving.

    'peak' mode keeps the min and max of each bin it collapses, so a
    narrow spur is never decimated away -- it just costs one column
    instead of hundreds of points.  With setClipToView the cost of a
    trace stops depending on the FFT size or the span at all, and starts
    depending only on how wide the plot is on screen.
    """
    pw.setDownsampling(auto=True, mode='peak')
    pw.setClipToView(True)


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
