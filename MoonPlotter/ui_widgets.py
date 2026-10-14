"""Reusable widgets: collapsible sidebar sections and the preview layer list."""
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QEvent, QCoreApplication
from PyQt6.QtGui import QPixmap, QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QCheckBox, QFrame,
                             QListWidget, QListWidgetItem, QLabel, QComboBox, QAbstractSpinBox, QSlider)


class WheelGuard(QObject):
    """Stops the mouse wheel from changing drop-downs, number boxes and sliders inside a scroll area:
    the wheel scrolls the panel instead. Click a control (or use the keyboard) to change its value."""
    GUARDED = (QComboBox, QAbstractSpinBox, QSlider)

    def __init__(self, scroll_area):
        super().__init__(scroll_area)
        self.scroll = scroll_area

    def _guarded(self, obj):
        body = self.scroll.widget()
        w = obj
        while isinstance(w, QWidget) and w is not body:
            if isinstance(w, self.GUARDED):
                return body is not None and body.isAncestorOf(w)
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and self._guarded(obj):
            QCoreApplication.sendEvent(self.scroll.verticalScrollBar(), event)
            return True
        return False

    @classmethod
    def install(cls, scroll_area):
        guard = cls(scroll_area)
        QCoreApplication.instance().installEventFilter(guard)
        return guard


class CollapsibleSection(QWidget):
    """Sidebar section with an arrow header. Optionally carries an 'enabled' checkbox in the
    header (used for the Contours / Fill layers)."""
    enabled_changed = pyqtSignal(bool)

    def __init__(self, title, expanded=True, checkable=False, checked=True, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton()
        self.toggle.setObjectName("sectionHeader")
        self.toggle.setText(title.upper())
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.toggled.connect(self._on_toggle)
        head.addWidget(self.toggle)
        head.addStretch()
        self.enable_box = None
        if checkable:
            self.enable_box = QCheckBox("On")
            self.enable_box.setChecked(checked)
            self.enable_box.setToolTip(f"Include the {title.lower()} layer in the plot")
            self.enable_box.toggled.connect(self.enabled_changed)
            head.addWidget(self.enable_box)
        outer.addLayout(head)

        rule = QFrame()
        rule.setObjectName("sectionRule")
        outer.addWidget(rule)

        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 8, 4, 12)
        self.body_layout.setSpacing(6)
        self.body.setVisible(expanded)
        outer.addWidget(self.body)

    def _on_toggle(self, on):
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.body.setVisible(on)

    def add(self, w):
        self.body_layout.addWidget(w)
        return w

    def add_layout(self, lay):
        self.body_layout.addLayout(lay)
        return lay

    def is_enabled(self):
        return self.enable_box.isChecked() if self.enable_box is not None else True


def swatch_icon(colors, size=14):
    """Small round swatch (one or more pen colours) for list items."""
    pm = QPixmap(size * max(1, len(colors)) + 2 * (len(colors) - 1), size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, c in enumerate(colors):
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        p.setBrush(QColor(c))
        p.drawEllipse(i * (size + 2) + 1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class LayerPanel(QWidget):
    """Checkable list of plot layers. Emits visibility_changed(layer_key, visible)."""
    visibility_changed = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        title = QLabel("LAYERS")
        title.setObjectName("panelTitle")
        v.addWidget(title)
        self.list = QListWidget()
        self.list.setObjectName("layers")
        self.list.itemChanged.connect(self._changed)
        v.addWidget(self.list, 1)
        self.hint = QLabel("Hidden layers are left out of the SVG and G-code.")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        order_title = QLabel("PLOT ORDER \u00b7 EST. TIME")
        order_title.setObjectName("panelTitle")
        v.addSpacing(6)
        v.addWidget(order_title)
        self.order = QLabel("—")
        self.order.setObjectName("hint")
        self.order.setWordWrap(True)
        self.order.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.order)
        self.total = QLabel("")
        self.total.setObjectName("intervalBadge")
        v.addWidget(self.total)
        self._updating = False

    def set_layers(self, layers, visibility):
        """layers: list of (key, label, [colours], stroke_count)."""
        self._updating = True
        self.list.clear()
        for key, label, colors, count in layers:
            it = QListWidgetItem(swatch_icon(colors), f"{label}\n{count:,} strokes")
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            it.setCheckState(Qt.CheckState.Checked if visibility.get(key, True) else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self._updating = False

    def set_order(self, rows, total=""):
        """rows: list of (pen name, time string)."""
        if not rows:
            self.order.setText("—")
            self.total.setText("")
            return
        html = "<table width='100%' cellspacing='0' cellpadding='1'>" + "".join(
            f"<tr><td>{i + 1}. {n}</td><td align='right'>{t}</td></tr>" for i, (n, t) in enumerate(rows)) + "</table>"
        self.order.setText(html)
        self.total.setText(f"Total \u2248 {total}" if total else "")

    def _changed(self, item):
        if self._updating:
            return
        self.visibility_changed.emit(item.data(Qt.ItemDataRole.UserRole),
                                     item.checkState() == Qt.CheckState.Checked)
