# cricket_app.py
from __future__ import annotations

import random

from kivy.app import App
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout

from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.button import Button

from db_storage import JsonDB
from scoring_engine import MatchScorer

from controllers.menu_controller import MenuController
from controllers.tournament_controller import TournamentController
from controllers.scoring_controller import ScoringController
from controllers.team_admin_controller import TeamAdminController

from auth_manager import AuthManager
from controllers.auth_controller import AuthController


class AppHeader(BoxLayout):
    title_text = StringProperty("Cricket")


class CricketApp(App):
    match_readonly = BooleanProperty(False)

    # Auth state
    is_logged_in = BooleanProperty(False)
    logged_in_user_name = StringProperty("")

    # Scoring state
    score_phase = StringProperty("setup")
    pending_extra = StringProperty("")

    score_team1 = StringProperty("")
    score_team2 = StringProperty("")

    score_summary = StringProperty("")
    score_detail = StringProperty("")
    score_target = StringProperty("")
    score_status = StringProperty("")
    score_result = StringProperty("")

    batting_first_team = StringProperty("")
    score_view_team = StringProperty("")

    toss_winner = StringProperty("")
    toss_choice = StringProperty("")

    toss_angle = NumericProperty(0.0)
    toss_coin_text = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.root_widget = None
        self.sm = None
        self.db: JsonDB | None = None

        self.current_tournament: str | None = None
        self.current_match_id: str | None = None
        self.scorer: MatchScorer | None = None

        self.menu: MenuController | None = None
        self.tournaments: TournamentController | None = None
        self.scoring: ScoringController | None = None
        self.team_admin: TeamAdminController | None = None

        self.auth: AuthManager | None = None
        self.auth_ctrl: AuthController | None = None

        self._sm_guard = False

    def build(self):
        print("[DEBUG] CricketApp.build() starting")
        try:
            Window.size = (430, 820)
        except Exception:
            pass

        Window.clearcolor = (1, 1, 1, 1)
        Window.bind(on_keyboard=self._on_keyboard)

        self.root_widget = Builder.load_file("ui.kv")
        self.sm = self.root_widget.ids.sm

        try:
            self.sm.bind(current=self._on_sm_current)
        except Exception:
            pass

        # DB
        self.db = JsonDB(self.user_data_dir, filename="data.json")
        data = self.db.load()
        if "tournaments" not in data:
            data = {"tournaments": {}}
            self.db.save(data)

        # =====================================================
        # AUTH BACKEND CONFIG
        # =====================================================
        BACKEND_URL = "https://cricket5-backend.onrender.com"
        # For Android on same Wi-Fi:
        # BACKEND_URL = "http://192.168.1.14:8080"

        APP_API_KEY = "Cr1ck3t5@Auth#2026"

        SSL_VERIFY = False
        CA_BUNDLE_PATH = None

        self.auth = AuthManager(
            self.user_data_dir,
            backend_url=BACKEND_URL,
            app_api_key=APP_API_KEY,
            ssl_verify=SSL_VERIFY,
            ca_bundle_path=CA_BUNDLE_PATH,
        )
        self.auth_ctrl = AuthController(self)

        # Controllers
        self.menu = MenuController(self)
        self.tournaments = TournamentController(self)
        self.scoring = ScoringController(self)
        self.team_admin = TeamAdminController(self)

        self.menu.set_menu_closed()
        Window.bind(size=self.menu.on_window_resize)

        self.set_header_title("Cricket")

        self.auth_ctrl.check_auto_login()

        print("[DEBUG] CricketApp.build() completed")
        return self.root_widget

    def _on_sm_current(self, sm, screen_name: str):
        if self._sm_guard:
            return
        allowed = {"login", "register", "forgot_password"}
        if screen_name in allowed:
            return
        if not bool(self.is_logged_in):
            try:
                self._sm_guard = True
                sm.current = "login"
            finally:
                self._sm_guard = False

    def _on_keyboard(self, window, key, scancode, codepoint, modifiers):
        if key == 27:
            if self.menu and self.menu.is_menu_open():
                self.menu.close_menu()
                return True
            return True
        return False

    def shade(self, rgba, factor=0.88):
        r, g, b, a = rgba
        return (
            max(0, min(1, r * factor)),
            max(0, min(1, g * factor)),
            max(0, min(1, b * factor)),
            a,
        )

    def request_exit(self):
        if self.menu and self.menu.is_menu_open():
            self.menu.close_menu()
        else:
            self.stop()

    def set_header_title(self, title: str):
        try:
            self.root_widget.ids.app_header.title_text = title
        except Exception:
            pass

    # ---------------- DB helpers ----------------
    def load_db(self) -> dict:
        return self.db.load()

    def save_db(self, data: dict):
        self.db.save(data)

    # ============================================================
    # ✅ NEW: owner-only delete helpers
    # ============================================================
    def _me_email(self) -> str:
        try:
            if self.auth:
                return (self.auth.get_logged_in_email() or "").strip().lower()
        except Exception:
            pass
        return ""

    def _can_delete_tournament(self, tour: dict) -> bool:
        """
        Owner-only delete rules:
        - Remote tournaments (downloaded via search) cannot be deleted from this UI.
          (they should disappear automatically when unshared, handled in tournament_controller)
        - If owner_email exists => only that email can delete
        - Legacy (no owner_email) => allow delete
        """
        if not isinstance(tour, dict):
            return False

        if bool(tour.get("is_remote", False)):
            return False

        owner = (tour.get("owner_email") or "").strip().lower()
        if not owner:
            return True  # legacy tournaments created before owner tracking

        me = self._me_email()
        return bool(me) and (me == owner)

    def _popup_info(self, title: str, msg: str):
        Popup(
            title=title,
            content=Label(text=msg, color=(0.1, 0.12, 0.16, 1)),
            size_hint=(None, None),
            size=(dp(420), dp(220)),
        ).open()

    def get_team_players(self, team_name: str) -> list[str]:
        if not self.current_tournament:
            return []
        db = self.load_db()
        t = db["tournaments"].get(self.current_tournament, {})
        players = t.get("teams", {}).get(team_name, [])
        out: list[str] = []
        for p in players:
            if isinstance(p, dict):
                n = str(p.get("name", "")).strip()
                if n:
                    out.append(n)
            else:
                s = str(p).strip()
                if s and " (" in s and s.endswith(")"):
                    s = s.split(" (", 1)[0].strip()
                if s:
                    out.append(s)
        return out

    def find_match_in_db(self, db: dict, tournament_name: str, match_id: str):
        matches = db["tournaments"][tournament_name]["matches"]
        for m in matches:
            if m.get("id") == match_id:
                return m
        return None

    def save_scorecard_to_db(self):
        if not (self.current_tournament and self.current_match_id and self.scorer):
            return
        db = self.load_db()
        m = self.find_match_in_db(db, self.current_tournament, self.current_match_id)
        if not m:
            return
        m["scorecard"] = self.scorer.to_dict()
        self.save_db(db)

    # ---------------- Menu wrappers ----------------
    def open_menu(self): self.menu.open_menu()
    def close_menu(self): self.menu.close_menu()
    def toggle_menu(self): self.menu.toggle_menu()

    def go(self, screen_name: str):
        try:
            self.close_menu()
        except Exception:
            pass
        try:
            if self.sm and screen_name in self.sm.screen_names:
                self.sm.current = screen_name
                return
        except Exception:
            pass
        try:
            self.menu.go(screen_name)
        except Exception:
            pass

    # ---------------- Tournament wrappers ----------------
    def create_tournament(self, name, fmt): self.tournaments.create_tournament(name, fmt)
    def load_tournaments(self): self.tournaments.load_tournaments()
    def open_tournament(self, name, *_): self.tournaments.open_tournament(name)
    def add_team(self, team, players): self.tournaments.add_team(team, players)
    def auto_generate_fixtures(self): self.tournaments.auto_generate_fixtures()
    def refresh_matches(self): self.tournaments.refresh_matches()
    def refresh_points(self): self.tournaments.refresh_points()
    def refresh_teams(self): self.tournaments.refresh_teams()
    def open_create_fixture_popup(self): self.tournaments.open_create_fixture_popup()

    def open_team_matches(self, team_name: str): self.tournaments.open_team_matches(team_name)
    def back_from_team_matches(self): self.tournaments.back_from_team_matches()

    # ============================================================
    # ✅ UPDATED: delete tournament (owner-only)
    # ============================================================
    def open_delete_tournament_popup(self):
        db = self.load_db()
        tournaments = db.get("tournaments", {}) or {}

        # ✅ show only deletable tournaments for this user
        names = []
        for name in sorted(tournaments.keys()):
            tour = tournaments.get(name)
            if self._can_delete_tournament(tour if isinstance(tour, dict) else {}):
                names.append(name)

        if not names:
            self._popup_info(
                "Delete Tournament",
                "No tournaments available to delete.\n(Only the creator can delete.)",
            )
            return

        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        layout.add_widget(Label(
            text="Select tournament to delete",
            color=(0.1, 0.12, 0.16, 1), size_hint_y=None, height=dp(22)
        ))
        sp = Spinner(text=names[0], values=names, size_hint_y=None, height=dp(44))
        layout.add_widget(sp)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(text="Cancel")
        delete_btn = Button(
            text="Delete", background_normal="",
            background_color=(0.90, 0.18, 0.22, 1), color=(1, 1, 1, 1),
        )
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(delete_btn)
        layout.add_widget(btn_row)

        pop = Popup(
            title="Delete Tournament", content=layout,
            size_hint=(0.95, None), height=dp(240), auto_dismiss=False
        )
        cancel_btn.bind(on_release=lambda *_: pop.dismiss())

        def _do_delete(*_):
            tname = (sp.text or "").strip()
            if not tname:
                return

            db2 = self.load_db()
            tours = db2.get("tournaments", {}) or {}
            t_data = tours.get(tname)
            if not isinstance(t_data, dict) or not self._can_delete_tournament(t_data):
                self._popup_info("Read Only", "Only the creator can delete this tournament.")
                return

            # ✅ delete from cloud only if owner and shared
            tid = t_data.get("tid", "")
            if tid and t_data.get("shared"):
                try:
                    self.auth.delete_tournament_cloud(tid)
                except Exception:
                    pass

            # delete local
            if tname in tours:
                del tours[tname]
                db2["tournaments"] = tours
                self.save_db(db2)

            pop.dismiss()
            self.load_tournaments()

        delete_btn.bind(on_release=_do_delete)
        pop.open()

    # ---------------- Scoring wrappers ----------------
    def start_match(self, match_id: str, *_):
        self.scoring.start_match(match_id)
        self.batting_first_team = (self.scorer.batting_first or "") if self.scorer else ""

        try:
            if self.scorer and self.scorer.current_innings():
                self.score_view_team = self.scorer.current_innings().batting_team
            else:
                self.score_view_team = self.score_team1
        except Exception:
            self.score_view_team = self.score_team1

        if self.score_phase == "setup":
            self.toss_winner = ""
            self.toss_choice = ""
            self._update_toss_status()

    def score_choose_batting(self, team_name: str): self.scoring.score_choose_batting(team_name)
    def score_start(self, overs_text: str): self.scoring.score_start(overs_text)
    def score_start_second_innings(self): self.scoring.score_start_second_innings()

    def score_apply_selectors(self, striker: str, non_striker: str, bowler: str):
        self.scoring.score_apply_selectors(striker, non_striker, bowler)

    def score_set_view_team(self, team_name: str):
        self.scoring.score_set_view_team(team_name)

    def score_set_batters(self, striker: str, non_striker: str): self.scoring.score_set_batters(striker, non_striker)
    def score_set_bowler(self, bowler: str): self.scoring.score_set_bowler(bowler)

    def score_set_extra(self, extra_code: str): self.scoring.score_set_extra(extra_code)
    def score_clear_extra(self): self.scoring.score_clear_extra()
    def score_add_runs(self, runs: int): self.scoring.score_add_runs(runs)
    def open_wicket_popup(self): self.scoring.open_wicket_popup()
    def score_undo(self): self.scoring.score_undo()
    def score_redo(self): self.scoring.score_redo()
    def score_reset_innings(self): self.scoring.score_reset_innings()
    
    # ✅ END INNINGS with FULL DEBUG
    def score_end_innings(self):
        """✅ DEBUG: END INNINGS BUTTON"""
        print("\n" + "="*60)
        print("[DEBUG] CricketApp.score_end_innings() CALLED")
        print(f"[DEBUG] current_tournament: {self.current_tournament}")
        print(f"[DEBUG] current_match_id: {self.current_match_id}")
        print(f"[DEBUG] scorer exists: {self.scorer is not None}")
        print(f"[DEBUG] match_readonly: {self.match_readonly}")
        print(f"[DEBUG] score_phase: {self.score_phase}")
        print(f"[DEBUG] scoring controller: {self.scoring}")
        print("="*60 + "\n")
        
        if self.scoring:
            self.scoring.score_end_innings()
        else:
            print("[ERROR] Scoring controller is None!")
            self.score_status = "ERROR: Scoring controller not initialized"

    # ---------------- Toss helpers ----------------
    def _update_toss_status(self):
        if self.toss_winner and self.toss_choice:
            self.score_status = f"Toss: {self.toss_winner} won, chose to {self.toss_choice}"
        elif self.toss_winner:
            self.score_status = f"Toss: {self.toss_winner} won, choice pending"
        elif self.toss_choice:
            self.score_status = f"Toss: choice {self.toss_choice}, winner pending"
        else:
            self.score_status = "Select toss winner and choice."

    def toss_select_winner(self, team_name: str):
        team_name = (team_name or "").strip()
        if not team_name:
            return
        self.toss_winner = team_name
        self._update_toss_status()

    def toss_select_choice(self, choice: str):
        choice = (choice or "").strip().lower()
        if choice not in ("bat", "field"):
            return
        self.toss_choice = choice
        self._update_toss_status()

    def score_start_from_toss(self, overs_text: str):
        if self.score_phase != "setup":
            self.score_status = "Match already started."
            return
        if not self.toss_winner or not self.toss_choice:
            self.score_status = "Select toss winner and bat/field."
            return

        team1 = self.score_team1
        team2 = self.score_team2
        if not team1 or not team2:
            self.score_status = "Teams not loaded."
            return

        if self.toss_choice == "bat":
            batting_first = self.toss_winner
        else:
            batting_first = team2 if self.toss_winner == team1 else team1

        self.score_choose_batting(batting_first)
        self.score_start(overs_text)

    # ---------------- Team admin wrappers ----------------
    def open_add_team_admin(self): self.team_admin.open_add_team()
    def open_update_team_admin(self): self.team_admin.open_update_team()
    def open_delete_team_admin(self): self.team_admin.open_delete_team()
    def team_admin_add_player(self): self.team_admin.add_player_row()
    def team_admin_delete_player(self): self.team_admin.delete_selected_players()
    def team_admin_move_up(self): self.team_admin.move_selected_up()
    def team_admin_move_down(self): self.team_admin.move_selected_down()
    def team_admin_save_team(self): self.team_admin.save_team()
    def team_admin_back(self): self.team_admin.back_to_tournament()
    def team_admin_confirm_delete(self): self.team_admin.confirm_delete_selected_team()
    def team_admin_toggle_select_all(self, active: bool): self.team_admin.toggle_select_all(active)

    # ---------------- Auth wrappers ----------------
    def auth_go_login(self): self.auth_ctrl.go_login()
    def auth_go_register(self): self.auth_ctrl.go_register()
    def auth_go_forgot_password(self): self.auth_ctrl.go_forgot_password()

    def auth_login_submit(self): self.auth_ctrl.login_submit()
    def auth_register_submit(self): self.auth_ctrl.register_submit()
    def auth_forgot_submit(self): self.auth_ctrl.forgot_submit()

    def auth_go_profile(self):
        if self.auth_ctrl.require_login():
            self.auth_ctrl.go_profile()

    def auth_profile_save(self): self.auth_ctrl.profile_save()
    def auth_profile_change_password(self): self.auth_ctrl.profile_change_password()
    def auth_profile_logout(self): self.auth_ctrl.profile_logout()
    def auth_profile_delete_account(self): self.auth_ctrl.profile_delete_account()


if __name__ == "__main__":
    CricketApp().run()