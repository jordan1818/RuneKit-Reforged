import logging
import sys
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThreadPool, QSettings
from PySide6.QtGui import QIcon

from runekit.app.view.browser_window import BrowserWindow


class AppWindow(BrowserWindow):
    pool: QThreadPool
    logger: logging.Logger
    app_icon: Optional[QIcon]

    def __init__(self, **kwargs):
        self.settings = QSettings()
        super().__init__(**kwargs)
        self.settings.setParent(self)

        # TODO: Hide from taskbar/group this as part of one big window?
        flags = Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Window
        if self.framed:
            flags |= Qt.WindowType.CustomizeWindowHint
        self.setWindowFlags(flags)

        self.pool = QThreadPool(parent=self)
        self.setWindowTitle(self.app.manifest["appName"])
        self.snap_to_game()
        self._setup_keep_above()

        self.logger = logging.getLogger(
            __name__
            + "."
            + self.__class__.__name__
            + "."
            + self.app.manifest["appName"]
        )

        self.browser.load(self.app.absolute_app_url)

        self.app_icon = self.app.host.app_store.icon(self.app.app_id)
        if self.app_icon:
            self.setWindowIcon(self.app_icon)

    def _setup_keep_above(self):
        """Ask the GameInstance backend for any extra always-on-top help
        it needs beyond the WindowStaysOnTopHint flag set above (see
        ROADMAP.md Phase 6). No-op on X11/macOS -- see
        GameInstance.keep_window_above()'s default implementation.

        winId() is called first to force creation of the underlying
        native QWindow (windowHandle() returns None until then), matching
        the same forcing already done for embed_window() in
        runekit/app/app.py's App.get_window().
        """
        self.winId()
        handle = self.windowHandle()
        if handle is None:
            return

        undo = self.app.game_instance.keep_window_above(handle)
        if undo is not None:
            self.destroyed.connect(lambda *_: undo())

    @property
    def framed(self) -> bool:
        return self.settings.value("settings/styledBorder", "true") == "true" and sys.platform != "darwin"

    def minimumSize(self) -> QSize:
        return QSize(self.app.manifest["minWidth"], self.app.manifest["minHeight"])

    def sizeHint(self) -> QSize:
        return QSize(
            self.app.manifest["defaultWidth"], self.app.manifest["defaultHeight"]
        )

    def maximumSize(self) -> QSize:
        return QSize(self.app.manifest["maxWidth"], self.app.manifest["maxHeight"])
