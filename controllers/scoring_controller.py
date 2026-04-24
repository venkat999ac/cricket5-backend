# controllers/scoring_controller.py
from __future__ import annotations

import random

from kivy.clock import Clock
from kivy.animation import Animation
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.graphics import Color, Rectangle, Ellipse

from scoring_engine import MatchScorer, overs_str, BowlerStat


class ScoringController:
    EXTRA_TIMEOUT_SEC = 2.0

    WICKET_REASONS = [
        "Bowled",
        "LBW",
        "Caught",
        "Run Out",
        "Stumped",
        "Hit Wicket",
    ]

    def __init__(self, app):
        self.app = app
        self._extra_clear_ev = None

    # ---------- basic helpers ----------
    def _safe(self, fn, fallback_status: str):
        try:
            fn()
        except Exception as e:
            self.app.score_status = f"{fallback_status}: {type(e).__name__} - {e}"
            try:
                self._refresh_score_ui()
            except Exception:
                pass

    def _inn(self):
        """LIVE innings (all scoring actions apply here)."""
        return self.app.scorer.current_innings() if self.app.scorer else None

    # ✅ View innings helper (display only)
    def _view_inn(self):
        """
        Returns the innings to DISPLAY based on app.score_view_team.
        Scoring actions continue using _inn() (live innings).
        """
        if not self.app.scorer:
            return None

        team = (getattr(self.app, "score_view_team", "") or "").strip()
        if not team:
            return self._inn()

        i1 = getattr(self.app.scorer, "innings1", None)
        i2 = getattr(self.app.scorer, "innings2", None)

        if i1 and getattr(i1, "batting_team", "") == team:
            return i1
        if i2 and getattr(i2, "batting_team", "") == team:
            return i2

        # If requested team innings doesn't exist yet, fall back to live innings
        return self._inn()

    def _score_screen(self):
        try:
            return self.app.sm.get_screen("score")
        except Exception:
            return None

    def _is_placeholder(self, text: str) -> bool:
        t = (text or "").strip().lower()
        return t in ("", "select", "select bowler", "select batter", "select next batter", "select fielder")

    # ---------- readonly helpers ----------
    def _is_readonly(self) -> bool:
        return bool(getattr(self.app, "match_readonly", False))

    def _readonly_block(self, msg: str = "Completed match (view only). Editing disabled."):
        self.app.score_status = msg
        try:
            self._refresh_score_ui()
        except Exception:
            pass

    # ✅ NEW: Permission check for match creator
    def _is_match_creator(self) -> bool:
        """
        True ONLY if logged-in user is the tournament owner.
        """
        try:
            if not self.app.current_tournament:
                return False

            db = self.app.load_db()
            tour = db.get("tournaments", {}).get(self.app.current_tournament, {})

            owner = (tour.get("owner_email") or "").strip().lower()
            if not owner:
                return False  # No owner → safest is readonly

            me = ""
            if self.app.auth:
                me = (self.app.auth.get_logged_in_email() or "").strip().lower()

            return bool(me) and (me == owner)

        except Exception:
            return False

    def _match_creator_guard(self, msg: str = "Only tournament creator can enter scores."):
        """Guard clause for scoring actions - blocks non-creators."""
        if not self._is_match_creator():
            self._readonly_block(msg)
            return True
        return False

    # ---------- match status helpers ----------
    def _get_match_row(self):
        if not (self.app.current_tournament and self.app.current_match_id):
            return None, None
        db = self.app.load_db()
        m = self.app.find_match_in_db(db, self.app.current_tournament, self.app.current_match_id)
        return db, m

    def _db_match_is_completed(self, match_row: dict) -> bool:
        if not match_row:
            return False
        if (match_row.get("status") or "").strip().lower() == "completed":
            return True
        if bool(match_row.get("winner")):
            return True
        if (match_row.get("result_text") or "").strip():
            return True
        return False

    def _set_match_status(self, status: str):
        """Keeps your existing behavior, but also stores scorecard when possible."""
        if not (self.app.current_tournament and self.app.current_match_id):
            return
        db = self.app.load_db()
        m = self.app.find_match_in_db(db, self.app.current_tournament, self.app.current_match_id)
        if not m:
            return

        # never override completed with running
        if self._db_match_is_completed(m) and status != "completed":
            return

        m["status"] = status
        try:
            if self.app.scorer:
                m["scorecard"] = self.app.scorer.to_dict()
        except Exception:
            pass
        self.app.save_db(db)
        try:
            self.app.refresh_matches()
        except Exception:
            pass

    def _infer_match_state(self, match_row: dict) -> tuple[bool, bool]:
        is_completed = self._db_match_is_completed(match_row)
        is_running = (match_row.get("status") == "running") or bool(match_row.get("scorecard"))
        if is_completed:
            is_running = False
        return is_completed, is_running

    def _finalize_match_if_complete(self):
        if not self.app.scorer:
            return
        if not self.app.scorer.is_match_complete():
            return

        winner, result_txt = self.app.scorer.result()
        db, m = self._get_match_row()
        if not db or not m:
            return

        m["status"] = "completed"
        m["winner"] = winner
        m["result_text"] = result_txt
        m["scorecard"] = self.app.scorer.to_dict()
        self.app.save_db(db)

        self.app.match_readonly = True
        self.app.score_phase = "complete"
        self.app.score_status = result_txt

        try:
            self.app.refresh_matches()
        except Exception:
            pass
        try:
            self.app.refresh_points()
        except Exception:
            pass

    # ✅ toggle view team (display only)
    def score_set_view_team(self, team_name: str):
        self._safe(lambda: self._score_set_view_team(team_name), "View team error")

    def _score_set_view_team(self, team_name: str):
        t = (team_name or "").strip()
        if not t:
            return
        self.app.score_view_team = t
        self._refresh_score_ui()

    # ---------- role helpers ----------
    def _role(self, team: str, player_name: str) -> str:
        try:
            if self.app.scorer and hasattr(self.app.scorer, "team_player_roles"):
                r = (self.app.scorer.team_player_roles.get(team, {}).get(player_name, "") or "").strip()
                if r:
                    return r
        except Exception:
            pass
        try:
            r = (self.app.get_player_role(team, player_name) or "").strip()
            return r
        except Exception:
            return ""

    def _disp(self, team: str, player_name: str) -> str:
        if not player_name:
            return ""
        r = self._role(team, player_name)
        return f"{player_name} ({r})" if r else player_name

    def _ensure_role_snapshot(self):
        if not self.app.scorer:
            return
        if not hasattr(self.app.scorer, "team_player_roles"):
            return

        for team in (self.app.scorer.team1, self.app.scorer.team2):
            current = self.app.scorer.team_player_roles.get(team, {})
            if current:
                continue

            roles = {}
            try:
                recs = self.app.get_team_player_records(team)
                for p in recs:
                    name = str(p.get("name", "")).strip()
                    role = str(p.get("specialist", p.get("role", ""))).strip()
                    if name:
                        roles[name] = role
            except Exception:
                roles = {}

            try:
                self.app.scorer.set_player_roles(team, roles)
            except Exception:
                pass

    # ---------- extra handling ----------
    def _schedule_extra_autoclear(self, code: str):
        if self._extra_clear_ev:
            self._extra_clear_ev.cancel()
        self._extra_clear_ev = Clock.schedule_once(lambda *_: self._auto_clear_extra(code), self.EXTRA_TIMEOUT_SEC)

    def _auto_clear_extra(self, code: str):
        if self.app.pending_extra == code:
            self.app.pending_extra = ""
            self.app.score_status = "Extra mode expired"
            self._refresh_score_ui()

    # =========================
    # Toss (kept)
    # =========================
    def _reset_toss_state(self):
        self.app.toss_state = "not_done"
        self.app.toss_coin_text = "TOSS"
        self.app.toss_winner = ""
        self.app.toss_choice = ""
        self.app.toss_angle = 0.0

    def toss_flip(self):
        print("[DEBUG] scoring.toss_flip() pressed")

        if self._match_creator_guard("Only creator can manage toss."):
            return

        if self.app.score_phase != "setup":
            self.app.score_status = "Toss allowed only before match start."
            return

        if self._is_readonly():
            return self._readonly_block()

        if self.app.toss_state == "flipping":
            return

        self.app.toss_state = "flipping"
        self.app.toss_coin_text = "..."
        self.app.toss_winner = ""
        self.app.toss_choice = ""
        self.app.toss_angle = 0.0
        self.app.score_status = "Flipping coin..."
        self._refresh_score_ui()

        anim = Animation(toss_angle=720.0, duration=0.8)
        anim.bind(on_complete=lambda *_: self._finish_toss())
        anim.start(self.app)

    def _finish_toss(self):
        face = random.choice(["HEADS", "TAILS"])
        self.app.toss_coin_text = face
        self.app.toss_state = "done"
        self.app.score_status = f"Toss: {face}. Select winner and Bat/Field."
        print(f"[DEBUG] toss result={face}")
        self._refresh_score_ui()

    def toss_select_winner(self, team_name: str):
        if self._match_creator_guard("Only creator can select toss winner."):
            return

        if self.app.score_phase != "setup":
            return
        if self.app.toss_state != "done":
            self.app.score_status = "Do toss first."
            return
        self.app.toss_winner = (team_name or "").strip()
        self._refresh_score_ui()

    def toss_select_choice(self, choice: str):
        if self._match_creator_guard("Only creator can select bat/field."):
            return

        if self.app.score_phase != "setup":
            return
        if self.app.toss_state != "done":
            self.app.score_status = "Do toss first."
            return
        choice = (choice or "").strip().lower()
        if choice not in ("bat", "field"):
            return
        self.app.toss_choice = choice
        self._refresh_score_ui()

    def score_start_from_toss(self, overs_text: str):
        if self._match_creator_guard("Only creator can start match."):
            return

        if self.app.score_phase != "setup":
            self.app.score_status = "Match already started."
            return
        if self.app.toss_state != "done":
            self.app.score_status = "Complete toss first."
            return
        if not self.app.toss_winner or not self.app.toss_choice:
            self.app.score_status = "Select toss winner and Bat/Field."
            return

        team1 = self.app.score_team1
        team2 = self.app.score_team2

        if self.app.toss_choice == "bat":
            batting_first = self.app.toss_winner
        else:
            batting_first = team2 if self.app.toss_winner == team1 else team1

        self.score_choose_batting(batting_first)
        self.score_start(overs_text)

    # ---------- popups ----------
    def _popup_select_next_batter(self, out_end: str):
        if self._match_creator_guard("Only creator can select batter."):
            return

        inn = self._inn()
        if not inn or inn.is_complete():
            return

        choices = inn.available_next_batters()
        if not choices:
            # all out
            self.app.score_status = "All out. Innings completed."
            self._sync_phase_after_change()
            self._refresh_score_ui()
            return

        sp = Spinner(text="Select next batter", values=choices, size_hint_y=None, height=dp(44))
        content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        content.add_widget(Label(
            text="Select next batter (required)",
            color=(0.1, 0.12, 0.16, 1),
            size_hint_y=None,
            height=dp(24),
        ))
        content.add_widget(sp)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(
            text="Cancel",
            background_normal="",
            background_color=(0.45, 0.49, 0.55, 1),
            color=(1, 1, 1, 1),
        )
        ok_btn = Button(
            text="OK",
            background_normal="",
            background_color=(0.16, 0.63, 0.95, 1),
            color=(1, 1, 1, 1),
        )
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(ok_btn)
        content.add_widget(btn_row)

        pop = Popup(title="Next Batter", content=content, size_hint=(0.92, None), height=dp(260), auto_dismiss=False)

        def _cancel(*_):
            self.app.score_status = "Please select batter to continue"
            self._refresh_score_ui()
            pop.dismiss()

        def _ok(*_):
            try:
                if sp.text in choices:
                    self.app.scorer.force_new_batter(out_end, sp.text)
                    self.app.save_scorecard_to_db()
                    self._set_match_status("running")
                    self.app.score_status = f"New batter: {sp.text}"
                    self._refresh_score_ui()
                pop.dismiss()
            except Exception as e:
                self.app.score_status = f"Next batter error: {type(e).__name__} - {e}"
                self._refresh_score_ui()

        cancel_btn.bind(on_release=_cancel)
        ok_btn.bind(on_release=_ok)
        pop.open()

    def _popup_select_new_bowler(self):
        if self._match_creator_guard("Only creator can select bowler."):
            return

        inn = self._inn()
        if not inn or inn.is_complete():
            return

        bowl_team = inn.bowling_team
        pool = self.app.scorer.team_players.get(bowl_team, []) or [f"{bowl_team} Player {i}" for i in range(1, 12)]
        choices = [p for p in pool if p != inn.last_over_bowler] if inn.last_over_bowler else list(pool)
        if not choices:
            choices = pool

        sp = Spinner(text="Select bowler", values=choices, size_hint_y=None, height=dp(44))
        content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        content.add_widget(Label(
            text="Over completed. Select next bowler",
            color=(0.1, 0.12, 0.16, 1),
            size_hint_y=None,
            height=dp(24),
        ))
        content.add_widget(sp)

        ok_btn = Button(
            text="OK",
            size_hint_y=None,
            height=dp(44),
            background_normal="",
            background_color=(0.16, 0.63, 0.95, 1),
            color=(1, 1, 1, 1),
        )
        content.add_widget(ok_btn)

        pop = Popup(title="Change Bowler", content=content, size_hint=(0.92, None), height=dp(220), auto_dismiss=False)

        def _ok(*_):
            try:
                if sp.text in choices:
                    self.app.scorer.set_bowler(sp.text)
                    self.app.score_status = f"Bowler: {sp.text}"
                    self.app.save_scorecard_to_db()
                    self._set_match_status("running")
                    self._refresh_score_ui()
                pop.dismiss()
            except Exception as e:
                self.app.score_status = f"Bowler error: {type(e).__name__} - {e}"
                self._refresh_score_ui()

        ok_btn.bind(on_release=_ok)
        pop.open()

    # ---------- match entry ----------
    def start_match(self, match_id: str):
        self._safe(lambda: self._start_match(match_id), "Start match failed")

    def _start_match(self, match_id: str):
        if not self.app.current_tournament:
            self.app.score_status = "Open a tournament first"
            return

        db = self.app.load_db()
        m = self.app.find_match_in_db(db, self.app.current_tournament, match_id)
        if not m:
            self.app.score_status = "Match not found"
            return

        self.app.current_match_id = match_id

        team1, team2 = m["team1"], m["team2"]
        self.app.score_team1 = team1
        self.app.score_team2 = team2

        is_completed, is_running = self._infer_match_state(m)

        if m.get("scorecard"):
            self.app.scorer = MatchScorer.from_dict(m["scorecard"])
        else:
            self.app.scorer = MatchScorer(team1, team2)

        self.app.scorer.set_players(team1, self.app.get_team_players(team1))
        self.app.scorer.set_players(team2, self.app.get_team_players(team2))

        self._ensure_role_snapshot()
        self.app.save_scorecard_to_db()

        self.app.pending_extra = ""
        self._reset_toss_state()

        # default view team when opening match (if not already set)
        if not (getattr(self.app, "score_view_team", "") or "").strip():
            inn_live = self._inn()
            self.app.score_view_team = (inn_live.batting_team if inn_live else team1)

        if self._db_match_is_completed(m) or is_completed:
            self.app.match_readonly = True
            self.app.score_phase = "complete"
            self.app.score_status = (m.get("result_text") or "").strip() or "Completed match (view only)"
            self.app.set_header_title("Match Statistics")
        else:
            self.app.match_readonly = False
            self.app.score_phase = "setup" if self.app.scorer.innings_no == 0 else "live"
            self.app.score_status = "Tap Toss → winner → bat/field → Start" if self.app.score_phase == "setup" else "Continue scoring"
            
            # ✅ NEW: Check if user is match creator
            is_creator = self._is_match_creator()

            if is_creator:
                self.app.set_header_title("Scoring (Admin)")
            else:
                self.app.set_header_title("Match Statistics (View Only)")
                self.app.score_status = "You have view-only access to this match."

            # 🔥 IMPORTANT FIX
            self.app.match_readonly = not is_creator

            if is_running and m.get("status") != "running":
                m["status"] = "running"
                m["scorecard"] = self.app.scorer.to_dict()
                self.app.save_db(db)
                try:
                    self.app.refresh_matches()
                except Exception:
                    pass

        scr = self._score_screen()
        if scr and "match_title" in scr.ids:
            scr.ids.match_title.text = f"{team1} vs {team2}"

        try:
            self.app.close_menu()
        except Exception:
            pass

        self.app.sm.current = "score"
        self._refresh_score_ui()

    # ---------- setup / innings ----------
    def score_choose_batting(self, team_name: str):
        if self._match_creator_guard("Only creator can choose batting team."):
            return

        self._safe(lambda: self._score_choose_batting(team_name), "Batting first error")

    def _score_choose_batting(self, team_name: str):
        if not self.app.scorer:
            return
        self.app.scorer.set_batting_first(team_name)
        self.app.batting_first_team = team_name
        self.app.score_status = f"Batting first: {team_name}"
        self.app.save_scorecard_to_db()
        self._refresh_score_ui()

    def score_start(self, overs_text: str):
        if self._match_creator_guard("Only creator can start innings."):
            return

        self._safe(lambda: self._score_start(overs_text), "Start innings error")

    def _score_start(self, overs_text: str):
        if not self.app.scorer:
            return

        try:
            ov = int(str(overs_text).strip())
            ov = max(1, min(50, ov))
        except Exception:
            ov = 20

        if not self.app.scorer.batting_first:
            self.app.score_status = "Please select batting first"
            return

        if self.app.scorer.innings_no == 0:
            self.app.scorer.start_first_innings(max_overs=ov)

        self.app.score_phase = "live"
        self.app.score_status = "Live scoring started"
        self.app.pending_extra = ""
        self.app.save_scorecard_to_db()
        self._set_match_status("running")

        # default view to live batting team
        inn_live = self._inn()
        if inn_live:
            self.app.score_view_team = inn_live.batting_team

        self._refresh_score_ui()

    def score_start_second_innings(self):
        if self._match_creator_guard("Only creator can start second innings."):
            return

        self._safe(self._score_start_second_innings, "2nd innings error")

    def _score_start_second_innings(self):
        if not self.app.scorer:
            return
        self.app.scorer.start_second_innings()
        self.app.score_phase = "live"
        self.app.score_status = "Second innings started"
        self.app.pending_extra = ""
        self.app.save_scorecard_to_db()
        self._set_match_status("running")

        inn_live = self._inn()
        if inn_live:
            self.app.score_view_team = inn_live.batting_team

        self._refresh_score_ui()

    # ==========================================================
    # score_apply_selectors (safe single-call Apply)
    # ==========================================================
    def score_apply_selectors(self, striker: str, non_striker: str, bowler: str):
        if self._match_creator_guard("Only creator can apply selectors."):
            return

        self._safe(lambda: self._score_apply_selectors(striker, non_striker, bowler), "Apply selectors error")

    def _score_apply_selectors(self, striker: str, non_striker: str, bowler: str):
        inn = self._inn()
        if not inn:
            self.app.score_status = "Start innings first"
            self._refresh_score_ui()
            return

        if inn.is_complete() or self.app.score_phase in ("break", "complete"):
            self.app.score_status = "Innings completed. Selectors locked."
            self._refresh_score_ui()
            return

        striker = (striker or "").strip()
        non_striker = (non_striker or "").strip()
        bowler = (bowler or "").strip()

        # wicket pending
        end = getattr(inn, "require_new_batter_end", None)
        if end in ("str", "non"):
            chosen = striker if end == "str" else non_striker
            if self._is_placeholder(chosen):
                self.app.score_status = "Please select batter to continue"
                self._refresh_score_ui()
                return
            self.app.scorer.force_new_batter("non" if end == "non" else "str", chosen)
            self.app.save_scorecard_to_db()
            self._set_match_status("running")
            self.app.score_status = f"New batter: {chosen}"
            self._refresh_score_ui()
            return

        # over ended
        if getattr(inn, "require_new_bowler", False):
            if self._is_placeholder(bowler):
                self.app.score_status = "Over completed. Select NEW bowler."
                self._refresh_score_ui()
                return
            self.app.scorer.set_bowler(bowler)
            self.app.save_scorecard_to_db()
            self._set_match_status("running")
            self.app.score_status = f"Bowler: {bowler}"
            self._refresh_score_ui()
            return

        # normal
        if self._is_placeholder(striker) or self._is_placeholder(non_striker) or striker == non_striker:
            self.app.score_status = "Select two different batters"
            self._refresh_score_ui()
            return
        if self._is_placeholder(bowler):
            self.app.score_status = "Select a bowler"
            self._refresh_score_ui()
            return

        self.app.scorer.set_batters(striker, non_striker)
        self.app.scorer.set_bowler(bowler)
        self.app.save_scorecard_to_db()
        self._set_match_status("running")
        self.app.score_status = "Selectors applied"
        self._refresh_score_ui()

    # ---------- selectors (backward compatible with your KV) ----------
    def score_set_batters(self, striker: str, non_striker: str):
        if self._match_creator_guard("Only creator can change batters."):
            return

        self._safe(lambda: self._score_set_batters(striker, non_striker), "Set batters error")

    def _score_set_batters(self, striker: str, non_striker: str):
        inn = self._inn()
        if not inn:
            self.app.score_status = "Start innings first"
            return

        striker = (striker or "").strip()
        non_striker = (non_striker or "").strip()

        # if wicket pending, allow this call to commit next batter too
        end = getattr(inn, "require_new_batter_end", None)
        if end in ("str", "non"):
            chosen = striker if end == "str" else non_striker
            if self._is_placeholder(chosen):
                self.app.score_status = "Please select batter to continue"
                self._refresh_score_ui()
                return
            self.app.scorer.force_new_batter("non" if end == "non" else "str", chosen)
            self.app.score_status = f"New batter: {chosen}"
            self.app.save_scorecard_to_db()
            self._set_match_status("running")
            self._refresh_score_ui()
            return

        self.app.scorer.set_batters(striker, non_striker)
        self.app.score_status = f"Batters: {inn.striker_name()} / {inn.non_striker_name()}"
        self.app.save_scorecard_to_db()
        self._set_match_status("running")
        self._refresh_score_ui()

    def score_set_bowler(self, name: str):
        if self._match_creator_guard("Only creator can change bowler."):
            return

        self._safe(lambda: self._score_set_bowler(name), "Set bowler error")

    def _score_set_bowler(self, name: str):
        inn = self._inn()
        if not inn:
            self.app.score_status = "Start innings first"
            return

        name = (name or "").strip()
        if self._is_placeholder(name):
            self.app.score_status = "Select bowler"
            self._refresh_score_ui()
            return

        self.app.scorer.set_bowler(name)
        self.app.score_status = f"Bowler: {name}"
        self.app.save_scorecard_to_db()
        self._set_match_status("running")
        self._refresh_score_ui()

    # ---------- extras ----------
    def score_set_extra(self, extra_code: str):
        if self._match_creator_guard("Only creator can set extras."):
            return

        self._safe(lambda: self._score_set_extra(extra_code), "Extra mode error")

    def _score_set_extra(self, extra_code: str):
        inn = self._inn()
        if not inn or self.app.score_phase != "live":
            self.app.score_status = "Start innings first"
            return
        
        if self.app.scorer.innings_no == 2:
            target = self.app.scorer.target()
        if target and inn.runs >= target:
            self.app.score_status = "🏁 Target reached! Extras disabled"
            self._refresh_score_ui()
            return

        if inn.is_complete():
            self.app.score_status = "Innings completed (Use END INNINGS or UNDO)"
            #return
        if getattr(inn, "require_new_batter_end", None):
            self.app.score_status = "Please select batter to continue"
            self._refresh_score_ui()
            return

        self.app.pending_extra = extra_code
        self.app.score_status = f"Extra active: {extra_code} (tap runs within 2s)"
        self._schedule_extra_autoclear(extra_code)
        self._refresh_score_ui()

    def score_clear_extra(self):
        if self._match_creator_guard("Only creator can clear extras."):
            return

        self.app.pending_extra = ""
        self.app.score_status = "Extra cleared"
        self._refresh_score_ui()

    # ---------- scoring ----------
    def score_add_runs(self, runs: int):
        if self._match_creator_guard("Only creator can add runs."):
            return

        self._safe(lambda: self._score_add_runs(runs), "Add runs error")

    def _score_add_runs(self, runs: int):
        inn = self._inn()
        if not inn or self.app.score_phase != "live":
            self.app.score_status = "Start innings first"
            return

        # ✅ NEW FIX: Stop scoring after target reached
        if self.app.scorer.innings_no == 2:
            target = self.app.scorer.target()
            if target and inn.runs >= target:
                self.app.score_status = "🏁 Target reached! Click 'END INNINGS'"
                self._refresh_score_ui()
                return

        if getattr(inn, "require_new_batter_end", None):
            self.app.score_status = "Please select batter to continue"
            self._refresh_score_ui()
            return

        old_lb = inn.legal_balls

        mode = self.app.pending_extra
        self.app.pending_extra = ""

        if mode == "WD":
            self.app.scorer.add_wide(run_by_running=runs)
        elif mode == "NB":
            self.app.scorer.add_no_ball(bat_runs=runs)
        elif mode == "B":
            self.app.scorer.add_bye(runs)
        elif mode == "LB":
            self.app.scorer.add_leg_bye(runs)
        else:
            self.app.scorer.add_runs(runs)

        self.app.save_scorecard_to_db()
        self._set_match_status("running")

        self._sync_phase_after_change()
        self._refresh_score_ui()

        inn2 = self._inn()
        if inn2 and inn2.legal_balls != old_lb and (inn2.legal_balls % 6 == 0) and getattr(inn2, "require_new_bowler", False):
            self._popup_select_new_bowler()

    # ---------- wicket popup (crash-proof) ----------
    def open_wicket_popup(self):
        if self._match_creator_guard("Only creator can record wickets."):
            return

        self._safe(self._open_wicket_popup, "Wicket popup error")

    def _open_wicket_popup(self):
        inn = self._inn()
        if not inn or self.app.score_phase != "live":
            self.app.score_status = "Start innings first"
            self._refresh_score_ui()
            return

        if self.app.scorer.innings_no == 2:
            target = self.app.scorer.target()
        if target and inn.runs >= target:
            self.app.score_status = "🏁 Target reached! Cannot add wicket"
            self._refresh_score_ui()
            return

        if inn.is_complete():
            self.app.score_status = "Innings completed (Use END INNINGS or UNDO)"
            self._refresh_score_ui()
            #return

        if getattr(inn, "require_new_batter_end", None):
            self.app.score_status = "Please select batter to continue"
            self._refresh_score_ui()
            return

        if getattr(inn, "require_new_bowler", False) or not inn.current_bowler:
            self.app.score_status = "Select NEW bowler first"
            self._refresh_score_ui()
            return

        nb_mode = (self.app.pending_extra == "NB")
        reasons = ["Run Out"] if nb_mode else list(self.WICKET_REASONS)

        bowl_team = inn.bowling_team
        fielders = self.app.scorer.team_players.get(bowl_team, []) or [f"{bowl_team} Player {i}" for i in range(1, 12)]

        out_end_sp = Spinner(text="Striker", values=["Striker", "Non-striker"], size_hint_y=None, height=dp(44))
        reason_sp = Spinner(text=reasons[0], values=reasons, size_hint_y=None, height=dp(44))
        fielder_sp = Spinner(text="Select fielder", values=fielders, size_hint_y=None, height=dp(44))
        runs_sp = Spinner(text="0", values=[str(i) for i in range(0, 4)], size_hint_y=None, height=dp(44))

        content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        content.add_widget(Label(
            text=("NB wicket: Run Out only" if nb_mode else "Wicket details"),
            color=(0.1, 0.12, 0.16, 1),
            size_hint_y=None, height=dp(22),
        ))
        content.add_widget(out_end_sp)
        content.add_widget(reason_sp)

        content.add_widget(Label(
            text="Fielder (Caught/Run Out/Stumped)",
            color=(0.25, 0.27, 0.33, 1),
            size_hint_y=None,
            height=dp(18),
        ))
        content.add_widget(fielder_sp)

        content.add_widget(Label(
            text="Runs completed (Run Out only)",
            color=(0.25, 0.27, 0.33, 1),
            size_hint_y=None,
            height=dp(18),
        ))
        content.add_widget(runs_sp)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(text="Cancel")
        ok_btn = Button(
            text="OK",
            background_normal="",
            background_color=(0.90, 0.18, 0.22, 1),
            color=(1, 1, 1, 1),
        )
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(ok_btn)
        content.add_widget(btn_row)

        pop = Popup(title="Wicket", content=content, size_hint=(0.92, None), height=dp(520), auto_dismiss=False)
        cancel_btn.bind(on_release=lambda *_: pop.dismiss())

        def _ok(*_):
            try:
                out_role = "striker" if out_end_sp.text.lower().startswith("str") else "non"
                reason = (reason_sp.text or "").strip()

                try:
                    rruns = int(runs_sp.text)
                except Exception:
                    rruns = 0

                needs_fielder = nb_mode or reason in ("Caught", "Run Out", "Stumped")
                if needs_fielder and (fielder_sp.text == "Select fielder" or self._is_placeholder(fielder_sp.text)):
                    self.app.score_status = f"Select fielder for {reason or 'Run Out'}"
                    self._refresh_score_ui()
                    return  # keep popup open

                bowler = (inn.current_bowler or "").strip()

                if nb_mode:
                    # NB wicket: Run Out only
                    self.app.pending_extra = ""
                    self.app.scorer.add_no_ball_runout(out_role=out_role, runs_by_running=rruns, fielder=fielder_sp.text)
                else:
                    if reason == "Caught":
                        # ✅ NEW: Caught & Bowled formatting
                        f = (fielder_sp.text or "").strip()
                        b = bowler
                        if b and f and f.lower() == b.lower():
                            how = f"c&b {b}"
                        else:
                            how = f"c {f} b {b}" if b else f"c {f}"
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=0)

                    elif reason == "Stumped":
                        # e.g. st Dhoni b Jadeja
                        how = f"st {fielder_sp.text} b {bowler}" if bowler else f"st {fielder_sp.text}"
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=0)

                    elif reason == "Run Out":
                        # run out not credited to bowler
                        how = (
                            f"Run Out ({fielder_sp.text}, {rruns} run)"
                            if rruns == 1
                            else f"Run Out ({fielder_sp.text}, {rruns} runs)"
                        )
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=rruns)

                    elif reason == "Bowled":
                        how = f"b {bowler}" if bowler else "Bowled"
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=0)

                    elif reason == "LBW":
                        how = f"lbw b {bowler}" if bowler else "LBW"
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=0)

                    elif reason == "Hit Wicket":
                        how = f"hit wicket b {bowler}" if bowler else "Hit Wicket"
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=0)

                    else:
                        how = f"{reason} b {bowler}" if bowler else reason
                        self.app.scorer.add_wicket(out_role=out_role, how_out=how, runs=0)

                self.app.save_scorecard_to_db()
                self._set_match_status("running")

                self._sync_phase_after_change()
                self._refresh_score_ui()

                # If wicket now requires next batter, open picker
                inn2 = self._inn()
                end = getattr(inn2, "require_new_batter_end", None) if inn2 else None
                if end in ("str", "non"):
                    self._popup_select_next_batter(end)

                pop.dismiss()

            except Exception as e:
                self.app.score_status = f"Wicket error: {type(e).__name__} - {e}"
                self._refresh_score_ui()
                # keep popup open

        ok_btn.bind(on_release=_ok)
        pop.open()

    # ---------- undo/redo/reset/end ----------
    def score_undo(self):
        if self._match_creator_guard("Only creator can undo."):
            return

        self._safe(self._score_undo, "Undo error")

    def _score_undo(self):
        if not self.app.scorer:
            return
        self.app.scorer.undo()
        self.app.pending_extra = ""
        self.app.save_scorecard_to_db()
        self._set_match_status("running")
        self.app.score_status = "Undone"
        self._sync_phase_after_change()
        self._refresh_score_ui()

    def score_redo(self):
        if self._match_creator_guard("Only creator can redo."):
            return

        self._safe(self._score_redo, "Redo error")

    def _score_redo(self):
        if not self.app.scorer:
            return
        self.app.scorer.redo()
        self.app.pending_extra = ""
        self.app.save_scorecard_to_db()
        self._set_match_status("running")
        self.app.score_status = "Redone"
        self._sync_phase_after_change()
        self._refresh_score_ui()

    def score_reset_innings(self):
        if self._match_creator_guard("Only creator can reset innings."):
            return

        self._safe(self._score_reset_innings, "Reset innings error")

    def _score_reset_innings(self):
        if not self.app.scorer:
            return
        self.app.scorer.reset_current_innings()
        self.app.pending_extra = ""
        self.app.save_scorecard_to_db()
        self._set_match_status("running")
        self.app.score_status = "Innings reset"
        self._refresh_score_ui()

    # ✅ ✅ ✅ FIXED: END INNINGS BUTTON LOGIC ✅ ✅ ✅
    def score_end_innings(self):
        """✅ PUBLIC METHOD: End innings with proper phase management"""
        print("\n" + "="*60)
        print("[DEBUG] score_end_innings() - PUBLIC METHOD called")
        print("="*60)
        
        if self._match_creator_guard("Only creator can end innings."):
            return
        
        if self._is_readonly():
            self._readonly_block("Cannot end innings on completed match")
            return
        
        try:
            self._score_end_innings()
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            self.app.score_status = f"ERROR: {type(e).__name__}"
            try:
                self._refresh_score_ui()
            except:
                pass

    def _score_end_innings(self):
        """✅ PRIVATE METHOD: Force innings completion and transition phase"""
        print("\n" + "="*80)
        print("[DEBUG] _score_end_innings() - PRIVATE METHOD called")
        print("="*80)
        
        try:
            # Get current innings
            inn = self._inn()
            if not inn:
                print("[ERROR] No current innings")
                self.app.score_status = "ERROR: No innings to end"
                self._refresh_score_ui()
                return
            
            print(f"[OK] Innings found: {inn.batting_team}")
            
            # Force completion
            print(f"[OK] Forcing innings completion (max_overs={inn.max_overs})")
            inn.legal_balls = inn.max_overs * 6
            
            # Save to DB
            print("[OK] Saving to database")
            self.app.save_scorecard_to_db()
            self._set_match_status("running")
            
            # Update phase based on innings number
            print(f"[OK] Updating phase (innings_no={self.app.scorer.innings_no})")
            
            if self.app.scorer.innings_no == 1:
                print("[OK] 1st innings ended - setting phase to BREAK")
                self.app.score_phase = "break"
                self.app.score_status = "✅ 1st Innings Complete! Click 'Start 2nd Innings' to continue."
                
            elif self.app.scorer.innings_no == 2:
                print("[OK] 2nd innings ended - checking match completion")
                if self.app.scorer.is_match_complete():
                    print("[OK] Match is complete - finalizing")
                    self._finalize_match_if_complete()
                else:
                    print("[OK] Match not complete")
                    self.app.score_phase = "complete"
                    self.app.score_status = "2nd Innings Completed"
            
            # Update UI
            print("[OK] Updating UI elements")
            self._ensure_role_snapshot()
            
            live_inn = self._inn()
            view_inn = self._view_inn()
            
            if view_inn:
                self.app.score_summary = f"{view_inn.batting_team}: {view_inn.runs}/{view_inn.wickets}"
                self.app.score_detail = (
                    f"Overs: {overs_str(view_inn.legal_balls)} / {view_inn.max_overs} | "
                    f"Bowler: {self._disp(view_inn.bowling_team, view_inn.current_bowler) if view_inn.current_bowler else '-'}"
                )
                tgt = self.app.scorer.target()
                self.app.score_target = f"Target: {tgt}" if tgt else ""
            
            scr = self._score_screen()
            if scr and live_inn:
                alive_bats = live_inn.available_batters_not_out()
                if "striker_sp" in scr.ids:
                    scr.ids.striker_sp.values = alive_bats
                    scr.ids.striker_sp.text = live_inn.striker_name()
                if "non_striker_sp" in scr.ids:
                    scr.ids.non_striker_sp.values = alive_bats
                    scr.ids.non_striker_sp.text = live_inn.non_striker_name()
                if "bowler_sp" in scr.ids:
                    bowl_team = live_inn.bowling_team
                    pool = self.app.scorer.team_players.get(bowl_team, []) or [f"{bowl_team} Player {i}" for i in range(1, 12)]
                    scr.ids.bowler_sp.values = pool
                    scr.ids.bowler_sp.text = live_inn.current_bowler or "Select bowler"
            
            self._render_batting_table()
            self._render_bowling_table()
            self._render_fow()
            self._render_last12()
            
            print(f"[SUCCESS] ✅ Innings ended! Phase={self.app.score_phase}")
            print("="*80 + "\n")
            
        except Exception as e:
            print(f"[CRITICAL ERROR] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            self.app.score_status = f"ERROR: {str(e)[:50]}"
            try:
                self._refresh_score_ui()
            except:
                pass
            print("="*80 + "\n")

    # ---------- phase sync ----------
    def _sync_phase_after_change(self):
        """
        ✅ FINAL FIX:
        - NEVER auto-end innings (1st or 2nd)
        - NEVER auto-finalize match
        - ALWAYS wait for manual "END INNINGS"
        """
        if not self.app.scorer:
            return

        inn = self._inn()
        if not inn:
            return

        # If DB says completed → lock UI
        db, m = self._get_match_row()
        if m and self._db_match_is_completed(m):
            self.app.match_readonly = True
            self.app.score_phase = "complete"
            rt = (m.get("result_text") or "").strip()
            if rt:
                self.app.score_status = rt
            return

        # ✅ IMPORTANT: REMOVE AUTO FINALIZATION
        # DO NOT call self._finalize_match_if_complete() here

        # ✅ If innings is complete → just notify (for BOTH innings)
        if inn.is_complete():
            self.app.score_phase = "live"  # stay in live until user clicks END

            if self.app.scorer.innings_no == 1:
                self.app.score_status = (
                    "⚠️ 1st Innings Complete! Click 'END INNINGS' to start 2nd innings."
                )
            elif self.app.scorer.innings_no == 2:
                self.app.score_status = (
                    "⚠️ 2nd Innings Complete! Click 'END INNINGS' to finish match."
                )
            return

        # otherwise continue live
        if self.app.scorer.innings_no > 0:
            self.app.score_phase = "live"

    # ==========================================================
    # Rendering (safe even if KV ids are missing)
    # ==========================================================
    def _row_bg(self, widget, rgba):
        with widget.canvas.before:
            Color(*rgba)
            rect = Rectangle(pos=widget.pos, size=widget.size)

        def _sync(*_):
            rect.pos = widget.pos
            rect.size = widget.size

        widget.bind(pos=_sync, size=_sync)

    def _cell(self, text: str, width: float, height: float, align="left", bold=False):
        if bold:
            text = f"[b]{text}[/b]"
        lab = Label(
            text=text,
            markup=True,
            size_hint=(None, None),
            width=width,
            height=height,
            color=(0.10, 0.12, 0.16, 1),
            halign=align,
            valign="middle",
        )
        lab.text_size = (width, height)
        return lab

    def _ball_chip_style(self, d):
        kind = getattr(d, "kind", "")
        runs_total = int(getattr(d, "runs_total", 0))

        if kind == "W":
            return ("W", (0.90, 0.18, 0.22, 1))
        if kind == "NBW":
            extra = max(0, runs_total - 1)
            return (("NbW" if extra == 0 else f"NbW+{extra}"), (0.90, 0.18, 0.22, 1))
        if kind == "WD":
            extra = max(0, runs_total - 1)
            return (("Wd" if extra == 0 else f"Wd+{extra}"), (0.13, 0.78, 0.42, 1))
        if kind == "NB":
            extra = max(0, runs_total - 1)
            return (("Nb" if extra == 0 else f"Nb+{extra}"), (0.13, 0.78, 0.42, 1))
        if kind == "B":
            return (f"B{runs_total}", (0.16, 0.63, 0.95, 1))
        if kind == "LB":
            return (f"LB{runs_total}", (0.16, 0.63, 0.95, 1))

        if runs_total == 0:
            return ("•", (0.45, 0.49, 0.55, 1))
        if runs_total == 4:
            return ("4", (0.55, 0.22, 0.90, 1))
        if runs_total == 6:
            return ("6", (0.96, 0.58, 0.11, 1))
        return (str(runs_total), (0.16, 0.63, 0.95, 1))

    def _render_last12(self):
        scr = self._score_screen()
        if not scr or "last12_box" not in scr.ids:
            return

        inn = self._view_inn()
        box = scr.ids.last12_box
        box.clear_widgets()

        if not inn or not getattr(inn, "balls", None):
            return

        recent = inn.balls[-12:]
        for d in recent:
            text, bg = self._ball_chip_style(d)
            chip = dp(34)

            w = Label(
                text=text,
                size_hint=(None, None),
                size=(chip, chip),
                font_size="11sp",
                color=(1, 1, 1, 1),
                halign="center",
                valign="middle",
            )
            w.text_size = w.size

            with w.canvas.before:
                Color(*bg)
                e = Ellipse(pos=w.pos, size=w.size)

            def _sync(_inst, _val, el=e):
                el.pos = _inst.pos
                el.size = _inst.size

            w.bind(pos=_sync, size=_sync)
            box.add_widget(w)

    def _render_batting_table(self):
        scr = self._score_screen()
        if not scr or "batting_box" not in scr.ids:
            return
        inn = self._view_inn()
        box = scr.ids.batting_box
        box.clear_widgets()
        if not inn:
            return

        W_NAME = dp(210)
        W_R = dp(40)
        W_B = dp(40)
        W_4 = dp(36)
        W_6 = dp(36)
        W_SR = dp(56)
        header_h = dp(28)
        row_h = dp(46)

        header = BoxLayout(size_hint_y=None, height=header_h, spacing=0, padding=(dp(6), 0, dp(6), 0))
        self._row_bg(header, (0.90, 0.92, 0.96, 1))
        header.add_widget(self._cell("Batter", W_NAME, header_h, "left", True))
        header.add_widget(self._cell("R", W_R, header_h, "center", True))
        header.add_widget(self._cell("B", W_B, header_h, "center", True))
        header.add_widget(self._cell("4", W_4, header_h, "center", True))
        header.add_widget(self._cell("6", W_6, header_h, "center", True))
        header.add_widget(self._cell("SR", W_SR, header_h, "center", True))
        box.add_widget(header)

        cur_str = inn.striker_name()
        cur_non = inn.non_striker_name()

        for i, name in enumerate(inn.batters_for_table(), start=1):
            st = inn.stats.get(name)
            if not st:
                continue

            tag = " *" if name == cur_str else (" •" if name == cur_non else "")
            if st.out:
                out_txt = (st.how_out or "out").strip()
                nm = f"{name}{tag}\n[color=666666][size=12sp]{out_txt}[/size][/color]"
            else:
                nm = f"{name}{tag}\n[color=666666][size=12sp]not out[/size][/color]"

            row = BoxLayout(size_hint_y=None, height=row_h, spacing=0, padding=(dp(6), 0, dp(6), 0))
            self._row_bg(row, (1, 1, 1, 1) if (i % 2 == 1) else (0.97, 0.98, 1, 1))
            row.add_widget(self._cell(nm, W_NAME, row_h, "left"))
            row.add_widget(self._cell(str(st.runs), W_R, row_h, "center"))
            row.add_widget(self._cell(str(st.balls), W_B, row_h, "center"))
            row.add_widget(self._cell(str(st.fours), W_4, row_h, "center"))
            row.add_widget(self._cell(str(st.sixes), W_6, row_h, "center"))
            row.add_widget(self._cell(f"{st.sr:.1f}", W_SR, row_h, "center"))
            box.add_widget(row)

    def _render_bowling_table(self):
        scr = self._score_screen()
        if not scr or "bowling_box" not in scr.ids:
            return
        inn = self._view_inn()
        box = scr.ids.bowling_box
        box.clear_widgets()
        if not inn:
            return

        W_NAME = dp(200)
        W_O = dp(50)
        W_R = dp(40)
        W_W = dp(40)
        W_E = dp(56)
        header_h = dp(28)
        row_h = dp(32)

        header = BoxLayout(size_hint_y=None, height=header_h, spacing=0, padding=(dp(6), 0, dp(6), 0))
        self._row_bg(header, (0.90, 0.92, 0.96, 1))
        header.add_widget(self._cell("Bowler", W_NAME, header_h, "left", True))
        header.add_widget(self._cell("O", W_O, header_h, "center", True))
        header.add_widget(self._cell("R", W_R, header_h, "center", True))
        header.add_widget(self._cell("W", W_W, header_h, "center", True))
        header.add_widget(self._cell("Econ", W_E, header_h, "center", True))
        box.add_widget(header)

        bowlers = list(inn.bowlers.values())
        if inn.current_bowler and inn.current_bowler not in inn.bowlers:
            bowlers.insert(0, BowlerStat(name=inn.current_bowler))

        for i, b in enumerate(bowlers, start=1):
            row = BoxLayout(size_hint_y=None, height=row_h, spacing=0, padding=(dp(6), 0, dp(6), 0))
            self._row_bg(row, (1, 1, 1, 1) if (i % 2 == 1) else (0.97, 0.98, 1, 1))
            nm = b.name + (" *" if (inn.current_bowler and b.name == inn.current_bowler) else "")
            row.add_widget(self._cell(nm, W_NAME, row_h, "left"))
            row.add_widget(self._cell(str(b.overs), W_O, row_h, "center"))
            row.add_widget(self._cell(str(b.runs), W_R, row_h, "center"))
            row.add_widget(self._cell(str(b.wickets), W_W, row_h, "center"))
            row.add_widget(self._cell(f"{b.economy:.1f}", W_E, row_h, "center"))
            box.add_widget(row)

    def _render_fow(self):
        scr = self._score_screen()
        if not scr or "fow_box" not in scr.ids:
            return
        inn = self._view_inn()
        box = scr.ids.fow_box
        box.clear_widgets()
        if not inn:
            return
        if not inn.fow:
            box.add_widget(Label(
                text="No wickets yet",
                color=(0.30, 0.32, 0.36, 1),
                size_hint_y=None,
                height=dp(22),
            ))
            return
        for f in inn.fow:
            txt = f"{f.get('wickets')}/{f.get('runs')} - {f.get('batter')} ({f.get('over')} ov)"
            box.add_widget(Label(text=txt, color=(0.10, 0.12, 0.16, 1), size_hint_y=None, height=dp(22)))

    # ---------- UI rendering ----------
    def _refresh_score_ui(self):
        if not self.app.scorer:
            self.app.score_summary = "Open a match from match list"
            self.app.score_detail = ""
            self.app.score_target = ""
            return

        self._ensure_role_snapshot()

        # force completed view if DB says completed
        db, m = self._get_match_row()
        if m and self._db_match_is_completed(m):
            self.app.match_readonly = True
            self.app.score_phase = "complete"
            rt = (m.get("result_text") or "").strip()
            if rt:
                self.app.score_status = rt

        live_inn = self._inn()       # selectors + scoring
        view_inn = self._view_inn()  # scoreboards + summary

        # Summary/detail uses VIEW innings
        if view_inn:
            self.app.score_summary = f"{view_inn.batting_team}: {view_inn.runs}/{view_inn.wickets}"
            self.app.score_detail = (
                f"Overs: {overs_str(view_inn.legal_balls)} / {view_inn.max_overs} | "
                f"Bowler: {self._disp(view_inn.bowling_team, view_inn.current_bowler) if view_inn.current_bowler else '-'}"
            )
            tgt = self.app.scorer.target()
            self.app.score_target = f"Target: {tgt}" if tgt else ""
        else:
            self.app.score_summary = "Innings not started"
            self.app.score_detail = ""
            self.app.score_target = ""

        # keep phase consistent when not readonly
        if not self._is_readonly():
            self._sync_phase_after_change()

        # Selectors still use LIVE innings (so scoring stays unchanged)
        scr = self._score_screen()
        if scr and live_inn:
            alive_bats = live_inn.available_batters_not_out()

            if "striker_sp" in scr.ids and "non_striker_sp" in scr.ids:
                scr.ids.striker_sp.values = alive_bats
                scr.ids.non_striker_sp.values = alive_bats
                scr.ids.striker_sp.text = live_inn.striker_name()
                scr.ids.non_striker_sp.text = live_inn.non_striker_name()

                if getattr(live_inn, "require_new_batter_end", None) == "str":
                    scr.ids.striker_sp.values = live_inn.available_next_batters()
                    scr.ids.striker_sp.text = "Select"
                elif getattr(live_inn, "require_new_batter_end", None) == "non":
                    scr.ids.non_striker_sp.values = live_inn.available_next_batters()
                    scr.ids.non_striker_sp.text = "Select"

            if "bowler_sp" in scr.ids:
                bowl_team = live_inn.bowling_team
                pool = self.app.scorer.team_players.get(bowl_team, []) or [f"{bowl_team} Player {i}" for i in range(1, 12)]
                if getattr(live_inn, "require_new_bowler", False) and live_inn.last_over_bowler:
                    pool = [p for p in pool if p != live_inn.last_over_bowler]
                scr.ids.bowler_sp.values = pool
                scr.ids.bowler_sp.text = live_inn.current_bowler or "Select bowler"
    
        # These renders use VIEW innings internally
        self._render_batting_table()
        self._render_bowling_table()
        self._render_fow()
        self._render_last12()