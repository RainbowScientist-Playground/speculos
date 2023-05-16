from PyQt5.QtWidgets import QApplication, QWidget, QMainWindow
from PyQt5.QtGui import QPainter, QColor, QPixmap
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSocketNotifier, QSettings, QRect
from typing import List, Optional, Union

from speculos.abstractions import Display, DisplayArgs, IODevice, ServerArgs, TextEvent
from . import bagl
from . import nbgl
from .display import FrameBuffer, COLORS, MODELS
from .readerror import ReadError

BUTTON_LEFT = 1
BUTTON_RIGHT = 2
DEFAULT_WINDOW_X = 10
DEFAULT_WINDOW_Y = 10


class PaintWidget(QWidget):
    def __init__(self, parent, model, pixel_size, vnc=None):
        super().__init__(parent)
        self.fb = FrameBuffer(model)
        self.pixel_size = pixel_size
        self.mPixmap = QPixmap()
        self.vnc = vnc

    def paintEvent(self, event):
        if self.fb.pixels:
            pixmap = QPixmap(self.size() / self.pixel_size)
            pixmap.fill(Qt.white)
            painter = QPainter(pixmap)
            painter.drawPixmap(0, 0, self.mPixmap)
            self._redraw(painter)
            self.mPixmap = pixmap
            self.fb.pixels = {}

        qp = QPainter(self)
        copied_pixmap = self.mPixmap
        if self.pixel_size != 1:
            # Only call scaled if needed.
            copied_pixmap = self.mPixmap.scaled(
                self.mPixmap.width() * self.pixel_size,
                self.mPixmap.height() * self.pixel_size)
        qp.drawPixmap(0, 0, copied_pixmap)

    def update(self,
               x: Optional[int] = None,
               y: Optional[int] = None,
               w: Optional[int] = None,
               h: Optional[int] = None) -> bool:
        if x and y and w and h:
            super().update(QRect(x, y, w, h))
        else:
            super().update()
        return self.fb.pixels != {}

    def _redraw(self, qp):
        for (x, y), color in self.fb.pixels.items():
            qp.setPen(QColor.fromRgb(color))
            qp.drawPoint(x, y)

        if self.vnc:
            self.vnc.redraw(self.fb.pixels)

        self.fb.screenshot_update_pixels()

    def draw_point(self, x, y, color):
        return self.fb.draw_point(x, y, color)

    def take_screenshot(self):
        return self.fb.take_screenshot()

    def update_screenshot(self):
        return self.fb.screenshot_update_pixels()

    def update_public_screenshot(self):
        return self.fb.update_public_screenshot()

    def get_public_screenshot(self):
        return self.fb.get_public_screenshot()


class App(QMainWindow):
    def __init__(self, qt_app: QApplication, display: DisplayArgs, server: ServerArgs) -> None:
        super().__init__()
        self.setWindowTitle('Ledger %s Emulator' % MODELS[display.model].name)

        self.seph = server.seph
        self._width, self._height = MODELS[display.model].screen_size
        self.pixel_size = display.pixel_size
        self.box_position_x, self.box_position_y = MODELS[display.model].box_position
        box_size_x, box_size_y = MODELS[display.model].box_size

        # If the position of the window has been saved in the settings, restore
        # it.
        settings = QSettings("ledger", "speculos")
        # Take in account multiple screens and their geometry:
        current_screen_x = qt_app.primaryScreen().geometry().x()
        current_screen_y = qt_app.primaryScreen().geometry().y()
        if display.x is None:
            window_x = settings.value("window_x", current_screen_x + DEFAULT_WINDOW_X, int)
        else:
            window_x = display.x
        if display.y is None:
            window_y = settings.value("window_y", current_screen_y + DEFAULT_WINDOW_Y, int)
        else:
            window_y = display.y
        window_width = (self._width + box_size_x) * display.pixel_size
        window_height = (self._height + box_size_y) * display.pixel_size

        # Be sure Window is FULLY visible in one of the available screens:
        window_is_visible = False
        for screen in qt_app.screens():
            x1 = screen.geometry().x()
            y1 = screen.geometry().y()
            x2 = x1 + screen.geometry().width() - 1
            y2 = y1 + screen.geometry().height() - 1

            if window_x >= x1 and window_y >= y1 and (window_x + window_width - 1) <= x2 and \
               (window_y + window_height - 1) <= y2:
                window_is_visible = True
                break   # No need to check other screens

        # If the window is not FULLY visible, force default coordinates on current screen:
        if not window_is_visible:
            print("Window is NOT FULLY visible => using default coordinates in current screen.")
            window_x = current_screen_x + DEFAULT_WINDOW_X
            window_y = current_screen_y + DEFAULT_WINDOW_Y

        self.setGeometry(window_x, window_y, window_width, window_height)
        self.setFixedSize(window_width, window_height)

        flags: Union[Qt.WindowFlags, Qt.WindowType] = Qt.FramelessWindowHint
        if display.ontop:
            flags |= Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor.fromRgb(COLORS[display.color]))
        self.setPalette(p)

        # Add paint widget and paint
        self.m = PaintWidget(self, display.model, display.pixel_size, server.vnc)
        self.m.move(self.box_position_x * display.pixel_size, self.box_position_y * display.pixel_size)
        self.m.resize(self._width * display.pixel_size, self._height * display.pixel_size)

        self._screen = Screen(self, display, server)

        self.setWindowIcon(QIcon('mcu/icon.png'))

        self.show()


    def screen_update(self) -> bool:
        return self._screen.screen_update()

    def keyPressEvent(self, event):
        self._screen._key_event(event, True)

    def keyReleaseEvent(self, event):
        self._screen._key_event(event, False)

    def _get_x_y(self):
        x = self.mouse_offset.x() // self.pixel_size - (self.box_position_x + 1)
        y = self.mouse_offset.y() // self.pixel_size - (self.box_position_y + 1)
        return x, y

    def mousePressEvent(self, event):
        '''Get the mouse location.'''

        self.mouse_offset = event.pos()

        x, y = self._get_x_y()
        if x >= 0 and x < self._width and y >= 0 and y < self._height:
            self.seph.handle_finger(x, y, True)

        QApplication.setOverrideCursor(Qt.DragMoveCursor)

    def mouseReleaseEvent(self, event):
        x, y = self._get_x_y()
        if x >= 0 and x < self._width and y >= 0 and y < self._height:
            self.seph.handle_finger(x, y, False)
        QApplication.restoreOverrideCursor()

    def mouseMoveEvent(self, event):
        '''Move the window.'''

        x = event.globalX()
        y = event.globalY()
        x_w = self.mouse_offset.x()
        y_w = self.mouse_offset.y()
        self.move(x - x_w, y - y_w)

    def closeEvent(self, event):
        '''
        Called when the window is closed. We save the current window position to
        the settings file in order to restore it upon next speculos execution.
        '''
        settings = QSettings("ledger", "speculos")
        settings.setValue("window_x", self.pos().x())
        settings.setValue("window_y", self.pos().y())


class Screen(Display):
    def __init__(self, app: App, display: DisplayArgs, server: ServerArgs) -> None:
        self.app = app
        super().__init__(display, server)
        self._init_notifiers(server)
        if display.model != "stax":
            self.bagl = bagl.Bagl(app.m, MODELS[display.model].screen_size, display.model)
        else:
            self.nbgl = nbgl.NBGL(app.m, MODELS[display.model].screen_size, display.force_full_ocr,
                                  display.disable_tesseract)
        self.seph = server.seph

    def klass_can_read(self, klass, s) -> None:
        try:
            klass.can_read(s, self)

        # This exception occur when can_read have no more data available
        except ReadError:
            self.app.close()

    def add_notifier(self, klass: IODevice) -> None:
        n = QSocketNotifier(klass.fileno, QSocketNotifier.Read, self.app)
        n.activated.connect(lambda s: self.klass_can_read(klass, s))
        assert klass.fileno not in self.notifiers
        self.notifiers[klass.fileno] = n

    def enable_notifier(self, fd: int, enabled: bool = True) -> None:
        n = self.notifiers[fd]
        n.setEnabled(enabled)

    def remove_notifier(self, fd: int) -> None:
        # just in case
        self.enable_notifier(fd, False)
        n = self.notifiers.pop(fd)
        n.disconnect()

    def _key_event(self, event, pressed) -> None:
        key = event.key()
        if key in [Qt.Key_Left, Qt.Key_Right]:
            buttons = {Qt.Key_Left: BUTTON_LEFT, Qt.Key_Right: BUTTON_RIGHT}
            # forward this event to seph
            self.seph.handle_button(buttons[key], pressed)
        elif key == Qt.Key_Down:
            self.seph.handle_button(BUTTON_LEFT, pressed)
            self.seph.handle_button(BUTTON_RIGHT, pressed)
        elif key == Qt.Key_Q and not pressed:
            self.app.close()

    def display_status(self, data) -> List[TextEvent]:
        ret = self.bagl.display_status(data)
        if MODELS[self.model].name == 'blue':
            self.screen_update()    # Actually, this method doesn't work
        return ret

    def display_raw_status(self, data) -> None:
        self.bagl.display_raw_status(data)
        if MODELS[self.model].name == 'blue':
            self.screen_update()    # Actually, this method doesn't work

    def screen_update(self) -> bool:
        return self.bagl.refresh()

    def run(self) -> None:
        pass


class QtScreen:
    def __init__(self, display: DisplayArgs, server: ServerArgs) -> None:
        self.app = QApplication([])
        self.app_widget = App(self.app, display, server)
        self.m = self.app_widget.m

    def run(self):
        self.app.exec_()
        self.app.quit()
