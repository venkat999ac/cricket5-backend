# auth_manager.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional, Dict, Any

import requests


class AuthError(Exception):
    """
    Backward-compatible exception expected by existing code:
        from auth_manager import AuthError
    """
    pass


@dataclass
class Session:
    token: str
    user: Dict[str, Any]
    remember: bool = True

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Session":
        return Session(
            token=str(d.get("token", "")),
            user=(d.get("user") or {}),
            remember=bool(d.get("remember", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"token": self.token, "user": self.user or {}, "remember": bool(self.remember)}


class AuthManager:
    """
    Auth + session persistence + backend API wrapper.

    Session stored in:
        <user_data_dir>/session.json

    Tournament cloud APIs supported:
      - POST /tournament/upload
      - POST /tournament/unshare
      - GET  /tournament/search?tid=XXXXXX
      - POST /tournament/delete-cloud
    """

    def __init__(
        self,
        user_data_dir: str,
        backend_url: str,
        app_api_key: str,
        ssl_verify: bool = True,
        ca_bundle_path: Optional[str] = None,
    ):
        self.user_data_dir = user_data_dir
        self.backend_url = (backend_url or "").rstrip("/")
        self.app_api_key = (app_api_key or "").strip()

        self.ssl_verify = bool(ssl_verify)
        self.ca_bundle_path = ca_bundle_path

        os.makedirs(self.user_data_dir, exist_ok=True)
        self._session_path = os.path.join(self.user_data_dir, "session.json")
        self._session: Optional[Session] = None

        # ✅ NEW: reuse HTTP connections (reduces login/logout latency)
        self._http = requests.Session()

        # ✅ NEW: configurable timeouts
        # connect timeout small, read timeout moderate
        self._connect_timeout = float(os.environ.get("CRICKET5_HTTP_CONNECT_TIMEOUT", "4"))
        self._read_timeout = float(os.environ.get("CRICKET5_HTTP_READ_TIMEOUT", "15"))

    # ============================================================
    # Session persistence
    # ============================================================
    def load_session(self) -> Optional[Session]:
        """
        Loads session from disk if present and if the saved 'remember' flag is true.
        If the file exists but remember==False it will not auto-load (session was saved only for in-memory use).
        """
        try:
            if not os.path.exists(self._session_path):
                self._session = None
                return None
            with open(self._session_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                self._session = None
                return None
            s = Session.from_dict(d)
            # Only set _session if token present and remember is True
            if s.token and s.remember:
                self._session = s
                return self._session
            # If remember is False, treat as no persisted session
            self._session = None
            return None
        except Exception:
            self._session = None
            return None

    def save_session(self, token: str, user: Dict[str, Any], remember: bool = True) -> None:
        """
        Save session in memory and optionally to disk.
        - remember=True  -> write session.json containing token,user,remember
        - remember=False -> keep session in memory only (no file write). This means app restart will require login.
        """
        self._session = Session(token=(token or ""), user=(user or {}), remember=bool(remember))
        if not remember:
            # Do not persist to disk
            return
        try:
            with open(self._session_path, "w", encoding="utf-8") as f:
                json.dump(self._session.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            # Keep session in memory even if disk write fails
            pass

    def clear_session(self) -> None:
        self._session = None
        try:
            if os.path.exists(self._session_path):
                os.remove(self._session_path)
        except Exception:
            pass

    def get_session(self) -> Optional[Session]:
        """
        Return in-memory session if set; otherwise attempt to load persisted session.
        Note: load_session returns None if persisted session has remember==False.
        """
        if self._session and self._session.token:
            return self._session
        return self.load_session()

    def is_logged_in(self) -> bool:
        s = self.get_session()
        return bool(s and s.token)

    def get_logged_in_email(self) -> str:
        """Used by tournament ownership logic."""
        try:
            s = self.get_session()
            if s and isinstance(s.user, dict):
                return (s.user.get("email", "") or "").strip()
        except Exception:
            pass
        return ""

    # ✅ NEW: local cached user (no network)
    def get_current_user_local(self) -> Dict[str, Any]:
        s = self.get_session()
        if s and isinstance(s.user, dict):
            return s.user
        return {}

    # ============================================================
    # HTTP helpers (robust diagnostics)
    # ============================================================
    def _verify_value(self):
        return self.ca_bundle_path if self.ca_bundle_path else self.ssl_verify

    def _headers(self, auth: bool = False, token_override: str | None = None) -> Dict[str, str]:
        h = {
            "X-APP-KEY": self.app_api_key,
            "Content-Type": "application/json",
        }
        token = (token_override or "").strip()
        if auth and not token:
            s = self.get_session()
            if s and s.token:
                token = s.token
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        auth: bool = False,
        token_override: str | None = None,
    ) -> Dict[str, Any]:
        try:
            if not self.backend_url:
                return {"ok": False, "error": "Backend URL not set."}
            if not self.app_api_key:
                return {"ok": False, "error": "App API key not set."}

            url = self.backend_url + path

            r = self._http.request(
                method=method.upper(),
                url=url,
                json=payload,
                params=params,
                headers=self._headers(auth=auth, token_override=token_override),
                timeout=(self._connect_timeout, self._read_timeout),
                verify=self._verify_value(),
            )

            # Expect JSON dict from backend
            try:
                data = r.json()
                if not isinstance(data, dict):
                    return {
                        "ok": False,
                        "error": "Unexpected response type (not JSON dict).",
                        "http_status": r.status_code,
                        "url": r.url,
                    }

                # normalize ok=false for HTTP errors if backend forgot
                if r.status_code >= 400 and data.get("ok", True):
                    data["ok"] = False
                    data.setdefault("error", f"HTTP {r.status_code}")

                data.setdefault("http_status", r.status_code)
                data.setdefault("url", r.url)
                return data

            except Exception:
                text = (r.text or "").strip()
                return {
                    "ok": False,
                    "error": (
                        f"Backend returned non-JSON (HTTP {r.status_code}). "
                        f"Likely wrong BACKEND_URL / backend not restarted / missing endpoint."
                    ),
                    "http_status": r.status_code,
                    "url": r.url,
                    "detail": text[:1200],
                }

        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _post(self, path: str, payload: Dict[str, Any], auth: bool = False, token_override: str | None = None) -> Dict[str, Any]:
        return self._request("POST", path, payload=payload, auth=auth, token_override=token_override)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, auth: bool = False, token_override: str | None = None) -> Dict[str, Any]:
        return self._request("GET", path, params=params, auth=auth, token_override=token_override)

    # ============================================================
    # Auth APIs (primary)
    # ============================================================
    def register(self, name: str, email: str, password: str) -> Dict[str, Any]:
        res = self._post("/auth/register", {"name": name, "email": email, "password": password}, auth=False)
        if res.get("ok") and res.get("token"):
            # default behavior: persist; controller can call save_session with remember flag if it wants non-persistent session
            self.save_session(res.get("token", ""), res.get("user", {}) or {}, remember=True)
        return res

    def login(self, email: str, password: str) -> Dict[str, Any]:
        res = self._post("/auth/login", {"email": email, "password": password}, auth=False)
        # DO NOT auto-save here — let caller decide remember flag via save_session.
        # But for backward compatibility, if res contains token, save it (persistent) by default.
        if res.get("ok") and res.get("token"):
            self.save_session(res.get("token", ""), res.get("user", {}) or {}, remember=True)
        return res

    def me(self) -> Dict[str, Any]:
        res = self._get("/auth/me", auth=True)
        if res.get("ok") and isinstance(res.get("user"), dict):
            s = self.get_session()
            if s and s.token:
                # retain remember flag on update
                self.save_session(s.token, res["user"], remember=s.remember)
        return res

    # ✅ NEW: used by AuthController.check_auto_login()
    def refresh_me(self) -> Dict[str, Any]:
        return self.me()

    def update_profile(self, name: str) -> Dict[str, Any]:
        res = self._post("/auth/profile", {"name": name}, auth=True)
        if res.get("ok") and isinstance(res.get("user"), dict):
            s = self.get_session()
            if s and s.token:
                self.save_session(s.token, res["user"], remember=s.remember)
        return res

    def change_password(self, old_password: str, new_password: str) -> Dict[str, Any]:
        return self._post("/auth/change-password", {"old_password": old_password, "new_password": new_password}, auth=True)

    def forgot_password(self, email: str) -> Dict[str, Any]:
        return self._post("/auth/forgot-password", {"email": email}, auth=False)

    def reset_password(self, token: str, new_password: str) -> Dict[str, Any]:
        return self._post("/auth/reset-password", {"token": token, "new_password": new_password}, auth=False)

    # ============================================================
    # Logout helpers
    # ============================================================
    def logout_local(self) -> None:
        """Clear local session immediately (no network call)."""
        self.clear_session()

    def logout_remote(self, token: str) -> Dict[str, Any]:
        token = (token or "").strip()
        if not token:
            return {"ok": True, "detail": "No token; local-only logout."}
        return self._post("/auth/logout", {}, auth=False, token_override=token)

    def logout(self) -> Dict[str, Any]:
        # keep backward-compatible behavior: call backend then clear
        res = self._post("/auth/logout", {}, auth=True)
        self.clear_session()
        return res

    def delete_account(self) -> Dict[str, Any]:
        res = self._post("/auth/delete", {}, auth=True)
        self.clear_session()
        return res

    # ============================================================
    # Backward-compatible method names (for your older controllers)
    # ============================================================
    def register_user(self, name: str, email: str, password: str) -> Dict[str, Any]:
        """Alias for older code."""
        return self.register(name, email, password)

    def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """Alias for older code."""
        return self.login(email, password)

    def logout_user(self) -> Dict[str, Any]:
        """Alias for older code."""
        return self.logout()

    def get_current_user(self) -> Dict[str, Any]:
        """Alias for older code. Returns cached local session user (fast)."""
        return self.get_current_user_local()

    # ============================================================
    # Tournament cloud APIs
    # ============================================================
    def upload_tournament(self, tid: str, name: str, fmt: str, data: Dict[str, Any], shared: int) -> Dict[str, Any]:
        return self._post(
            "/tournament/upload",
            {"tid": tid, "name": name, "format": fmt, "data": data, "shared": int(shared)},
            auth=True,
        )

    def unshare_tournament(self, tid: str) -> Dict[str, Any]:
        return self._post("/tournament/unshare", {"tid": tid}, auth=True)

    def search_tournament(self, tid: str) -> Dict[str, Any]:
        return self._get("/tournament/search", params={"tid": tid}, auth=False)

    def delete_tournament_cloud(self, tid: str) -> Dict[str, Any]:
        return self._post("/tournament/delete-cloud", {"tid": tid}, auth=True)