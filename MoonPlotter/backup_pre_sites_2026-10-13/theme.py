"""'Drafting table' light theme: warm paper panels, ink-blue accents, thin rules."""
from PyQt6.QtGui import QPalette, QColor, QFont

BG = '#EFEBE3'          # window / desk
PANEL = '#F7F4EE'       # sidebar
CARD = '#FFFDF9'        # inputs, canvases, cards
BORDER = '#D8D1C4'
RULE = '#E4DED3'
TEXT = '#2A2824'
MUTED = '#7C7568'
ACCENT = '#2F4B7C'      # ink blue
ACCENT_HOVER = '#3C5E98'
ACCENT_SOFT = '#DFE5EF'
SEPIA = '#9A6B2F'
WARN = '#A2452F'

QSS = f"""
QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow, QWidget#central {{ background: {BG}; }}
QWidget#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QWidget#sidebarBody, QWidget#footer {{ background: {PANEL}; }}
QWidget#footer {{ border-top: 1px solid {BORDER}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLabel#appTitle {{ font-size: 13pt; font-weight: 600; color: {ACCENT}; }}
QLabel#appSubtitle {{ color: {MUTED}; font-size: 9pt; }}
QLabel#hint {{ color: {MUTED}; font-size: 9pt; }}
QLabel#fieldLabel {{ color: {MUTED}; font-size: 9pt; }}
QLabel#value {{ color: {ACCENT}; font-weight: 600; min-width: 44px; }}
QLabel#intervalBadge {{ color: {SEPIA}; font-weight: 600; }}
QLabel#panelTitle {{ font-size: 9pt; font-weight: 700; color: {MUTED}; }}
QLabel#status {{ color: {MUTED}; font-style: italic; font-size: 9pt; }}

QToolButton#sectionHeader {{
    border: none; background: transparent; padding: 7px 2px;
    font-size: 9pt; font-weight: 700; color: {TEXT};
}}
QToolButton#sectionHeader:hover {{ color: {ACCENT}; }}
QFrame#sectionRule {{ background: {RULE}; max-height: 1px; min-height: 1px; border: none; }}

QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; }}

QPushButton {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 4px; padding: 5px 10px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; font-weight: 600; }}
QPushButton:disabled {{ color: #B4AD9F; border-color: {RULE}; }}
QPushButton#primary {{
    background: {ACCENT}; color: white; border: 1px solid {ACCENT}; font-weight: 600; padding: 8px;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; color: white; }}
QPushButton#primary:disabled {{ background: #9AA7BD; border-color: #9AA7BD; color: #EEF1F6; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; padding: 4px 8px; }}
QPushButton#ghost:hover {{ color: {ACCENT}; border-color: {BORDER}; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px; min-height: 20px;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #B9B0A1; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}

QSlider::groove:horizontal {{ height: 4px; background: {RULE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -5px 0; border-radius: 7px; background: {CARD}; border: 2px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_SOFT}; }}

QListWidget#layers {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 2px; }}
QListWidget#layers::item {{ padding: 5px 4px; border-bottom: 1px solid {RULE}; }}
QListWidget#layers::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

QProgressBar {{ border: none; background: {RULE}; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QToolTip {{ background: {TEXT}; color: {CARD}; border: none; padding: 5px; }}
QSplitter::handle {{ background: {BG}; width: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


_INDICATOR_QSS = """
QCheckBox::indicator, QListWidget#layers::indicator {{
    width: 14px; height: 14px; border: 1px solid #A79F90; border-radius: 3px; background: {card};
}}
QCheckBox::indicator:hover, QListWidget#layers::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked, QListWidget#layers::indicator:checked {{
    background: {accent}; border-color: {accent}; image: url("{check}");
}}
QCheckBox::indicator:disabled {{ background: {rule}; border-color: {rule}; }}
QRadioButton {{ spacing: 6px; margin-right: 10px; }}
QCheckBox {{ spacing: 7px; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid #A79F90; border-radius: 8px; background: {card};
}}
QRadioButton::indicator:hover {{ border-color: {accent}; }}
QRadioButton::indicator:checked {{ border: 4px solid {accent}; width: 8px; height: 8px; border-radius: 8px; }}
QRadioButton::indicator:disabled {{ background: {rule}; border-color: {rule}; }}
"""

_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
              '<path d="M3 7.4 L5.8 10 L11 4.2" fill="none" stroke="white" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _indicator_qss():
    """Check-mark image is written to a temp folder (Qt style sheets need a file URL)."""
    import os
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "topo_plotter_check.svg")
    try:
        with open(path, "w") as f:
            f.write(_CHECK_SVG)
    except OSError:
        pass
    return _INDICATOR_QSS.format(card=CARD, accent=ACCENT, rule=RULE, check=path.replace("\\", "/"))


def apply_theme(app):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(CARD))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(CARD))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor('#FFFFFF'))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(CARD))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    app.setPalette(pal)
    font = QFont("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(QSS + _indicator_qss())


def style_axes(fig, ax):
    """Matplotlib canvases blend into the cards."""
    fig.set_facecolor(CARD)
    for sp in ax.spines.values():
        sp.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.title.set_color(TEXT)
