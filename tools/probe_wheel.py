"""What does this machine's wheel hardware actually send? — the M92.1 probe.

`PdfView._is_mouse_detent` has to tell a **discrete mouse wheel** (which M92.1 gives a defined step)
from a **precision device** (a touchpad, or a hi-res wheel in free-spin mode, both left on Qt's own
arithmetic because touchpad scrolling is out of M92's scope). On Windows it does that by granularity
— a notched wheel reports exactly ±120 per click, a touchpad reports fractions of it — because
`pixelDelta` is null on Windows for every device.

That is an inference from how the platform is documented to behave, not a measurement, and one
better discriminator may exist: `QWheelEvent.device().type()`. Whether Qt's Windows plugin fills that
in as `TouchPad` can only be answered by real hardware, which is what this window is for.

Run it, scroll each device over the window, and read the table::

    .venv/Scripts/python.exe tools/probe_wheel.py

Press Esc or close the window to print the summary.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from viewer.pdf_view import _WHEEL_LINE_PX, _WHEEL_NOTCH


class WheelProbe(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KlarPDF — wheel probe (M92.1)")
        self.resize(900, 460)
        self._rows: list[tuple] = []
        layout = QVBoxLayout(self)
        self._label = QLabel(
            "Scroll over this window.\n\n"
            "  1. a few clicks of the MOUSE WHEEL\n"
            "  2. a two-finger swipe on the TOUCHPAD\n"
            "  3. if your mouse has a free-spin mode, switch it on and spin\n\n"
            "Then close the window (or press Esc) for the summary."
        )
        self._label.setFont(QFont("Consolas", 10))
        self._label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._label)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def wheelEvent(self, event) -> None:
        device = event.device()
        dev_type = device.type().name if device is not None else "?"
        dev_name = device.name() if device is not None else "?"
        angle, pixel = event.angleDelta(), event.pixelDelta()
        whole = angle.y() != 0 and angle.y() % _WHEEL_NOTCH == 0
        self._rows.append((dev_type, dev_name, angle.y(), angle.x(),
                           pixel.y(), pixel.isNull(), whole, event.phase().name))
        recent = self._rows[-16:]
        head = f"{'device':<14}{'angleY':>8}{'angleX':>8}{'pixelY':>8}{'pxNull':>8}{'x120':>6}  phase"
        body = "\n".join(
            f"{r[0]:<14}{r[2]:>8}{r[3]:>8}{r[4]:>8}{str(r[5]):>8}{str(r[6]):>6}  {r[7]}"
            for r in recent)
        self._label.setText(f"{len(self._rows)} events (last 16)\n\n{head}\n{body}")
        event.accept()

    def closeEvent(self, event) -> None:
        self.summary()
        super().closeEvent(event)

    def summary(self) -> None:
        print(f"\n{'=' * 78}\nwheel probe — {len(self._rows)} events\n{'=' * 78}")
        if not self._rows:
            print("no wheel events captured")
            return
        by_device: dict[tuple, list] = {}
        for row in self._rows:
            by_device.setdefault((row[0], row[1]), []).append(row)
        for (dev_type, dev_name), rows in by_device.items():
            deltas = sorted({r[2] for r in rows})
            whole = sum(1 for r in rows if r[6])
            print(f"\ndevice type : {dev_type}")
            print(f"device name : {dev_name}")
            print(f"events      : {len(rows)}")
            print(f"angleDelta.y values seen: {deltas[:12]}{' …' if len(deltas) > 12 else ''}")
            print(f"whole multiples of {_WHEEL_NOTCH}: {whole}/{len(rows)}"
                  f"  -> _is_mouse_detent() would say "
                  f"{'MOUSE (new M92.1 step)' if whole == len(rows) else 'PRECISION (Qt path)'}")
            print(f"pixelDelta null in all events: {all(r[5] for r in rows)}")
            print(f"phases seen : {sorted({r[7] for r in rows})}")
        print(f"\nwheelScrollLines = {QApplication.wheelScrollLines()}   "
              f"_WHEEL_LINE_PX = {_WHEEL_LINE_PX:g}")
        print(f"one detent at 100% zoom would move "
              f"{QApplication.wheelScrollLines() * _WHEEL_LINE_PX:g} px\n")


def main() -> None:
    app = QApplication(sys.argv)
    probe = WheelProbe()
    probe.show()
    app.exec()


if __name__ == "__main__":
    main()
