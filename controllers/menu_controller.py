# controllers/menu_controller.py
from __future__ import annotations

from kivy.animation import Animation


class MenuController:
    """
    Drawer controller.
    We CLOSE immediately (no animation) to avoid overlay blocking touches
    after navigation into scoring screen.

    Overlay behavior is controlled in KV (size 0,0 when disabled).
    """

    def __init__(self, app):
        self.app = app

    def _drawer(self):
        return self.app.root_widget.ids.drawer

    def _sm(self):
        return self.app.root_widget.ids.sm

    def set_menu_closed(self):
        d = self._drawer()
        d.x = -d.width

    def is_menu_open(self) -> bool:
        d = self._drawer()
        return d.x >= 0

    def open_menu(self):
        d = self._drawer()
        Animation.cancel_all(d, "x")
        Animation(x=0, duration=0.18).start(d)

    def close_menu(self):
        # ✅ IMPORTANT: close immediately so overlay disables immediately
        d = self._drawer()
        Animation.cancel_all(d, "x")
        d.x = -d.width

    def toggle_menu(self):
        self.close_menu() if self.is_menu_open() else self.open_menu()

    def go(self, screen_name: str):
        sm = self._sm()
        if screen_name in sm.screen_names:
            sm.current = screen_name
        self.close_menu()

    def on_window_resize(self, *_):
        d = self._drawer()
        if not self.is_menu_open():
            d.x = -d.width