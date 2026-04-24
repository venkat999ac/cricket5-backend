# controllers/team_admin_controller.py
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

# Native picker (Windows/Android/iOS) if available
try:
    from plyer import filechooser as plyer_filechooser  # type: ignore
except Exception:
    plyer_filechooser = None


class TeamAdminController:
    ROLE_OPTIONS = (
        "Batter",
        "Bowler",
        "Allrounder",
        "WK&Batter",
        "Fast Bowler",
        "Leg Spinner",
        "Off Spinner",
    )

    def __init__(self, app):
        self.app = app

        # modes: "add", "update", "delete"
        self._mode: str = "add"

        # for update/delete selection
        self._selected_team: str | None = None

        # player row widgets state
        self._player_rows: list[dict[str, Any]] = []

    # ==========================================================
    # ✅ READONLY GUARD (new)
    # ==========================================================
    def _readonly_guard(self) -> bool:
        """Block action if user does not own this tournament. Returns True if blocked."""
        try:
            if self.app.tournaments and self.app.tournaments.is_tournament_readonly():
                self._info_popup(
                    "Read Only",
                    "You can only view this tournament.\nOnly the creator can edit."
                )
                return True
        except Exception:
            pass
        return False

    # ==========================================================
    # Small UI helpers
    # ==========================================================
    def _info_popup(self, title: str, msg: str):
        Popup(
            title=title,
            content=Label(text=msg, color=(0.10, 0.12, 0.16, 1)),
            size_hint=(None, None),
            size=(dp(360), dp(220)),
        ).open()

    def _get_scr(self, name: str):
        return self.app.sm.get_screen(name)

    def _normalize_role(self, role: str) -> str:
        r = (role or "").strip()
        if r in self.ROLE_OPTIONS:
            return r
        rl = r.lower()
        for opt in self.ROLE_OPTIONS:
            if opt.lower() == rl:
                return opt
        return "Batter"

    def _safe_int(self, s: str, default: str = "") -> str:
        s = (s or "").strip()
        if not s:
            return default
        try:
            int(s)
            return s
        except Exception:
            return default

    def _get_downloads_path(self) -> str:
        """
        Default path = Downloads folder across platforms (best-effort).

        Windows/macOS/Linux: ~/Downloads
        Android: /storage/emulated/0/Download or EXTERNAL_STORAGE/Download
        iOS: sandbox -> <user_data_dir>/Downloads
        """
        candidates: list[Path] = []

        # Desktop
        try:
            home = Path.home()
            candidates.append(home / "Downloads")
            candidates.append(home / "OneDrive" / "Downloads")
        except Exception:
            pass

        # Android common paths
        ext = os.environ.get("EXTERNAL_STORAGE")
        if ext:
            candidates.append(Path(ext) / "Download")
        candidates.append(Path("/storage/emulated/0/Download"))
        candidates.append(Path("/sdcard/Download"))

        # iOS/sandbox fallback
        try:
            candidates.append(Path(self.app.user_data_dir) / "Downloads")
        except Exception:
            pass

        for p in candidates:
            try:
                if p.exists() and p.is_dir():
                    return str(p)
            except Exception:
                continue

        # create sandbox Downloads if possible
        try:
            sandbox_dl = Path(self.app.user_data_dir) / "Downloads"
            sandbox_dl.mkdir(parents=True, exist_ok=True)
            return str(sandbox_dl)
        except Exception:
            return os.getcwd()

    def _extract_player_fields(self, p: Any) -> tuple[str, str, str]:
        """
        Returns (name, age, role) from:
        - dict: {"name":..., "age":..., "specialist":...}
        - legacy string: "Name (Role)" or "Name"
        """
        if isinstance(p, dict):
            name = str(p.get("name", "")).strip()
            age = str(p.get("age", "")).strip()
            role = str(p.get("specialist", "") or p.get("role", "")).strip()
            role = self._normalize_role(role)
            return name, age, role

        s = str(p).strip()
        name = s
        role = "Batter"
        if " (" in s and s.endswith(")"):
            name, role = s.rsplit(" (", 1)
            role = role[:-1].strip()
            name = name.strip()
        role = self._normalize_role(role)
        return name, "", role

    # ==========================================================
    # Entry points from CricketApp wrappers
    # ==========================================================
    def open_add_team(self):
        if not self.app.current_tournament:
            self._info_popup("Teams", "Open a tournament first.")
            return
        # ✅ Readonly guard
        if self._readonly_guard():
            return
        self._mode = "add"
        self._selected_team = None
        self._open_team_editor(team_name="", players=[])

    def open_update_team(self):
        if not self.app.current_tournament:
            self._info_popup("Teams", "Open a tournament first.")
            return
        # ✅ Readonly guard
        if self._readonly_guard():
            return
        self._mode = "update"
        self._selected_team = None
        self._open_team_picker(purpose="update")

    def open_delete_team(self):
        if not self.app.current_tournament:
            self._info_popup("Teams", "Open a tournament first.")
            return
        # ✅ Readonly guard
        if self._readonly_guard():
            return
        self._mode = "delete"
        self._selected_team = None
        self._open_team_picker(purpose="delete")

    # ==========================================================
    # Team picker screen (Update/Delete)
    # ==========================================================
    def _open_team_picker(self, purpose: str):
        db = self.app.load_db()
        tour = db.get("tournaments", {}).get(self.app.current_tournament, {})
        teams = sorted(list((tour.get("teams") or {}).keys()))

        scr = self._get_scr("team_pick")
        grid = scr.ids.team_pick_grid
        grid.clear_widgets()

        scr.ids.delete_confirm_box.opacity = 0
        scr.ids.delete_confirm_box.disabled = True
        scr.ids.delete_selected_lbl.text = "No team selected"

        if not teams:
            scr.ids.pick_hint.text = "No teams available"
            grid.add_widget(Button(text="No teams found.", size_hint_y=None, height=dp(44), disabled=True))
            self.app.go("team_pick")
            return

        scr.ids.pick_hint.text = "Tap a team"
        for tname in teams:
            btn = Button(
                text=tname,
                size_hint_y=None,
                height=dp(46),
                background_normal="",
                background_down="",
                background_color=(0.16, 0.63, 0.95, 1),
                color=(1, 1, 1, 1),
            )
            if purpose == "update":
                btn.bind(on_release=lambda _btn, tn=tname: self._pick_team_for_update(tn))
            else:
                btn.bind(on_release=lambda _btn, tn=tname: self._pick_team_for_delete(tn))
            grid.add_widget(btn)

        self.app.go("team_pick")

    def _pick_team_for_update(self, team_name: str):
        self._selected_team = team_name
        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        players = (tour.get("teams", {}) or {}).get(team_name, []) or []
        self._open_team_editor(team_name=team_name, players=players)

    def _pick_team_for_delete(self, team_name: str):
        self._selected_team = team_name
        scr = self._get_scr("team_pick")
        scr.ids.delete_selected_lbl.text = f"Selected: {team_name}"
        scr.ids.delete_confirm_box.opacity = 1
        scr.ids.delete_confirm_box.disabled = False

    def confirm_delete_selected_team(self):
        if not (self.app.current_tournament and self._selected_team):
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        teams = tour.get("teams", {}) or {}

        tname = self._selected_team
        if tname not in teams:
            self._info_popup("Delete Team", "Team not found.")
            return

        del teams[tname]
        tour["teams"] = teams

        if "points" in tour and isinstance(tour["points"], dict):
            tour["points"].pop(tname, None)

        matches = tour.get("matches", []) or []
        matches = [m for m in matches if tname not in (m.get("team1"), m.get("team2"))]
        tour["matches"] = matches

        self.app.save_db(db)
        self.back_to_tournament()

    # ==========================================================
    # Team editor screen (Add/Update team + players)
    # ==========================================================
    def _open_team_editor(self, team_name: str, players: list):
        scr = self._get_scr("team_editor")
        scr.ids.team_name_inp.text = (team_name or "").strip()

        grid = scr.ids.player_table_grid
        grid.clear_widgets()
        self._player_rows = []

        try:
            scr.ids.header_sel_all.active = False
        except Exception:
            pass

        if players:
            for p in players:
                name, age, role = self._extract_player_fields(p)
                self._add_player_row_to_grid(name=name, age=age, role=role)
        else:
            self._add_player_row_to_grid(name="", age="", role="Batter")

        self.app.go("team_editor")

    def _add_player_row_to_grid(self, name: str = "", age: str = "", role: str = "Batter"):
        scr = self._get_scr("team_editor")
        grid = scr.ids.player_table_grid

        cb = CheckBox(size_hint_x=None, width=dp(32))
        idx_lbl = Label(
            text=str(len(self._player_rows) + 1),
            size_hint_x=None,
            width=dp(34),
            color=(0.10, 0.12, 0.16, 1),
            halign="center",
            valign="middle",
        )
        idx_lbl.text_size = (dp(34), dp(40))

        name_inp = TextInput(text=(name or ""), multiline=False, size_hint_x=None, width=dp(180))
        age_inp = TextInput(
            text=self._safe_int(age, default=""),
            multiline=False,
            input_filter="int",
            size_hint_x=None,
            width=dp(52),
        )
        role_sp = Spinner(
            text=self._normalize_role(role),
            values=list(self.ROLE_OPTIONS),
            size_hint_x=None,
            width=dp(120),
        )

        grid.add_widget(cb)
        grid.add_widget(idx_lbl)
        grid.add_widget(name_inp)
        grid.add_widget(age_inp)
        grid.add_widget(role_sp)

        self._player_rows.append({
            "cb": cb,
            "idx": idx_lbl,
            "name": name_inp,
            "age": age_inp,
            "role": role_sp,
        })

    def _reindex_rows(self):
        for i, r in enumerate(self._player_rows, start=1):
            r["idx"].text = str(i)

    def add_player_row(self):
        self._add_player_row_to_grid(name="", age="", role="Batter")

    def toggle_select_all(self, active: bool):
        for r in self._player_rows:
            r["cb"].active = bool(active)

    def delete_selected_players(self):
        if not self._player_rows:
            return

        scr = self._get_scr("team_editor")
        grid = scr.ids.player_table_grid

        keep: list[dict[str, Any]] = []
        for r in self._player_rows:
            if r["cb"].active:
                for w in (r["cb"], r["idx"], r["name"], r["age"], r["role"]):
                    try:
                        grid.remove_widget(w)
                    except Exception:
                        pass
            else:
                keep.append(r)

        self._player_rows = keep

        if not self._player_rows:
            self._add_player_row_to_grid(name="", age="", role="Batter")

        try:
            scr.ids.header_sel_all.active = False
        except Exception:
            pass

        self._reindex_rows()

    def move_selected_up(self):
        self._move_selected(delta=-1)

    def move_selected_down(self):
        self._move_selected(delta=1)

    def _move_selected(self, delta: int):
        if not self._player_rows:
            return

        selected_indices = [i for i, r in enumerate(self._player_rows) if r["cb"].active]
        if not selected_indices:
            return

        if delta < 0:
            if selected_indices[0] == 0:
                return
            for i in selected_indices:
                self._player_rows[i - 1], self._player_rows[i] = self._player_rows[i], self._player_rows[i - 1]
        else:
            if selected_indices[-1] == len(self._player_rows) - 1:
                return
            for i in reversed(selected_indices):
                self._player_rows[i + 1], self._player_rows[i] = self._player_rows[i], self._player_rows[i + 1]

        scr = self._get_scr("team_editor")
        grid = scr.ids.player_table_grid
        grid.clear_widgets()

        for r in self._player_rows:
            grid.add_widget(r["cb"])
            grid.add_widget(r["idx"])
            grid.add_widget(r["name"])
            grid.add_widget(r["age"])
            grid.add_widget(r["role"])

        self._reindex_rows()

    def save_team(self):
        if not self.app.current_tournament:
            return

        scr = self._get_scr("team_editor")
        team_name = (scr.ids.team_name_inp.text or "").strip()

        if not team_name:
            self._info_popup("Save Team", "Team name is required.")
            return

        players_out: list[dict[str, Any]] = []
        for r in self._player_rows:
            nm = (r["name"].text or "").strip()
            if not nm:
                continue
            age_txt = (r["age"].text or "").strip()
            role = self._normalize_role(r["role"].text)
            players_out.append({"name": nm, "age": age_txt, "specialist": role})

        if not players_out:
            self._info_popup("Save Team", "Add at least 1 player with a name.")
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]
        tour.setdefault("teams", {})
        tour["teams"][team_name] = players_out

        tour.setdefault("points", {})
        if team_name not in tour["points"]:
            tour["points"][team_name] = {"played": 0, "won": 0, "lost": 0, "nr": 0, "points": 0, "nrr": 0.0}

        self.app.save_db(db)
        self.back_to_tournament()

    def back_to_tournament(self):
        if self.app.current_tournament:
            self.app.set_header_title(self.app.current_tournament)

        try:
            self.app.refresh_teams()
        except Exception:
            pass
        try:
            self.app.refresh_points()
        except Exception:
            pass
        try:
            self.app.refresh_matches()
        except Exception:
            pass

        self.app.go("tournament_detail")

    # ==========================================================
    # Import Teams (CSV) - OVERWRITE mode with Browse-first UI
    # ==========================================================
    def open_import_csv_popup(self):
        """Backward-compatible alias."""
        self.open_import_teams_popup()

    def open_import_teams_popup(self):
        if not self.app.current_tournament:
            self._info_popup("Import Teams", "Open a tournament first.")
            return

        # ✅ Readonly guard
        if self._readonly_guard():
            return

        selected_path = {"value": ""}

        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))

        root.add_widget(Label(
            text="Import Teams from CSV (Overwrite mode).",
            size_hint_y=None,
            height=dp(22),
            color=(0.10, 0.12, 0.16, 1),
        ))

        file_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(10))
        file_inp = TextInput(text="", hint_text="No file selected", multiline=False, readonly=True)
        browse_btn = Button(
            text="Browse",
            size_hint_x=None,
            width=dp(110),
            background_normal="",
            background_down="",
            background_color=(0.55, 0.22, 0.90, 1),
            color=(1, 1, 1, 1),
        )
        file_row.add_widget(file_inp)
        file_row.add_widget(browse_btn)
        root.add_widget(file_row)

        root.add_widget(Label(
            text="CSV headers: Team, Player Name, Age, Role",
            size_hint_y=None,
            height=dp(20),
            color=(0.35, 0.35, 0.35, 1),
        ))

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(
            text="Cancel",
            background_normal="",
            background_color=(0.45, 0.49, 0.55, 1),
            color=(1, 1, 1, 1),
        )
        import_btn = Button(
            text="Import (Overwrite)",
            background_normal="",
            background_color=(0.16, 0.63, 0.95, 1),
            color=(1, 1, 1, 1),
            disabled=True,
        )
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(import_btn)
        root.add_widget(btn_row)

        pop = Popup(
            title="Import Teams (CSV)",
            content=root,
            size_hint=(0.95, None),
            height=dp(260),
            auto_dismiss=False,
        )

        def _set_selected(path: str | None):
            p = (path or "").strip()
            if not p:
                return
            selected_path["value"] = p
            file_inp.text = p
            import_btn.disabled = False

        def _browse_native(*_):
            start_path = self._get_downloads_path()

            if plyer_filechooser is None:
                _browse_kivy()
                return

            def _on_selection(selection):
                if not selection:
                    return
                picked = selection[0]
                Clock.schedule_once(lambda *_dt: _set_selected(picked), 0)

            try:
                plyer_filechooser.open_file(
                    on_selection=_on_selection,
                    filters=[("CSV Files", "*.csv")],
                    path=start_path,
                    multiple=False,
                )
            except Exception:
                _browse_kivy()

        def _browse_kivy():
            start_path = self._get_downloads_path()

            chooser = FileChooserListView(
                path=start_path,
                filters=["*.csv", "*.CSV"],
                multiselect=False,
                dirselect=False,
                show_hidden=False,
            )
            try:
                chooser.selection = []
            except Exception:
                pass

            c_root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
            c_root.add_widget(Label(
                text=f"Select a CSV file (Downloads)\n{start_path}",
                size_hint_y=None,
                height=dp(44),
                color=(0.10, 0.12, 0.16, 1),
            ))

            sc = ScrollView()
            sc.add_widget(chooser)
            c_root.add_widget(sc)

            c_btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
            c_cancel = Button(
                text="Cancel",
                background_normal="",
                background_color=(0.45, 0.49, 0.55, 1),
                color=(1, 1, 1, 1),
            )
            c_select = Button(
                text="Select",
                background_normal="",
                background_color=(0.13, 0.78, 0.42, 1),
                color=(1, 1, 1, 1),
            )
            c_btn_row.add_widget(c_cancel)
            c_btn_row.add_widget(c_select)
            c_root.add_widget(c_btn_row)

            c_pop = Popup(title="Browse CSV", content=c_root, size_hint=(0.95, 0.9), auto_dismiss=False)

            def _select(*_a):
                if not chooser.selection:
                    self._info_popup("Browse CSV", "Please tap a CSV file to select.")
                    return
                _set_selected(chooser.selection[0])
                c_pop.dismiss()

            c_cancel.bind(on_release=lambda *_: c_pop.dismiss())
            c_select.bind(on_release=_select)
            c_pop.open()

        def _do_import(*_):
            path = (selected_path["value"] or "").strip()
            if not path:
                self._info_popup("Import Teams", "Please choose a CSV file first.")
                return
            pop.dismiss()
            self.import_teams_from_csv_path(path)

        cancel_btn.bind(on_release=lambda *_: pop.dismiss())
        browse_btn.bind(on_release=_browse_native)
        import_btn.bind(on_release=_do_import)

        pop.open()

    def import_teams_from_csv_path(self, csv_path: str):
        if not self.app.current_tournament:
            return

        csv_path = (csv_path or "").strip()
        if not csv_path or not os.path.exists(csv_path):
            self._info_popup("Import Teams", f"File not found:\n{csv_path}")
            return

        teams_out: dict[str, list[dict[str, Any]]] = {}
        total_rows = 0
        imported_players = 0
        skipped_rows = 0
        invalid_role_count = 0

        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    self._info_popup("Import Teams", "CSV has no header row.")
                    return

                hdr_map = {h.strip().lower(): h for h in reader.fieldnames if h}

                def _col(*names: str) -> str | None:
                    for n in names:
                        if n in hdr_map:
                            return hdr_map[n]
                    return None

                col_team = _col("team", "team name", "team_name")
                col_player = _col("player name", "player", "player_name", "name")
                col_age = _col("age")
                col_role = _col("role", "specialist")

                if not col_team or not col_player:
                    self._info_popup(
                        "Import Teams",
                        "Missing required columns.\nRequired: Team, Player Name\nOptional: Age, Role",
                    )
                    return

                for row in reader:
                    total_rows += 1
                    team = (row.get(col_team) or "").strip()
                    player = (row.get(col_player) or "").strip()
                    if not team or not player:
                        skipped_rows += 1
                        continue

                    age = ""
                    if col_age:
                        age = self._safe_int(str(row.get(col_age) or "").strip(), default="")

                    role_raw = ""
                    if col_role:
                        role_raw = str(row.get(col_role) or "").strip()

                    role_norm = self._normalize_role(role_raw)
                    if role_raw and role_norm == "Batter" and role_raw.strip().lower() != "batter":
                        invalid_role_count += 1

                    teams_out.setdefault(team, [])
                    teams_out[team].append({"name": player, "age": age, "specialist": role_norm})
                    imported_players += 1

        except Exception as e:
            self._info_popup("Import Teams", f"Failed to import:\n{e}")
            return

        if not teams_out:
            self._info_popup("Import Teams", "No valid rows found to import.")
            return

        db = self.app.load_db()
        tour = db["tournaments"][self.app.current_tournament]

        # OVERWRITE
        tour["teams"] = teams_out
        tour["matches"] = []
        tour["points"] = {}

        self.app.save_db(db)
        self.back_to_tournament()

        self._info_popup(
            "Import Completed",
            f"Imported teams: {len(teams_out)}\n"
            f"Imported players: {imported_players}\n"
            f"CSV rows read: {total_rows}\n"
            f"Skipped rows: {skipped_rows}\n"
            f"Invalid roles defaulted: {invalid_role_count}\n\n"
            f"Note: Fixtures cleared (overwrite import).",
        )

    # ==========================================================
    # ✅ Export Teams (CSV)
    # ==========================================================
    def open_export_teams_popup(self):
        """
        Exports current tournament teams into a CSV.
        Uses native save dialog when available (plyer.save_file), otherwise:
        - choose destination folder (native choose_dir if available) OR
        - Kivy folder picker fallback
        """
        if not self.app.current_tournament:
            self._info_popup("Export Teams", "Open a tournament first.")
            return

        # ensure we have something to export
        db = self.app.load_db()
        tour = db.get("tournaments", {}).get(self.app.current_tournament, {})
        teams = tour.get("teams", {}) or {}
        if not teams:
            self._info_popup("Export Teams", "No teams found to export.")
            return

        default_dir = self._get_downloads_path()
        default_name = f"{self.app.current_tournament}_teams.csv".replace(" ", "_")

        selected_dir = {"value": default_dir}
        selected_file = {"value": ""}  # if native save returns full file path

        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))

        root.add_widget(Label(
            text="Export Teams to CSV",
            size_hint_y=None,
            height=dp(22),
            color=(0.10, 0.12, 0.16, 1),
        ))

        # Destination folder row
        dir_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(10))
        dir_inp = TextInput(text=default_dir, multiline=False, readonly=True)
        browse_btn = Button(
            text="Browse",
            size_hint_x=None,
            width=dp(110),
            background_normal="",
            background_down="",
            background_color=(0.55, 0.22, 0.90, 1),
            color=(1, 1, 1, 1),
        )
        dir_row.add_widget(dir_inp)
        dir_row.add_widget(browse_btn)
        root.add_widget(dir_row)

        # File name row
        name_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(10))
        name_inp = TextInput(text=default_name, multiline=False)
        name_row.add_widget(name_inp)
        root.add_widget(name_row)

        root.add_widget(Label(
            text="CSV columns: Team, Player Name, Age, Role",
            size_hint_y=None,
            height=dp(20),
            color=(0.35, 0.35, 0.35, 1),
        ))

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(
            text="Cancel",
            background_normal="",
            background_color=(0.45, 0.49, 0.55, 1),
            color=(1, 1, 1, 1),
        )
        export_btn = Button(
            text="Export",
            background_normal="",
            background_color=(0.16, 0.63, 0.95, 1),
            color=(1, 1, 1, 1),
        )
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(export_btn)
        root.add_widget(btn_row)

        pop = Popup(
            title="Export Teams",
            content=root,
            size_hint=(0.95, None),
            height=dp(320),
            auto_dismiss=False,
        )

        def _set_dir(p: str | None):
            p = (p or "").strip()
            if not p:
                return
            selected_dir["value"] = p
            dir_inp.text = p
            selected_file["value"] = ""  # clear any previous save_file path

        def _browse_export_native(*_):
            start = self._get_downloads_path()

            # Prefer native "Save As" dialog when available
            if plyer_filechooser is not None and hasattr(plyer_filechooser, "save_file"):
                def _on_sel(selection):
                    if not selection:
                        return
                    picked = selection[0]
                    # update on main thread
                    def _apply(*_dt):
                        selected_file["value"] = picked
                        try:
                            pp = Path(picked)
                            _set_dir(str(pp.parent))
                            name_inp.text = pp.name
                        except Exception:
                            pass
                    Clock.schedule_once(_apply, 0)

                try:
                    plyer_filechooser.save_file(
                        on_selection=_on_sel,
                        filters=[("CSV Files", "*.csv")],
                        path=start,
                    )
                    return
                except Exception:
                    pass

            # else choose directory (native) if possible
            if plyer_filechooser is not None and hasattr(plyer_filechooser, "choose_dir"):
                def _on_dir(selection):
                    if not selection:
                        return
                    picked_dir = selection[0]
                    Clock.schedule_once(lambda *_dt: _set_dir(picked_dir), 0)

                try:
                    plyer_filechooser.choose_dir(on_selection=_on_dir, path=start)
                    return
                except Exception:
                    pass

            # fallback Kivy folder picker
            _browse_export_kivy()

        def _browse_export_kivy():
            start = self._get_downloads_path()

            chooser = FileChooserListView(
                path=start,
                dirselect=True,
                multiselect=False,
                show_hidden=False,
            )
            try:
                chooser.selection = []
            except Exception:
                pass

            c_root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
            c_root.add_widget(Label(
                text=f"Select destination folder\n{start}",
                size_hint_y=None,
                height=dp(44),
                color=(0.10, 0.12, 0.16, 1),
            ))

            sc = ScrollView()
            sc.add_widget(chooser)
            c_root.add_widget(sc)

            c_btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
            c_cancel = Button(
                text="Cancel",
                background_normal="",
                background_color=(0.45, 0.49, 0.55, 1),
                color=(1, 1, 1, 1),
            )
            c_select = Button(
                text="Select Folder",
                background_normal="",
                background_color=(0.13, 0.78, 0.42, 1),
                color=(1, 1, 1, 1),
            )
            c_btn_row.add_widget(c_cancel)
            c_btn_row.add_widget(c_select)
            c_root.add_widget(c_btn_row)

            c_pop = Popup(title="Choose Folder", content=c_root, size_hint=(0.95, 0.9), auto_dismiss=False)

            def _select(*_a):
                if not chooser.path:
                    return
                # In dirselect mode, selection contains chosen dir; if empty, use current path
                dest = chooser.selection[0] if chooser.selection else chooser.path
                _set_dir(dest)
                c_pop.dismiss()

            c_cancel.bind(on_release=lambda *_: c_pop.dismiss())
            c_select.bind(on_release=_select)
            c_pop.open()

        def _do_export(*_):
            # if native save dialog filled a full file path, use it
            full_path = (selected_file["value"] or "").strip()

            if not full_path:
                out_dir = (selected_dir["value"] or "").strip()
                fname = (name_inp.text or "").strip() or "teams.csv"
                if not fname.lower().endswith(".csv"):
                    fname += ".csv"
                full_path = str(Path(out_dir) / fname)

            pop.dismiss()
            self.export_teams_to_csv_path(full_path)

        cancel_btn.bind(on_release=lambda *_: pop.dismiss())
        browse_btn.bind(on_release=_browse_export_native)
        export_btn.bind(on_release=_do_export)

        pop.open()

    def export_teams_to_csv_path(self, out_path: str):
        if not self.app.current_tournament:
            return

        out_path = (out_path or "").strip()
        if not out_path:
            self._info_popup("Export Teams", "Invalid output path.")
            return

        try:
            out_dir = str(Path(out_path).parent)
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        db = self.app.load_db()
        tour = db.get("tournaments", {}).get(self.app.current_tournament, {})
        teams = tour.get("teams", {}) or {}

        if not teams:
            self._info_popup("Export Teams", "No teams found to export.")
            return

        rows_written = 0
        try:
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Team", "Player Name", "Age", "Role"])

                for team_name in sorted(teams.keys()):
                    players = teams.get(team_name, []) or []
                    for p in players:
                        name, age, role = self._extract_player_fields(p)
                        if not name:
                            continue
                        w.writerow([team_name, name, age, role])
                        rows_written += 1

        except Exception as e:
            self._info_popup("Export Teams", f"Failed to export:\n{e}")
            return

        self._info_popup(
            "Export Completed",
            f"Exported to:\n{out_path}\n\nPlayers exported: {rows_written}",
        )