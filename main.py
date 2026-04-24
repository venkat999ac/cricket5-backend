from cricket_app import CricketApp
from kivy.core.window import Window


if __name__ == "__main__":
    # On desktop/laptop, open at a phone-like size.
    # On Android/iOS, this is ignored.
    try:
        Window.size = (430, 820)  # width x height in pixels
    except Exception:
        pass

    CricketApp().run()