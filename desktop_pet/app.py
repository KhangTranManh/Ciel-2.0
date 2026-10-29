"""Transparent desktop-pet shell backed by Ciel's normal AgentLoop workflow.

Artwork is deliberately external: see asset_manifest.json and README.md.  This module
does not make direct model-provider calls; every command enters the existing Ciel core.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from html import escape
from pathlib import Path
from typing import Any

try:
    from PyQt6.QtCore import QObject, QPoint, QThread, QTimer, Qt, pyqtSignal
    from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import (
        QApplication, QLineEdit, QMenu, QMessageBox, QSystemTrayIcon, QTextBrowser,
        QVBoxLayout, QWidget,
    )
except ImportError as exc:  # Keep normal Ciel imports usable without desktop extras.
    raise SystemExit(
        "Desktop pet needs PyQt6. Install it with: "
        "./myenv/Scripts/python.exe -m pip install -r requirements-desktop.txt"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
PET_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PET_DIR / "asset_manifest.json"
SETTINGS_PATH = ROOT / "ciel_data" / "desktop_pet.json"


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(fallback)
    except (OSError, json.JSONDecodeError):
        return dict(fallback)


def load_manifest() -> dict[str, Any]:
    return _load_json(MANIFEST_PATH, {
        "sprite_sheet": "assets/ciel_sprite.png",
        "grid": {"columns": 4, "rows": 2},
        "sequences": {"idle": [1, 2, 3, 4], "action": [5, 6, 7, 8]},
        "render": {"height_px": 160, "frame_ms": 120, "reply_display_seconds": 12},
    })


class CielWorker(QThread):
    """Keeps model and tool work off the Qt event thread."""

    completed = pyqtSignal(str)

    def __init__(self, ciel, message: str):
        super().__init__()
        self.ciel = ciel
        self.message = message

    def run(self) -> None:
        try:
            result = self.ciel.run_step(self.message)
        except Exception as exc:  # The UI must remain usable after one failed request.
            result = f"Ciel could not complete that request: {type(exc).__name__}: {exc}"
        self.completed.emit(str(result))


class ApprovalBridge(QObject):
    """Turns a Ciel safety callback in a worker into a main-thread modal prompt."""

    requested = pyqtSignal(str, str, object, object, object)

    def ask(self, tool_name: str, preview: str, tool_args: dict) -> bool:
        done = threading.Event()
        outcome = {"approved": False}
        self.requested.emit(tool_name, preview, tool_args, outcome, done)
        done.wait()
        return bool(outcome["approved"])


class InputPanel(QLineEdit):
    submitted = pyqtSignal(str)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setPlaceholderText("Ask Ciel…  (Enter to send, Esc to close)")
        self.setFixedSize(360, 44)
        self.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,248); color: #1d2433; "
            "border: 2px solid #89c9ff; border-radius: 20px; padding: 9px 16px; "
            "font: 13px 'Segoe UI'; } QLineEdit:focus { border-color: #4388c5; }"
        )
        self.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        message = self.text().strip()
        if message:
            self.clear()
            self.hide()
            self.submitted.emit(message)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class Bubble(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.text = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_text(self, text: str, seconds: int) -> None:
        self.text = text
        self.resize(340, 130)
        self.timer.start(max(1, seconds) * 1000)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(250, 252, 255, 242))
        painter.setPen(QColor(110, 155, 205, 210))
        painter.drawRoundedRect(1, 1, self.width() - 2, self.height() - 18, 15, 15)
        painter.setPen(QColor(28, 41, 60))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(14, 12, self.width() - 28, self.height() - 38,
                         int(Qt.TextFlag.TextWordWrap), self.text)
        painter.end()


class ChatHistory(QWidget):
    """A persistent, scrollable transcript opened by double-clicking the pet."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Ciel conversation")
        self.resize(440, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setStyleSheet(
            "QTextBrowser { background: #161d2a; color: #edf4ff; border: 1px solid #3e5a7b; "
            "border-radius: 10px; padding: 10px; font: 11pt 'Segoe UI'; }"
        )
        layout.addWidget(self.transcript)

    def append(self, speaker: str, text: str) -> None:
        colour = "#91cdfd" if speaker == "You" else "#d8e8ff"
        self.transcript.append(
            f'<p style="margin: 0 0 12px 0;"><b style="color:{colour};">'
            f'{escape(speaker)}</b><br>{escape(str(text)).replace(chr(10), "<br>")}</p>'
        )
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())


class PetWindow(QWidget):
    def __init__(self, ciel, manifest: dict[str, Any], bridge: ApprovalBridge):
        super().__init__()
        self.ciel = ciel
        self.manifest = manifest
        self.bridge = bridge
        self.settings = _load_json(SETTINGS_PATH, {"x": 1100, "y": 620})
        self.frames = self._load_frames()
        self.sequence = "walk"
        self.frame_index = 0
        self.worker: CielWorker | None = None
        self.drag_start: QPoint | None = None
        self.drag_offset: QPoint | None = None
        self.dragging = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Ciel desktop pet")
        if self.frames:
            self.resize(self.frames[0].size())
        else:
            self.resize(160, 160)
        self.move(int(self.settings.get("x", 1100)), int(self.settings.get("y", 620)))

        self.bubble = Bubble()
        self.input = InputPanel()
        self.input.submitted.connect(self.send_message)
        self.history = ChatHistory()
        self._restore_history()
        self._setup_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._set_sequence("walk")
        self.bridge.requested.connect(self.show_approval)
        self.show()

    def _load_frames(self) -> list[QPixmap]:
        rel = self.manifest.get("sprite_sheet", "assets/ciel_sprite.png")
        sheet = QPixmap(str(PET_DIR / rel))
        if sheet.isNull():
            separate = self.manifest.get("separate_frames", {})
            pattern = str(separate.get("pattern", ""))
            count = int(separate.get("count", 0))
            frames = [QPixmap(str(PET_DIR / pattern.format(index=index)))
                      for index in range(1, count + 1)] if pattern else []
            return [] if not frames or any(frame.isNull() for frame in frames) else frames
        grid = self.manifest.get("grid", {})
        columns, rows = int(grid.get("columns", 4)), int(grid.get("rows", 2))
        if columns < 1 or rows < 1:
            return []
        target = int(self.manifest.get("render", {}).get("height_px", 160))
        frames = []
        for row in range(rows):
            top = round(row * sheet.height() / rows)
            bottom = round((row + 1) * sheet.height() / rows)
            for col in range(columns):
                # Use proportional boundaries rather than integer division.  Generated
                # sheets may be one or two pixels short of a perfectly even grid.
                left = round(col * sheet.width() / columns)
                right = round((col + 1) * sheet.width() / columns)
                frames.append(sheet.copy(left, top, right - left, bottom - top).scaledToHeight(
                    target, Qt.TransformationMode.SmoothTransformation))
        return frames

    def _frames_for_current_sequence(self) -> list[QPixmap]:
        sequence = self.manifest.get("sequences", {}).get(self.sequence, [1])
        return [self.frames[index - 1] for index in sequence
                if isinstance(index, int) and 0 < index <= len(self.frames)]

    def _tick(self) -> None:
        frames = self._frames_for_current_sequence()
        if not frames:
            return
        self.frame_index += 1
        if self.frame_index >= len(frames):
            self.frame_index = 0
        self.update()

    def _set_sequence(self, sequence: str) -> None:
        """Switch movement states and use the cadence declared for that state."""
        if sequence not in self.manifest.get("sequences", {}):
            sequence = "walk"
        self.sequence = sequence
        self.frame_index = 0
        render = self.manifest.get("render", {})
        interval = int(render.get(f"{sequence}_frame_ms", 550))
        self.timer.start(max(40, interval))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        frames = self._frames_for_current_sequence()
        if frames:
            painter.drawPixmap(0, 0, frames[min(self.frame_index, len(frames) - 1)])
        else:
            painter.setBrush(QColor(68, 92, 124, 230))
            painter.setPen(QColor(156, 205, 255))
            painter.drawRoundedRect(self.rect().adjusted(4, 4, -4, -4), 22, 22)
            painter.setPen(QColor("white"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter),
                             "Ciel\nAdd sprite sheet")
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint()
            self.drag_offset = self.drag_start - self.frameGeometry().topLeft()
            self.dragging = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_offset:
            point = event.globalPosition().toPoint()
            self.dragging = self.dragging or (point - self.drag_start).manhattanLength() > 6
            if self.dragging:
                if self.sequence != "run":
                    self._set_sequence("run")
                self.move(point - self.drag_offset)
                self._place_bubble()
                self._place_input()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self._save_position()
                self._set_sequence("walk")
            else:
                self.show_input()
            self.drag_start = self.drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.input.hide()
            self.show_history()

    def _restore_history(self) -> None:
        """Show durable Ciel conversation memory as well as messages from this run."""
        try:
            messages = list(self.ciel.core.chat_history.messages)[-50:]
        except Exception:
            messages = []
        for message in messages:
            role = getattr(message, "type", "")
            speaker = "You" if role in {"human", "user"} else "Ciel"
            content = getattr(message, "content", "")
            if content:
                self.history.append(speaker, str(content))

    def show_history(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        x = min(max(screen.left() + 8, self.x() + self.width() + 8),
                screen.right() - self.history.width() - 8)
        y = min(max(screen.top() + 8, self.y()),
                screen.bottom() - self.history.height() - 8)
        self.history.move(x, y)
        self.history.show()
        self.history.raise_()

    def show_input(self) -> None:
        self._place_input()
        self.input.show()
        self.input.setFocus()

    def _place_input(self) -> None:
        """Keep the compact command field attached to the pet while it is dragged."""
        screen = QApplication.primaryScreen().availableGeometry()
        x = min(max(screen.left() + 8, self.x()), screen.right() - self.input.width() - 8)
        y = self.y() + self.height() + 8
        if y + self.input.height() > screen.bottom() - 8:
            y = self.y() - self.input.height() - 8
        self.input.move(x, max(screen.top() + 8, y))

    def send_message(self, message: str) -> None:
        if self.worker and self.worker.isRunning():
            self.show_reply("Ciel is still working on the previous request.", 3)
            return
        self.show_reply("Ciel is thinking…", 60)
        self.history.append("You", message)
        self._set_sequence("action")
        self.worker = CielWorker(self.ciel, message)
        self.worker.completed.connect(self.on_response)
        self.worker.start()

    def on_response(self, response: str) -> None:
        # The sword sequence represents active work, so it loops for the whole request
        # and stops only once Ciel has a real response to show.
        self._set_sequence("walk")
        self.history.append("Ciel", response)
        seconds = int(self.manifest.get("render", {}).get("reply_display_seconds", 12))
        self.show_reply(response, seconds)

    def show_reply(self, text: str, seconds: int) -> None:
        self.bubble.show_text(text, seconds)
        self._place_bubble()

    def _place_bubble(self) -> None:
        if self.bubble.isVisible():
            self.bubble.move(max(8, self.x() - 80), max(8, self.y() - self.bubble.height() - 8))

    def show_approval(self, tool: str, preview: str, args: dict, outcome: dict, done) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(f"Ciel safety check — {tool}")
        box.setText(preview)
        approve = box.addButton("Approve once", QMessageBox.ButtonRole.AcceptRole)
        always = box.addButton("Approve this tool this session", QMessageBox.ButtonRole.ActionRole)
        decline = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is always:
            outcome["approved"] = bool(self.ciel.core.permissions.grant_for_session(tool))
        else:
            outcome["approved"] = clicked is approve
        done.set()

    def _setup_tray(self) -> None:
        icon = QIcon(self.frames[0]) if self.frames else QIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Ciel desktop pet")
        menu = QMenu()
        toggle = QAction("Show / hide", self)
        toggle.triggered.connect(self._toggle_visible)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(toggle)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _toggle_visible(self) -> None:
        self.setVisible(not self.isVisible())
        if not self.isVisible():
            self.bubble.hide()
            self.input.hide()

    def _save_position(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps({"x": self.x(), "y": self.y()}), encoding="utf-8")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_position()
        event.ignore()
        self.hide()
        self.bubble.hide()
        self.input.hide()
        self.history.hide()


def run() -> int:
    from core.agent_loop import AgentLoop
    from core.proactive_setup import setup_proactive
    from core.scheduler import CielScheduler

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ciel = AgentLoop()
    scheduler = CielScheduler()
    scheduler.cleanse_callback = ciel.core._brain_cleanse
    bridge = ApprovalBridge()
    gate_off = os.getenv("DISABLE_SAFETY_GATE", "").casefold() in {"1", "true", "yes"}
    ciel.core.confirm_callback = (lambda _n, _p, _a: True) if gate_off else bridge.ask
    setup_proactive(ciel, scheduler, include_cli=False)
    scheduler.start_background()
    window = PetWindow(ciel, load_manifest(), bridge)
    app.aboutToQuit.connect(window._save_position)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
