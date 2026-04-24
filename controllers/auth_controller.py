# controllers/auth_controller.py
from __future__ import annotations

import threading
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from auth_manager import AuthError


class AuthController:
    """
    Screen names:
      - login
      - register
      - forgot_password
      - profile
    """

    def __init__(self, app):
        self.app = app
        # Ensure remember flag exists on app
        if not hasattr(self.app, "auth_remember"):
            self.app.auth_remember = False

    # ---------------- Helpers ----------------
    def _sm(self):
        return self.app.sm

    def _get(self, screen_name: str):
        try:
            return self._sm().get_screen(screen_name)
        except Exception:
            return None

    def _set_lbl(self, screen_name: str, lbl_id: str, msg: str):
        scr = self._get(screen_name)
        if not scr:
            return
        try:
            if lbl_id in scr.ids:
                scr.ids[lbl_id].text = msg
        except Exception:
            pass

    def _apply_logged_in_state(self):
        # ✅ Now fast: auth.get_current_user() returns cached session user (no HTTP)
        u = self.app.auth.get_current_user() if self.app.auth else None
        try:
            self.app.is_logged_in = bool(self.app.auth.is_logged_in()) if self.app.auth else False
        except Exception:
            self.app.is_logged_in = False
        self.app.logged_in_user_name = str((u or {}).get("name", "") or "").strip() if u else ""

    def _run_bg(self, fn, on_ok=None, on_err=None):
        """
        Run fn() in background thread. Schedule callbacks on UI thread.

        IMPORTANT FIX:
        - Capture exception object into default arg for lambda
          because Python clears exception variable after except block.
        """
        def _worker():
            try:
                res = fn()
                if on_ok:
                    Clock.schedule_once(lambda _dt, res=res: on_ok(res), 0)
            except Exception as e:
                if on_err:
                    err = e
                    Clock.schedule_once(lambda _dt, err=err: on_err(err), 0)

        threading.Thread(target=_worker, daemon=True).start()

    # ---------------- Navigation ----------------
    def check_auto_login(self):
        self._apply_logged_in_state()
        if self.app.is_logged_in:
            def _do():
                return self.app.auth.refresh_me()

            def _ok(_res):
                self._apply_logged_in_state()
                self.app.set_header_title("Cricket")
                self._sm().current = "home"

            def _err(_e):
                try:
                    self.app.auth.logout_local()
                except Exception:
                    try:
                        self.app.auth.logout()
                    except Exception:
                        pass
                self._apply_logged_in_state()
                self.go_login()

            self._run_bg(_do, _ok, _err)
        else:
            self.go_login()

    def go_login(self):
        self.app.set_header_title("Login")
        self._set_lbl("login", "login_status", "")
        self._sm().current = "login"

    def go_register(self):
        self.app.set_header_title("Register")
        self._set_lbl("register", "register_status", "")
        self._sm().current = "register"

    def go_forgot_password(self):
        self.app.set_header_title("Forgot Password")
        self._set_lbl("forgot_password", "forgot_status", "")
        scr = self._get("forgot_password")
        if scr:
            try:
                scr.ids.forgot_email.text = ""
            except Exception:
                pass
        self._sm().current = "forgot_password"

    def go_profile(self):
        if not self.require_login():
            return
        self.app.set_header_title("My Profile")
        self._load_profile()
        self._sm().current = "profile"

    # ---------------- Gate ----------------
    def require_login(self) -> bool:
        self._apply_logged_in_state()
        if self.app.is_logged_in:
            return True
        self.go_login()
        return False

    # ---------------- Login/Register actions ----------------
    def login_submit(self):
        """
        Background login with proper error handling and 'remember' support.
        """
        scr = self._get("login")
        if not scr:
            return

        email = scr.ids.login_email.text if "login_email" in scr.ids else ""
        pw = scr.ids.login_password.text if "login_password" in scr.ids else ""
        remember = bool(getattr(self.app, "auth_remember", False))

        # Basic client-side validation
        if not (email or "").strip():
            self._set_lbl("login", "login_status", "Enter email")
            return
        if not pw:
            self._set_lbl("login", "login_status", "Enter password")
            return

        self._set_lbl("login", "login_status", "Please wait...")

        def _do():
            # Return server response dict (or may raise)
            return self.app.auth.login(email, pw)

        def _ok(res):
            # res can be a dict or any value returned by auth.login
            try:
                if not isinstance(res, dict):
                    # Unexpected structure
                    self._set_lbl("login", "login_status", "Unexpected server response")
                    return

                if not res.get("ok"):
                    # Show server-provided error (if any)
                    errmsg = res.get("error") or res.get("message") or "Login failed"
                    self._set_lbl("login", "login_status", str(errmsg))
                    return

                # Success: res contains token and user (if backend follows convention)
                token = res.get("token", "") or ""
                user = res.get("user", {}) or {}

                # Explicitly save session according to remember flag if AuthManager supports it.
                try:
                    # Prefer save_session signature (token, user, remember)
                    if hasattr(self.app.auth, "save_session"):
                        try:
                            self.app.auth.save_session(token, user, remember=remember)
                        except TypeError:
                            # older save_session without remember param
                            self.app.auth.save_session(token, user)
                    else:
                        # fallback: nothing
                        pass
                except Exception:
                    pass

                # Update app state and navigate
                self._apply_logged_in_state()
                self.app.set_header_title("Cricket")

                # Clear sensitive fields when remember disabled
                try:
                    if not remember:
                        scr.ids.login_password.text = ""
                        scr.ids.login_email.text = ""
                except Exception:
                    pass

                # Navigate to home
                try:
                    self._sm().current = "home"
                except Exception:
                    pass

            except Exception as e:
                # Defensive fallback
                self._set_lbl("login", "login_status", str(e))

        def _err(e):
            # Display exception message (network / unexpected)
            msg = str(e) if e else "Login error"
            self._set_lbl("login", "login_status", msg)

        self._run_bg(_do, _ok, _err)

    def register_submit(self):
        scr = self._get("register")
        if not scr:
            return

        name = scr.ids.register_name.text if "register_name" in scr.ids else ""
        email = scr.ids.register_email.text if "register_email" in scr.ids else ""
        pw1 = scr.ids.register_password.text if "register_password" in scr.ids else ""
        pw2 = scr.ids.register_confirm.text if "register_confirm" in scr.ids else ""

        if (pw1 or "") != (pw2 or ""):
            self._set_lbl("register", "register_status", "Passwords do not match.")
            return

        self._set_lbl("register", "register_status", "Please wait...")

        def _do():
            return self.app.auth.register(name, email, pw1)

        def _ok(_res):
            self._apply_logged_in_state()
            self.app.set_header_title("Cricket")
            self._sm().current = "home"

        def _err(e):
            self._set_lbl("register", "register_status", str(e))

        self._run_bg(_do, _ok, _err)

    # ---------------- Forgot password ----------------
    def forgot_submit(self):
        scr = self._get("forgot_password")
        if not scr:
            return

        email = scr.ids.forgot_email.text if "forgot_email" in scr.ids else ""
        self._set_lbl("forgot_password", "forgot_status", "Sending reset link...")

        def _do():
            self.app.auth.forgot_password(email)
            return True

        def _ok(_):
            self._set_lbl(
                "forgot_password",
                "forgot_status",
                "If the email is registered, a reset link has been sent."
            )

        def _err(e):
            self._set_lbl("forgot_password", "forgot_status", str(e))

        self._run_bg(_do, _ok, _err)

    # ---------------- Profile actions ----------------
    def _load_profile(self):
        u = self.app.auth.get_current_user() if self.app.auth else None
        if not u:
            return
        scr = self._get("profile")
        if not scr:
            return
        try:
            scr.ids.profile_email.text = str(u.get("email", "") or "")
            scr.ids.profile_name.text = str(u.get("name", "") or "")
            scr.ids.profile_created.text = str(u.get("created_at", "") or "")
            scr.ids.profile_status.text = ""
            scr.ids.profile_pwd_status.text = ""
            scr.ids.profile_old_password.text = ""
            scr.ids.profile_new_password.text = ""
            scr.ids.profile_new_confirm.text = ""
        except Exception:
            pass

    def profile_save(self):
        if not self.require_login():
            return
        scr = self._get("profile")
        if not scr:
            return

        name = scr.ids.profile_name.text if "profile_name" in scr.ids else ""
        self._set_lbl("profile", "profile_status", "Saving...")

        def _do():
            return self.app.auth.update_profile(name)

        def _ok(_res):
            self._apply_logged_in_state()
            self._load_profile()
            self._set_lbl("profile", "profile_status", "Profile saved.")

        def _err(e):
            self._set_lbl("profile", "profile_status", str(e))

        self._run_bg(_do, _ok, _err)

    def profile_change_password(self):
        if not self.require_login():
            return
        scr = self._get("profile")
        if not scr:
            return

        old_pw = scr.ids.profile_old_password.text if "profile_old_password" in scr.ids else ""
        new_pw = scr.ids.profile_new_password.text if "profile_new_password" in scr.ids else ""
        new2 = scr.ids.profile_new_confirm.text if "profile_new_confirm" in scr.ids else ""

        if new_pw != new2:
            self._set_lbl("profile", "profile_pwd_status", "New passwords do not match.")
            return

        self._set_lbl("profile", "profile_pwd_status", "Changing password...")

        def _do():
            self.app.auth.change_password(old_pw, new_pw)
            return True

        def _ok(_):
            self._set_lbl("profile", "profile_pwd_status", "Password updated.")
            try:
                scr.ids.profile_old_password.text = ""
                scr.ids.profile_new_password.text = ""
                scr.ids.profile_new_confirm.text = ""
            except Exception:
                pass

        def _err(e):
            self._set_lbl("profile", "profile_pwd_status", str(e))

        self._run_bg(_do, _ok, _err)

    def profile_logout(self):
        # ✅ Instant UI logout (no waiting for network)
        token = ""
        try:
            s = self.app.auth.get_session()
            token = (s.token or "") if s else ""
        except Exception:
            token = ""

        try:
            # preferred: clear local session (logout_local)
            try:
                self.app.auth.logout_local()
            except Exception:
                # fallback
                try:
                    self.app.auth.clear_session()
                except Exception:
                    pass
        except Exception:
            pass

        self._apply_logged_in_state()
        self.go_login()

        # best-effort remote logout in background
        def _do_remote():
            if token:
                return self.app.auth.logout_remote(token)
            return {"ok": True, "detail": "No token"}

        self._run_bg(_do_remote, on_ok=None, on_err=None)

    def profile_delete_account(self):
        if not self.require_login():
            return

        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        root.add_widget(Label(
            text="Delete account permanently?",
            color=(0.10, 0.12, 0.16, 1),
            size_hint_y=None,
            height=dp(26),
        ))

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel = Button(text="Cancel")
        delete = Button(
            text="Delete",
            background_normal="",
            background_color=(0.90, 0.18, 0.22, 1),
            color=(1, 1, 1, 1),
        )
        btn_row.add_widget(cancel)
        btn_row.add_widget(delete)
        root.add_widget(btn_row)

        pop = Popup(title="Delete Account", content=root, size_hint=(0.92, None), height=dp(220), auto_dismiss=False)

        def _do_delete(*_):
            pop.dismiss()

            def _do():
                self.app.auth.delete_account()
                return True

            def _ok(_):
                self._apply_logged_in_state()
                self.go_login()

            def _err(e):
                self._set_lbl("profile", "profile_status", str(e))

            self._run_bg(_do, _ok, _err)

        cancel.bind(on_release=lambda *_: pop.dismiss())
        delete.bind(on_release=_do_delete)
        pop.open()