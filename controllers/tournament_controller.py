# controllers/tournament_controller.py
from __future__ import annotations

import random
from functools import partial
from uuid import uuid4
from datetime import datetime, date

import calendar as _cal

from kivy.factory import Factory
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, Rectangle


class TournamentController:
    def __init__(self, app):
        self.app = app
        self._team_matches_team: str | None = None

        # ✅ NEW: when user searches a tid, show only that tid in list
        self._list_filter_tid: str | None = None

    # ============================================================
    # Tournament ID / Sharing / Ownership helpers
    # ============================================================
    def _my_email(self) -> str:
        try:
            return (self.app.auth.get_logged_in_email() or "").strip().lower()
        except Exception:
            return ""

    def _generate_tid(self) -> str:
        return str(random.randint(100000, 999999))

    def _ensure_tournament_fields(self, db: dict) -> bool:
        """Migration: ensure each tournament has tid, shared, owner_email."""
        changed = False
        db.setdefault("tournaments", {})
        tours = db.get("tournaments") or {}
        if not isinstance(tours, dict):
            db["tournaments"] = {}
            return True

        tids = set()
        for t in tours.values():
            if isinstance(t, dict) and t.get("tid"):
                tids.add(str(t["tid"]))

        me = self._my_email()
        for t in tours.values():
            if not isinstance(t, dict):
                continue
            if not t.get("tid"):
                tid = self._generate_tid()
                while tid in tids:
                    tid = self._generate_tid()
                t["tid"] = tid
                tids.add(tid)
                changed = True
            if "shared" not in t:
                t["shared"] = False
                changed = True
            if "owner_email" not in t:
                t["owner_email"] = ""
                changed = True
            if not (t.get("owner_email") or "").strip() and me:
                t["owner_email"] = me
                changed = True
        return changed

    def _is_owner(self, tour: dict) -> bool:
        # If tournament was fetched via Search, ALWAYS readonly
        if tour.get("is_remote"):
            return False

        owner = (tour.get("owner_email") or "").strip().lower()
        if not owner:
            return True  # legacy tournaments with no owner set

        me = self._my_email()

        # If we can't determine current user email,
        # but tournament HAS an owner → treat as NOT owner (safe default)
        if not me:
            return False

        return me == owner

    def is_tournament_readonly(self, tournament_name: str | None = None) -> bool:
        tname = tournament_name or getattr(self.app, "current_tournament", None)
        if not tname:
            return False
        db = self.app.load_db()
        tour = (db.get("tournaments", {}) or {}).get(tname, {})
        if not isinstance(tour, dict):
            return False
        return not self._is_owner(tour)

    def _readonly_guard(self) -> bool:
        if self.is_tournament_readonly():
            self._show_msg("Read Only", "You can only view this tournament.\nOnly the creator can edit.")
            return True
        return False

    def _show_msg(self, title: str, msg: str):
        Popup(
            title=title,
            content=Label(text=msg, color=(0.1, 0.12, 0.16, 1)),
            size_hint=(None, None),
            size=(dp(420), dp(260)),
        ).open()

    def _err_popup(self, title: str, res: dict):
        msg = res.get("error", "Unknown error")
        http_status = res.get("http_status", "")
        url = res.get("url", "")
        detail = res.get("detail", "")
        text = f"{msg}\n\nHTTP: {http_status}\nURL: {url}"
        if detail:
            text += f"\n\nDETAIL:\n{detail}"
        self._show_msg(title, text)

    def _cloud_payload(self, tname: str, tour: dict) -> dict:
        data = dict(tour)
        data.pop("tid", None)
        data.pop("shared", None)
        data.pop("owner_email", None)
        data.pop("is_remote", None)
        data.pop("cloud_shared", None)
        return {
            "tid": str(tour.get("tid", "")),
            "name": tname,
            "fmt": str(tour.get("format", "")),
            "data": data,
            "shared": 1 if tour.get("shared") else 0,
        }

    # ============================================================
    # ✅ NEW: shared flag parsing + purge unshared remote copies
    # ============================================================
    def _as_bool(self, v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            try:
                return bool(int(v))
            except Exception:
                return False
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "y", "on")
        return False

    def _remote_still_shared(self, tid: str) -> bool | None:
        """
        Returns:
          True  => definitely shared/available
          False => definitely NOT shared/not available (delete local remote copy)
          None  => unknown/network error (keep local copy)
        """
        tid = (tid or "").strip()
        if not tid:
            return None
        try:
            res = self.app.auth.search_tournament(tid)
        except Exception:
            return None

        if not isinstance(res, dict):
            return None

        if not res.get("ok"):
            hs = res.get("http_status", None)
            try:
                hs = int(hs)
            except Exception:
                hs = None
            # Treat 403/404 as "not shared/not available"
            if hs in (403, 404):
                return False
            return None

        cloud = res.get("tournament") or {}
        if not isinstance(cloud, dict):
            return None

        shared_flag = cloud.get("shared", None)
        if shared_flag is None:
            shared_flag = res.get("shared", None)

        # If server doesn't provide shared flag, do not delete.
        if shared_flag is None:
            return True

        return self._as_bool(shared_flag)

    def _purge_unshared_remote_tournaments(self, db: dict) -> bool:
        """Remove local remote tournaments that are no longer shared by owner."""
        tours = db.get("tournaments", {}) or {}
        if not isinstance(tours, dict):
            return False

        changed = False
        for name in list(tours.keys()):
            t = tours.get(name)
            if not isinstance(t, dict):
                continue
            if not bool(t.get("is_remote", False)):
                continue

            tid = str(t.get("tid", "") or "").strip()
            still = self._remote_still_shared(tid)
            if still is False:
                del tours[name]
                changed = True

        if changed:
            db["tournaments"] = tours
        return changed

    def _find_local_name_by_tid(self, db: dict, tid: str) -> str | None:
        tours = db.get("tournaments", {}) or {}
        for name, t in tours.items():
            if isinstance(t, dict) and str(t.get("tid", "")) == str(tid):
                return name
        return None

    # ============================================================
    # ✅ ENHANCED: Share / Unshare with proper status messages
    # ============================================================
    def toggle_share(self, tournament_name: str, shared: bool):
        """
        Toggle tournament sharing state:
        - shared=True:  Make ONLINE & SHARED (other users can search)
        - shared=False: Make PRIVATE (only owner can access)
        """
        if self.is_tournament_readonly(tournament_name):
            self._show_msg("Read Only", "Only the creator can change sharing.")
            self.load_tournaments()
            return

        db = self.app.load_db()
        tours = db.get("tournaments", {}) or {}
        tour = tours.get(tournament_name)
        if not isinstance(tour, dict):
            return

        tour["shared"] = bool(shared)
        self.app.save_db(db)

        if shared:
            # ✅ MAKING SHARED: Upload to cloud
            payload = self._cloud_payload(tournament_name, tour)
            res = self.app.auth.upload_tournament(
                tid=payload["tid"], name=payload["name"],
                fmt=payload["fmt"], data=payload["data"], shared=payload["shared"],
            )
            if res.get("ok"):
                self._show_msg(
                    "Tournament Shared",
                    f"✅ '{tournament_name}' is now SHARED online.\n\n"
                    f"Other users can find it using Tournament ID:\n{payload['tid']}"
                )
            else:
                self._err_popup("Share Failed", res)
                # Revert on failure
                db = self.app.load_db()
                db["tournaments"][tournament_name]["shared"] = False
                self.app.save_db(db)
        else:
            # ✅ MAKING PRIVATE: Remove from cloud
            tid = str(tour.get("tid", ""))
            res = self.app.auth.unshare_tournament(tid)
            if res.get("ok"):
                self._show_msg(
                    "Tournament Private",
                    f"🔒 '{tournament_name}' is now PRIVATE.\n\n"
                    f"Only you can access it. Other users cannot search for it."
                )
            else:
                self._err_popup("Unshare Failed", res)
                # Revert on failure
                db = self.app.load_db()
                db["tournaments"][tournament_name]["shared"] = True
                self.app.save_db(db)

        self.load_tournaments()

    # Keeping for compatibility if referenced elsewhere, but UI button removed
    def sync_tournament(self, tournament_name: str):
        if self.is_tournament_readonly(tournament_name):
            self._show_msg("Read Only", "Only the creator can update cloud.")
            return

        db = self.app.load_db()
        tour = (db.get("tournaments", {}) or {}).get(tournament_name)
        if not isinstance(tour, dict):
            return
        if not tour.get("shared"):
            self._show_msg("Info", "Enable Share first, then Update.")
            return

        payload = self._cloud_payload(tournament_name, tour)
        res = self.app.auth.upload_tournament(
            tid=payload["tid"], name=payload["name"],
            fmt=payload["fmt"], data=payload["data"], shared=payload["shared"],
        )
        if res.get("ok"):
            self._show_msg("Updated", f"Cloud updated.\nTournament ID: {payload['tid']}")
        else:
            self._err_popup("Update Failed", res)

        self.load_tournaments()

    # ============================================================
    # ✅ ENHANCED: Search by Tournament ID - Only shared tournaments
    # ============================================================
    def search_tournament_by_id(self, tid: str):
        """
        Search for shared tournament by ID.
        ONLY returns tournaments with shared=True.
        Private tournaments (shared=False) are NOT searchable.
        """
        tid = (tid or "").strip()
        if len(tid) != 6:
            self._show_msg("Search", "Enter a valid 6-digit Tournament ID.")
            return

        # If already exists locally, just filter to it
        db = self.app.load_db()
        self._ensure_tournament_fields(db)
        local_name = self._find_local_name_by_tid(db, tid)
        if local_name:
            local_tour = (db.get("tournaments", {}) or {}).get(local_name, {})
            if isinstance(local_tour, dict):
                # ✅ Check if locally shared
                if not bool(local_tour.get("shared", False)):
                    self._show_msg("Not Shared", f"Tournament {tid} is PRIVATE.\nOnly the owner can access it.")
                    return
            self._list_filter_tid = tid
            self.load_tournaments()
            return

        res = self.app.auth.search_tournament(tid)
        if not res.get("ok"):
            self._err_popup("Search Failed", res)
            return

        cloud = res.get("tournament") or {}
        if not isinstance(cloud, dict):
            self._show_msg("Search Failed", "Invalid server response.")
            return

        # ✅ CRITICAL: Check if tournament is SHARED before importing
        shared_flag = cloud.get("shared", None)
        if shared_flag is None:
            shared_flag = res.get("shared", None)
        
        # If shared flag exists and is False, REJECT (tournament is private)
        if shared_flag is not None and (not self._as_bool(shared_flag)):
            self._show_msg(
                "Not Shared",
                f"🔒 Tournament ID {tid} is PRIVATE.\n\n"
                f"Only the owner can access it.\n"
                f"This tournament is not available for public search."
            )
            return

        name = (cloud.get("name") or "Tournament").strip()
        owner = (cloud.get("owner_email") or "").strip()
        fmt = (cloud.get("format") or cloud.get("fmt") or "").strip()
        data = cloud.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        db = self.app.load_db()
        self._ensure_tournament_fields(db)

        # If tid exists (race), update
        for local_name, lt in (db.get("tournaments", {}) or {}).items():
            if isinstance(lt, dict) and str(lt.get("tid", "")) == tid:
                lt.update(data)
                lt["tid"] = tid
                lt["format"] = fmt
                lt["owner_email"] = owner
                lt["is_remote"] = True

                # For remote display purposes: reflect cloud state as shared=True
                # (checkbox is disabled for non-owner anyway)
                lt["cloud_shared"] = True
                lt["shared"] = True

                self.app.save_db(db)

                self._list_filter_tid = tid
                self.load_tournaments()
                
                self._show_msg(
                    "Tournament Found",
                    f"✅ Found shared tournament: {name}\n\n"
                    f"Tournament ID: {tid}\n"
                    f"Owner: {owner}"
                )
                return

        # Create unique local name
        local_name = name
        i = 1
        while local_name in db["tournaments"]:
            local_name = f"{name}_{i}"
            i += 1

        db["tournaments"][local_name] = {
            "tid": tid,
            "format": fmt,
            "owner_email": owner,
            "shared": True,        # display status (checkbox disabled for non-owner)
            "cloud_shared": True,
            "is_remote": True,
            **data,
        }
        self.app.save_db(db)

        self._list_filter_tid = tid
        self.load_tournaments()

        self._show_msg(
            "Tournament Added",
            f"✅ Successfully added shared tournament: {name}\n\n"
            f"Tournament ID: {tid}\n"
            f"You can now view all matches and statistics."
        )

    # ---------------- small UI helpers ----------------
    def _row_bg(self, widget, rgba):
        with widget.canvas.before:
            Color(*rgba)
            rect = Rectangle(pos=widget.pos, size=widget.size)

        def _sync(*_):
            rect.pos = widget.pos
            rect.size = widget.size

        widget.bind(pos=_sync, size=_sync)

    def _mk_cell(self, text, width, align="center", bold=False):
        if bold:
            text = f"[b]{text}[/b]"
        lab = Label(
            text=text,
            markup=True,
            size_hint=(None, None),
            width=width,
            height=dp(32),
            color=(0.10, 0.12, 0.16, 1),
            halign=align,
            valign="middle",
        )
        lab.text_size = (width, dp(32))
        return lab

    def _fmt_weekday(self, dstr: str) -> str:
        if not dstr:
            return "TBD"
        try:
            dt = datetime.strptime(dstr, "%Y-%m-%d").date()
            return dt.strftime("%a, %d %b")
        except Exception:
            return "TBD"

    def _match_status(self, m: dict) -> str:
        if m.get("status") == "completed" or m.get("winner"):
            return "Completed"
        if m.get("status") == "running" or m.get("scorecard"):
            return "Running"
        return "Pending"

    def _time_sort_key(self, t: str):
        t = (t or "").strip().upper()
        if not t or t == "TBD":
            return (99, 99)

        try:
            if "AM" in t or "PM" in t:
                t = " ".join(t.split())
                dt = datetime.strptime(t, "%I:%M %p")
                return (dt.hour, dt.minute)
        except Exception:
            pass

        try:
            dt = datetime.strptime(t, "%H:%M")
            return (dt.hour, dt.minute)
        except Exception:
            return (99, 99)

    def _compute_last5(self, team: str, matches: list[dict]) -> list[str]:
        seq = []
        for m in reversed(matches or []):  # most recent first
            if not (m.get("status") == "completed" or m.get("winner")):
                continue
            if team not in (m.get("team1"), m.get("team2")):
                continue

            w = m.get("winner")
            if not w:
                continue

            seq.append("W" if w == team else "L")
            if len(seq) >= 5:
                break

        seq.reverse()
        return seq

    # ✅ NEW: Get sharing status label
    def _get_share_status(self, tour: dict) -> str:
        """Returns display text for sharing status."""
        if bool(tour.get("is_remote", False)):
            return "👁️ Remote (Shared)"
        if bool(tour.get("shared", False)):
            return "🌐 Shared Online"
        return "🔒 Private"

    # ---------------- points calculation ----------------
    def _recalculate_points_from_matches(self, tour: dict) -> dict:
        teams_dict = tour.get("teams", {}) or {}
        matches = tour.get("matches", []) or []

        acc = {}
        for team in teams_dict.keys():
            acc[team] = {
                "played": 0,
                "won": 0,
                "lost": 0,
                "nr": 0,
                "points": 0,
                "nrr": 0.0,
                "_runs_for": 0,
                "_balls_for": 0,
                "_runs_against": 0,
                "_balls_against": 0,
            }

        def _add_nrr(team, runs_for, balls_for, runs_against, balls_against):
            if team not in acc:
                return
            acc[team]["_runs_for"] += int(runs_for or 0)
            acc[team]["_balls_for"] += int(balls_for or 0)
            acc[team]["_runs_against"] += int(runs_against or 0)
            acc[team]["_balls_against"] += int(balls_against or 0)

        for m in matches:
            status = (m.get("status") or "").lower().strip()
            winner = m.get("winner")
            is_completed = (status == "completed") or bool(winner)
            if not is_completed:
                continue

            t1 = m.get("team1")
            t2 = m.get("team2")
            if not t1 or not t2:
                continue
            if t1 not in acc or t2 not in acc:
                continue

            acc[t1]["played"] += 1
            acc[t2]["played"] += 1

            if winner == t1:
                acc[t1]["won"] += 1
                acc[t2]["lost"] += 1
                acc[t1]["points"] += 2
            elif winner == t2:
                acc[t2]["won"] += 1
                acc[t1]["lost"] += 1
                acc[t2]["points"] += 2
            else:
                acc[t1]["nr"] += 1
                acc[t2]["nr"] += 1
                acc[t1]["points"] += 1
                acc[t2]["points"] += 1

            sc = m.get("scorecard") or {}
            inn1 = sc.get("innings1")
            inn2 = sc.get("innings2")
            if inn1 and inn2:
                _add_nrr(
                    inn1.get("batting_team"),
                    inn1.get("runs", 0),
                    inn1.get("legal_balls", 0),
                    inn2.get("runs", 0),
                    inn2.get("legal_balls", 0),
                )
                _add_nrr(
                    inn2.get("batting_team"),
                    inn2.get("runs", 0),
                    inn2.get("legal_balls", 0),
                    inn1.get("runs", 0),
                    inn1.get("legal_balls", 0),
                )

        for team, p in acc.items():
            bf = p.pop("_balls_for")
            ba = p.pop("_balls_against")
            rf = p.pop("_runs_for")
            ra = p.pop("_runs_against")

            nrr = 0.0
            if bf > 0 and ba > 0:
                nrr = (rf / (bf / 6.0)) - (ra / (ba / 6.0))
            elif bf > 0:
                nrr = (rf / (bf / 6.0))
            p["nrr"] = float(nrr)

        return acc

    # ---------------- basic tournament actions ----------------
    def create_tournament(self, name, fmt):
        name = (name or "").strip()
        if not name:
            return
        db = self.app.load_db()
        self._ensure_tournament_fields(db)
        if name not in db["tournaments"]:
            existing_tids = {str(t.get("tid")) for t in db["tournaments"].values() if isinstance(t, dict) and t.get("tid")}
            tid = self._generate_tid()
            while tid in existing_tids:
                tid = self._generate_tid()

            db["tournaments"][name] = {
                "tid": tid,
                "format": fmt,
                "teams": {},
                "matches": [],
                "points": {},
                "shared": False,  # ✅ NEW: Default to PRIVATE
                "owner_email": self._my_email(),
            }
            self.app.save_db(db)
        self.app.set_header_title("Cricket")
        self.app.go("home")

    def load_tournaments(self):
        # migrate fields
        db = self.app.load_db()
        if self._ensure_tournament_fields(db):
            self.app.save_db(db)

        # ✅ NEW: purge remote tournaments that are no longer shared
        db2 = self.app.load_db()
        if self._purge_unshared_remote_tournaments(db2):
            self.app.save_db(db2)
        db = self.app.load_db()

        self.app.set_header_title("Tournaments")
        grid = self.app.sm.get_screen("tournament_list").ids.tour_grid
        grid.clear_widgets()

        # Search row
        search_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8), padding=(dp(6), dp(6), dp(6), dp(6)))
        search_inp = TextInput(
            hint_text="Enter 6-digit Tournament ID",
            multiline=False,
            input_filter="int",
            font_size=dp(14),
        )
        search_btn = Button(
            text="Search",
            size_hint_x=None,
            width=dp(110),
            background_normal="",
            background_down="",
            background_color=(0.13, 0.78, 0.42, 1),
            color=(1, 1, 1, 1),
            bold=True,
        )
        search_btn.bind(on_release=lambda *_: self.search_tournament_by_id(search_inp.text.strip()))
        search_row.add_widget(search_inp)
        search_row.add_widget(search_btn)
        grid.add_widget(search_row)

        # Filter helper
        if self._list_filter_tid:
            show_all_btn = Button(
                text="Show All Tournaments",
                size_hint_y=None,
                height=dp(44),
                background_normal="",
                background_down="",
                background_color=(0.45, 0.49, 0.55, 1),
                color=(1, 1, 1, 1),
            )

            def _clear_filter(*_):
                self._list_filter_tid = None
                self.load_tournaments()

            show_all_btn.bind(on_release=_clear_filter)
            grid.add_widget(show_all_btn)

        tournaments = db.get("tournaments", {}) or {}
        if self._list_filter_tid:
            tournaments = {
                n: t for n, t in tournaments.items()
                if isinstance(t, dict) and str(t.get("tid", "")) == str(self._list_filter_tid)
            }

        if not tournaments:
            grid.add_widget(Button(text="No tournaments found.", size_hint_y=None, height=44, disabled=True))
            self.app.go("tournament_list")
            return

        for tname in sorted(tournaments.keys()):
            tour = tournaments.get(tname) or {}
            if not isinstance(tour, dict):
                continue

            tid = str(tour.get("tid", "------"))
            shared = bool(tour.get("shared", False))
            is_owner = self._is_owner(tour)
            is_remote = bool(tour.get("is_remote", False)) and (not is_owner)

            # ✅ SIMPLIFIED LAYOUT: Single row with checkbox, name, and access type
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(44),
                spacing=dp(8),
                padding=(dp(6), dp(4), dp(6), dp(4))
            )

            # Left: Checkbox
            try:
                ShareCB = Factory.BlackCheckBox
                share_cb = ShareCB()
            except Exception:
                share_cb = CheckBox()

            share_cb.size_hint = (None, None)
            share_cb.size = (dp(20), dp(20))
            share_cb.active = shared
            share_cb.disabled = not is_owner
            share_cb.bind(active=lambda _cb, val, n=tname: self.toggle_share(n, bool(val)))

            row.add_widget(share_cb)

            # Middle: Tournament name and ID (left-aligned, clickable)
            name_text = f"{tname}\n({tid})"
            main_btn = Button(
                text=name_text,
                size_hint_x=0.6,
                background_normal="",
                background_down="",
                background_color=(0.16, 0.63, 0.95, 1) if is_owner else (0.55, 0.55, 0.55, 1),
                color=(1, 1, 1, 1),
                halign="left",
                valign="middle",
                font_size="13sp",
            )
            main_btn.text_size = (main_btn.width - dp(10), None)
            main_btn.bind(on_release=partial(self.open_tournament, tname))
            row.add_widget(main_btn)

            # Right: Access type label
            access_text = "View Only" if (not is_owner) else "Editable"
            access_color = (0.70, 0.73, 0.78, 1) if (not is_owner) else (0.16, 0.63, 0.95, 1)
            
            access_label = Label(
                text=access_text,
                color=access_color,
                font_size=dp(11),
                size_hint_x=0.3,
                halign="right",
                valign="middle",
            )
            access_label.text_size = (access_label.width, dp(44))
            row.add_widget(access_label)

            # Add background color
            self._row_bg(row, (0.98, 0.99, 1.0, 1) if not is_remote else (0.95, 0.97, 0.99, 1))

            grid.add_widget(row)

        self.app.go("tournament_list")

    def open_tournament(self, name, *_):
        self.app.current_tournament = name

        db = self.app.load_db()
        tour = (db.get("tournaments", {}) or {}).get(name, {})
        tid = ""
        if isinstance(tour, dict):
            tid = str(tour.get("tid", "") or "")
        if tid:
            self.app.set_header_title(f"{name} ({tid})")
        else:
            self.app.set_header_title(name)

        try:
            self.app.match_readonly = self.is_tournament_readonly(name)
        except Exception:
            self.app.match_readonly = False

        if isinstance(tour, dict) and tour.get("is_remote"):
            self.app.match_readonly = True

        print(
            f"[DEBUG] open_tournament: name={name}, match_readonly={self.app.match_readonly}, "
            f"is_remote={tour.get('is_remote') if isinstance(tour, dict) else None}, "
            f"owner={tour.get('owner_email') if isinstance(tour, dict) else None}"
        )

        self.refresh_teams()
        self.refresh_points()
        self.refresh_matches()
        self.app.go("tournament_detail")

    # ---------------- Teams section ----------------
    def add_team(self, team, players):
        if self._readonly_guard():
            return

        team = (team or "").strip()
        if not team or not self.app.current_tournament:
            return

        db = self.app.load_db()
        t = db["tournaments"][self.app.current_tournament]

        plist = [p.strip() for p in (players or []) if p and p.strip()]
        t["teams"][team] = plist

        t.setdefault("points", {})
        t["points"].setdefault(team, {"played": 0, "won": 0, "lost": 0, "nr": 0, "points": 0, "nrr": 0.0})

        self.app.save_db(db)
        self.refresh_teams()
        self.refresh_points()

    def refresh_teams(self):
        if not self.app.current_tournament:
            return
        scr = self.app.sm.get_screen("tournament_detail")
        scr.ids.team_grid.clear_widgets()

    # ---------------- Points table ----------------
    def refresh_points(self):
        if not self.app.current_tournament:
            return

        scr = self.app.sm.get_screen("tournament_detail")
        grid = scr.ids.points_grid
        grid.clear_widgets()

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]

        teams_dict = tour.get("teams", {}) or {}
        matches = tour.get("matches", []) or []

        if not teams_dict:
            grid.add_widget(Button(text="No teams added", size_hint_y=None, height=40, disabled=True))
            return

        points = self._recalculate_points_from_matches(tour)
        tour["points"] = points
        self.app.save_db(db)

        sorted_rows = sorted(
            points.items(),
            key=lambda x: (-int(x[1].get("points", 0) or 0), -int(x[1].get("won", 0) or 0), int(x[1].get("played", 0) or 0)),
        )

        W_RK = dp(22)
        W_TEAM = dp(70)
        W_M = dp(26)
        W_W = dp(26)
        W_L = dp(26)
        W_NR = dp(28)
        W_NRR = dp(56)
        W_PTS = dp(30)
        W_L5 = dp(90)

        header = BoxLayout(size_hint_y=None, height=dp(32), spacing=0, padding=(dp(6), 0, dp(6), 0))
        self._row_bg(header, (0.90, 0.92, 0.96, 1))
        header.add_widget(self._mk_cell("#", W_RK, bold=True))
        header.add_widget(self._mk_cell("Team", W_TEAM, align="left", bold=True))
        header.add_widget(self._mk_cell("M", W_M, bold=True))
        header.add_widget(self._mk_cell("W", W_W, bold=True))
        header.add_widget(self._mk_cell("L", W_L, bold=True))
        header.add_widget(self._mk_cell("NR", W_NR, bold=True))
        header.add_widget(self._mk_cell("NRR", W_NRR, bold=True))
        header.add_widget(self._mk_cell("Pts", W_PTS, bold=True))
        header.add_widget(self._mk_cell("Last 5", W_L5, align="left", bold=True))
        grid.add_widget(header)

        rank = 0
        for team, p in sorted_rows:
            if team not in teams_dict:
                continue
            rank += 1
            even = (rank % 2 == 0)
            bg = (1, 1, 1, 1) if not even else (0.97, 0.98, 1, 1)

            row = BoxLayout(size_hint_y=None, height=dp(32), spacing=0, padding=(dp(6), 0, dp(6), 0))
            self._row_bg(row, bg)

            played = int(p.get("played", 0) or 0)
            won = int(p.get("won", 0) or 0)
            lost = int(p.get("lost", 0) or 0)
            nr = int(p.get("nr", 0) or 0)
            pts = int(p.get("points", 0) or 0)
            try:
                nrr = float(p.get("nrr", 0.0) or 0.0)
            except Exception:
                nrr = 0.0

            last5 = self._compute_last5(team, matches)
            last5_txt = "  ".join(last5) if last5 else "-"

            row.add_widget(self._mk_cell(str(rank), W_RK))

            team_btn = Button(
                text=team,
                size_hint=(None, None),
                width=W_TEAM,
                height=dp(32),
                background_normal="",
                background_down="",
                background_color=(0, 0, 0, 0),
                color=(0.10, 0.12, 0.16, 1),
                halign="left",
                valign="middle",
            )
            team_btn.text_size = (W_TEAM, dp(32))
            team_btn.bind(on_release=partial(self.open_team_matches, team))
            row.add_widget(team_btn)

            row.add_widget(self._mk_cell(str(played), W_M))
            row.add_widget(self._mk_cell(str(won), W_W))
            row.add_widget(self._mk_cell(str(lost), W_L))
            row.add_widget(self._mk_cell(str(nr), W_NR))
            row.add_widget(self._mk_cell(f"{nrr:+.3f}", W_NRR))
            row.add_widget(self._mk_cell(str(pts), W_PTS, bold=True))
            row.add_widget(self._mk_cell(last5_txt, W_L5, align="left"))
            grid.add_widget(row)

    # ---------------- Team matches screen ----------------
    def open_team_matches(self, team: str, *_):
        team = (team or "").strip()
        if not team or not self.app.current_tournament:
            return
        self._team_matches_team = team
        self.app.set_header_title(f"{team} Matches")
        self.refresh_team_matches(team)
        self.app.go("team_matches")

    def back_from_team_matches(self):
        if self.app.current_tournament:
            db = self.app.load_db()
            tour = (db.get("tournaments", {}) or {}).get(self.app.current_tournament, {})
            tid = str(tour.get("tid", "")) if isinstance(tour, dict) else ""
            if tid:
                self.app.set_header_title(f"{self.app.current_tournament} ({tid})")
            else:
                self.app.set_header_title(self.app.current_tournament)
        self.app.go("tournament_detail")

    def refresh_team_matches(self, team: str):
        if not self.app.current_tournament:
            return

        scr = self.app.sm.get_screen("team_matches")
        grid = scr.ids.team_matches_grid
        grid.clear_widgets()

        try:
            scr.ids.team_matches_title.text = f"{team} - Matches"
        except Exception:
            pass

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        matches = tour.get("matches", []) or []
        matches = [m for m in matches if team in (m.get("team1"), m.get("team2"))]

        if not matches:
            grid.add_widget(Button(
                text="No matches for this team yet.",
                size_hint_y=None,
                height=dp(44),
                disabled=True,
                background_normal="",
                background_color=(0.92, 0.92, 0.92, 1),
                color=(0.35, 0.35, 0.35, 1),
            ))
            return

        def _sort_key(m):
            d = (m.get("date") or "").strip()
            try:
                dd = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                dd = date.max
            return (dd, self._time_sort_key(m.get("time")))

        matches = sorted(matches, key=_sort_key)

        readonly = self.is_tournament_readonly()

        for idx, m in enumerate(matches, start=1):
            match_id = m.get("id")
            team1 = m.get("team1", "")
            team2 = m.get("team2", "")

            d_txt = self._fmt_weekday(m.get("date", ""))
            t_txt = (m.get("time") or "TBD").strip() or "TBD"
            v_txt = (m.get("venue") or "TBD").strip() or "TBD"

            status = self._match_status(m)
            if status == "Completed":
                status_line = m.get("result_text") or (f"Winner: {m.get('winner')}" if m.get("winner") else "Completed")
                bg = (0.65, 0.65, 0.65, 1)
            elif status == "Running":
                status_line = "Status: Running"
                bg = (0.96, 0.58, 0.11, 1)
            else:
                status_line = "Status: Pending"
                bg = (0.16, 0.63, 0.95, 1) if idx % 2 == 1 else (0.13, 0.78, 0.42, 1)

            row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(78))

            num_lbl = Label(
                text=str(idx),
                size_hint_x=None,
                width=dp(36),
                color=(1, 1, 1, 1),
                bold=True,
                halign="center",
                valign="middle",
            )
            num_lbl.text_size = (dp(36), dp(78))
            self._row_bg(num_lbl, bg)

            main_btn = Button(
                text=f"{team1} vs {team2}\n{d_txt} | {t_txt} | {v_txt}\n{status_line}",
                size_hint_x=1,
                background_normal="",
                background_down="",
                background_color=bg,
                color=(1, 1, 1, 1),
                halign="center",
                valign="middle",
            )
            main_btn.text_size = (dp(320), None)
            main_btn.bind(on_release=partial(self.app.start_match, match_id))

            edit_btn = Button(
                text="Edit",
                size_hint_x=None,
                width=dp(64),
                background_normal="",
                background_down="",
                background_color=(0.18, 0.18, 0.18, 1),
                color=(1, 1, 1, 1),
                disabled=readonly,
            )
            edit_btn.bind(on_release=lambda *_args, mid=match_id: self.open_edit_fixture_popup(mid))

            row.add_widget(num_lbl)
            row.add_widget(main_btn)
            row.add_widget(edit_btn)
            grid.add_widget(row)

    # ---------------- Calendar popup ----------------
    def _open_calendar_popup(self, target_date_input: TextInput):
        try:
            base = datetime.strptime(target_date_input.text.strip(), "%Y-%m-%d").date()
            y, m = base.year, base.month
        except Exception:
            today = date.today()
            y, m = today.year, today.month

        state = {"year": y, "month": m}

        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))

        header = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        prev_btn = Button(text="<", size_hint_x=None, width=dp(44))
        title_lbl = Label(text="", color=(0.1, 0.12, 0.16, 1), bold=True)
        next_btn = Button(text=">", size_hint_x=None, width=dp(44))
        header.add_widget(prev_btn)
        header.add_widget(title_lbl)
        header.add_widget(next_btn)
        root.add_widget(header)

        dow = GridLayout(cols=7, size_hint_y=None, height=dp(24))
        for d in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]:
            dow.add_widget(Label(text=f"[b]{d}[/b]", markup=True, color=(0.2, 0.22, 0.26, 1)))
        root.add_widget(dow)

        days_grid = GridLayout(cols=7, spacing=dp(4), size_hint_y=None)
        days_grid.bind(minimum_height=days_grid.setter("height"))
        root.add_widget(days_grid)

        cancel = Button(text="Cancel", size_hint_y=None, height=dp(44))
        root.add_widget(cancel)

        pop = Popup(title="Select Date", content=root, size_hint=(0.95, None), height=dp(420), auto_dismiss=False)

        def _render():
            yy = state["year"]
            mm = state["month"]
            title_lbl.text = f"{_cal.month_name[mm]} {yy}"

            days_grid.clear_widgets()
            weeks = _cal.monthcalendar(yy, mm)
            for w in weeks:
                for daynum in w:
                    if daynum == 0:
                        days_grid.add_widget(Label(text=""))
                    else:
                        b = Button(
                            text=str(daynum),
                            size_hint_y=None,
                            height=dp(40),
                            background_normal="",
                            background_down="",
                            background_color=(0.16, 0.63, 0.95, 1),
                            color=(1, 1, 1, 1),
                        )

                        def _pick(d=daynum):
                            target_date_input.text = f"{yy:04d}-{mm:02d}-{d:02d}"
                            pop.dismiss()

                        b.bind(on_release=lambda *_args, f=_pick: f())
                        days_grid.add_widget(b)

        def _prev(*_):
            mm = state["month"] - 1
            yy = state["year"]
            if mm < 1:
                mm = 12
                yy -= 1
            state["month"] = mm
            state["year"] = yy
            _render()

        def _next(*_):
            mm = state["month"] + 1
            yy = state["year"]
            if mm > 12:
                mm = 1
                yy += 1
            state["month"] = mm
            state["year"] = yy
            _render()

        prev_btn.bind(on_release=_prev)
        next_btn.bind(on_release=_next)
        cancel.bind(on_release=lambda *_: pop.dismiss())

        _render()
        pop.open()

    # ---------------- Create Fixture popup ----------------
    def open_create_fixture_popup(self):
        if self._readonly_guard():
            return

        if not self.app.current_tournament:
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        teams = sorted(list((tour.get("teams") or {}).keys()))

        if len(teams) < 2:
            Popup(
                title="Create Fixture",
                content=Label(text="Add at least 2 teams first.", color=(0.1, 0.12, 0.16, 1)),
                size_hint=(None, None),
                size=(dp(320), dp(180)),
            ).open()
            return

        wrap = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))

        teams_row = BoxLayout(orientation="horizontal", spacing=dp(10), size_hint_y=None, height=dp(80))

        col1 = BoxLayout(orientation="vertical", spacing=dp(4))
        col1.add_widget(Label(text="Team 1", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        team1_sp = Spinner(text=teams[0], values=teams, size_hint_y=None, height=dp(44))
        col1.add_widget(team1_sp)

        col2 = BoxLayout(orientation="vertical", spacing=dp(4))
        col2.add_widget(Label(text="Team 2", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        team2_sp = Spinner(
            text=teams[1] if len(teams) > 1 else teams[0],
            values=[t for t in teams if t != teams[0]],
            size_hint_y=None,
            height=dp(44),
        )
        col2.add_widget(team2_sp)

        def _sync_team2(*_):
            vals = [t for t in teams if t != team1_sp.text]
            team2_sp.values = vals
            if team2_sp.text not in vals:
                team2_sp.text = vals[0] if vals else ""

        team1_sp.bind(text=_sync_team2)

        teams_row.add_widget(col1)
        teams_row.add_widget(col2)
        wrap.add_widget(teams_row)

        wrap.add_widget(Label(text="Date", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        date_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        date_inp = TextInput(text=date.today().strftime("%Y-%m-%d"), multiline=False)
        cal_btn = Button(text="📅", size_hint_x=None, width=dp(54))
        cal_btn.bind(on_release=lambda *_: self._open_calendar_popup(date_inp))
        date_row.add_widget(date_inp)
        date_row.add_widget(cal_btn)
        wrap.add_widget(date_row)

        wrap.add_widget(Label(text="Time", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        time_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        hour_sp = Spinner(text="07", values=[f"{i:02d}" for i in range(1, 13)], size_hint_x=None, width=dp(80))
        min_sp = Spinner(text="30", values=["00", "15", "30", "45"], size_hint_x=None, width=dp(80))
        ap_sp = Spinner(text="PM", values=["AM", "PM"], size_hint_x=None, width=dp(80))
        time_row.add_widget(hour_sp)
        time_row.add_widget(Label(text=":", size_hint_x=None, width=dp(14), color=(0.1, 0.12, 0.16, 1)))
        time_row.add_widget(min_sp)
        time_row.add_widget(ap_sp)
        wrap.add_widget(time_row)

        wrap.add_widget(Label(text="Venue", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        venue_inp = TextInput(text="TBD", multiline=False, size_hint_y=None, height=dp(44))
        wrap.add_widget(venue_inp)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(text="Cancel", background_normal="", background_color=(0.45, 0.49, 0.55, 1), color=(1, 1, 1, 1))
        save_btn = Button(text="Create", background_normal="", background_color=(0.16, 0.63, 0.95, 1), color=(1, 1, 1, 1))
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(save_btn)
        wrap.add_widget(btn_row)

        pop = Popup(title="Create Fixture", content=wrap, size_hint=(0.95, None), height=dp(470), auto_dismiss=False)

        def _create(*_):
            t1 = (team1_sp.text or "").strip()
            t2 = (team2_sp.text or "").strip()
            if not t1 or not t2 or t1 == t2:
                return

            dstr = (date_inp.text or "").strip()
            try:
                datetime.strptime(dstr, "%Y-%m-%d")
            except Exception:
                dstr = date.today().strftime("%Y-%m-%d")

            tstr = f"{hour_sp.text}:{min_sp.text} {ap_sp.text}".strip()
            venue = (venue_inp.text or "").strip() or "TBD"

            self._create_fixture(t1, t2, dstr, tstr, venue)
            pop.dismiss()

        cancel_btn.bind(on_release=lambda *_: pop.dismiss())
        save_btn.bind(on_release=_create)
        pop.open()

    def _create_fixture(self, team1: str, team2: str, dstr: str, tstr: str, venue: str):
        if self._readonly_guard():
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        matches = tour.setdefault("matches", [])

        matches.append({
            "id": str(uuid4()),
            "team1": team1,
            "team2": team2,
            "winner": None,
            "result_text": "",
            "round": 1,
            "status": "pending",
            "date": dstr,
            "time": tstr,
            "venue": venue,
        })

        self.app.save_db(db)
        self.refresh_matches()

    def open_edit_fixture_popup(self, match_id: str):
        if self._readonly_guard():
            return

        if not self.app.current_tournament:
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        matches = tour.get("matches", []) or []

        match = None
        for m in matches:
            if m.get("id") == match_id:
                match = m
                break

        if not match:
            Popup(
                title="Edit Fixture",
                content=Label(text="Match not found.", color=(0.1, 0.12, 0.16, 1)),
                size_hint=(None, None),
                size=(dp(320), dp(180)),
            ).open()
            return

        wrap = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))

        wrap.add_widget(Label(
            text=f"[b]{match.get('team1')} vs {match.get('team2')}[/b]",
            markup=True,
            size_hint_y=None,
            height=dp(26),
            color=(0.1, 0.12, 0.16, 1),
            halign="center",
        ))

        wrap.add_widget(Label(text="Date", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        date_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        date_inp = TextInput(text=(match.get("date") or date.today().strftime("%Y-%m-%d")), multiline=False)
        cal_btn = Button(text="📅", size_hint_x=None, width=dp(54))
        cal_btn.bind(on_release=lambda *_: self._open_calendar_popup(date_inp))
        date_row.add_widget(date_inp)
        date_row.add_widget(cal_btn)
        wrap.add_widget(date_row)

        wrap.add_widget(Label(text="Time", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        time_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))

        hh, mm, ap = "07", "30", "PM"
        try:
            ttxt = (match.get("time") or "").strip().upper()
            if "AM" in ttxt or "PM" in ttxt:
                ttxt = " ".join(ttxt.split())
                dt = datetime.strptime(ttxt, "%I:%M %p")
                hh = dt.strftime("%I")
                mm = dt.strftime("%M")
                ap = dt.strftime("%p")
        except Exception:
            pass

        hour_sp = Spinner(text=hh, values=[f"{i:02d}" for i in range(1, 13)], size_hint_x=None, width=dp(80))
        min_sp = Spinner(text=mm, values=["00", "15", "30", "45"], size_hint_x=None, width=dp(80))
        ap_sp = Spinner(text=ap, values=["AM", "PM"], size_hint_x=None, width=dp(80))
        time_row.add_widget(hour_sp)
        time_row.add_widget(Label(text=":", size_hint_x=None, width=dp(14), color=(0.1, 0.12, 0.16, 1)))
        time_row.add_widget(min_sp)
        time_row.add_widget(ap_sp)
        wrap.add_widget(time_row)

        wrap.add_widget(Label(text="Venue", size_hint_y=None, height=dp(20), color=(0.1, 0.12, 0.16, 1)))
        venue_inp = TextInput(text=(match.get("venue") or "TBD"), multiline=False, size_hint_y=None, height=dp(44))
        wrap.add_widget(venue_inp)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(text="Cancel", background_normal="", background_color=(0.45, 0.49, 0.55, 1), color=(1, 1, 1, 1))
        delete_btn = Button(text="Delete", background_normal="", background_color=(0.90, 0.18, 0.22, 1), color=(1, 1, 1, 1))
        update_btn = Button(text="Update", background_normal="", background_color=(0.13, 0.78, 0.42, 1), color=(1, 1, 1, 1))
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(delete_btn)
        btn_row.add_widget(update_btn)
        wrap.add_widget(btn_row)

        pop = Popup(title="Edit Fixture", content=wrap, size_hint=(0.95, None), height=dp(470), auto_dismiss=False)

        def _do_update(*_):
            if self._readonly_guard():
                return

            dstr = (date_inp.text or "").strip()
            try:
                datetime.strptime(dstr, "%Y-%m-%d")
            except Exception:
                dstr = match.get("date") or date.today().strftime("%Y-%m-%d")

            tstr = f"{hour_sp.text}:{min_sp.text} {ap_sp.text}".strip()
            venue = (venue_inp.text or "").strip() or "TBD"

            match["date"] = dstr
            match["time"] = tstr
            match["venue"] = venue

            self.app.save_db(db)
            self.refresh_matches()
            pop.dismiss()

        def _do_delete(*_):
            if self._readonly_guard():
                return

            tour2 = db["tournaments"][self.app.current_tournament]
            tour2["matches"] = [m for m in (tour2.get("matches", []) or []) if m.get("id") != match_id]
            self.app.save_db(db)
            self.refresh_matches()
            pop.dismiss()

        cancel_btn.bind(on_release=lambda *_: pop.dismiss())
        update_btn.bind(on_release=_do_update)
        delete_btn.bind(on_release=_do_delete)
        pop.open()

    # ============================================================
    # Matches schedule
    # ============================================================
    def refresh_matches(self):
        if not self.app.current_tournament:
            return

        scr = self.app.sm.get_screen("tournament_detail")
        grid = scr.ids.match_grid
        grid.clear_widgets()

        db = self.app.load_db()
        matches = db["tournaments"][self.app.current_tournament].get("matches", []) or []

        if not matches:
            grid.add_widget(Button(
                text="No fixtures yet. Use 'Create Fixture' button above.",
                size_hint_y=None,
                height=dp(44),
                disabled=True,
                background_normal="",
                background_color=(0.92, 0.92, 0.92, 1),
                color=(0.35, 0.35, 0.35, 1),
            ))
            return

        def _sort_key(m):
            d = (m.get("date") or "").strip()
            try:
                dd = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                dd = date.max
            return (dd, self._time_sort_key(m.get("time")))

        matches = sorted(matches, key=_sort_key)
        readonly = self.is_tournament_readonly()

        for idx, m in enumerate(matches, start=1):
            match_id = m.get("id")
            team1 = m.get("team1", "")
            team2 = m.get("team2", "")

            d_txt = self._fmt_weekday(m.get("date", ""))
            t_txt = (m.get("time") or "TBD").strip() or "TBD"
            v_txt = (m.get("venue") or "TBD").strip() or "TBD"

            status = self._match_status(m)
            if status == "Completed":
                status_line = m.get("result_text") or (f"Winner: {m.get('winner')}" if m.get("winner") else "Completed")
                bg = (0.65, 0.65, 0.65, 1)
            elif status == "Running":
                status_line = "Status: Running"
                bg = (0.96, 0.58, 0.11, 1)
            else:
                status_line = "Status: Pending"
                bg = (0.16, 0.63, 0.95, 1) if idx % 2 == 1 else (0.13, 0.78, 0.42, 1)

            row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(78))

            num_lbl = Label(
                text=str(idx),
                size_hint_x=None,
                width=dp(36),
                color=(1, 1, 1, 1),
                bold=True,
                halign="center",
                valign="middle",
            )
            num_lbl.text_size = (dp(36), dp(78))
            self._row_bg(num_lbl, bg)

            main_btn = Button(
                text=f"{team1} vs {team2}\n{d_txt} | {t_txt} | {v_txt}\n{status_line}",
                size_hint_x=1,
                background_normal="",
                background_down="",
                background_color=bg,
                color=(1, 1, 1, 1),
                halign="center",
                valign="middle",
            )
            main_btn.text_size = (dp(320), None)
            main_btn.bind(on_release=partial(self.app.start_match, match_id))

            edit_btn = Button(
                text="Edit",
                size_hint_x=None,
                width=dp(64),
                background_normal="",
                background_down="",
                background_color=(0.18, 0.18, 0.18, 1),
                color=(1, 1, 1, 1),
                disabled=readonly,
            )
            edit_btn.bind(on_release=lambda *_args, mid=match_id: self.open_edit_fixture_popup(mid))

            row.add_widget(num_lbl)
            row.add_widget(main_btn)
            row.add_widget(edit_btn)
            grid.add_widget(row)

    # ============================================================
    # Auto-generate fixtures
    # ============================================================
    def auto_generate_fixtures(self):
        if self._readonly_guard():
            return

        if not self.app.current_tournament:
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        teams = sorted(list((tour.get("teams") or {}).keys()))

        if len(teams) < 2:
            Popup(
                title="Auto Generate",
                content=Label(text="Add at least 2 teams first.", color=(0.1, 0.12, 0.16, 1)),
                size_hint=(None, None),
                size=(dp(320), dp(180)),
            ).open()
            return

        matches = tour.setdefault("matches", [])
        existing_pairs = set()
        for m in matches:
            a = m.get("team1", "")
            b = m.get("team2", "")
            if a and b:
                existing_pairs.add(tuple(sorted([a, b])))

        added = 0
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                pair = tuple(sorted([teams[i], teams[j]]))
                if pair in existing_pairs:
                    continue
                matches.append({
                    "id": str(uuid4()),
                    "team1": teams[i],
                    "team2": teams[j],
                    "winner": None,
                    "result_text": "",
                    "round": 1,
                    "status": "pending",
                    "date": "",
                    "time": "",
                    "venue": "TBD",
                })
                added += 1

        self.app.save_db(db)
        self.refresh_matches()

        Popup(
            title="Auto Generate",
            content=Label(text=f"Generated {added} new fixture(s).", color=(0.1, 0.12, 0.16, 1)),
            size_hint=(None, None),
            size=(dp(320), dp(180)),
        ).open()