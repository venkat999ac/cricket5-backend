# scoring_engine.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple


def overs_str(legal_balls: int) -> str:
    return f"{legal_balls // 6}.{legal_balls % 6}"


@dataclass
class BatterStat:
    name: str
    runs: int = 0
    balls: int = 0
    dots: int = 0
    fours: int = 0
    sixes: int = 0
    out: bool = False
    how_out: str = ""

    @property
    def sr(self) -> float:
        return round((self.runs * 100.0 / self.balls), 2) if self.balls else 0.0


@dataclass
class BowlerStat:
    name: str
    balls: int = 0           # legal balls only
    runs: int = 0
    wickets: int = 0         # bowler wickets only (exclude run out)
    wides: int = 0           # count of wides
    noballs: int = 0         # count of no balls

    @property
    def overs(self) -> str:
        return overs_str(self.balls)

    @property
    def economy(self) -> float:
        return round(self.runs * 6.0 / self.balls, 2) if self.balls else 0.0


@dataclass
class Delivery:
    # kind: RUN, WD, NB, B, LB, W, NBW
    # NBW = no-ball + run out wicket
    kind: str
    runs_total: int
    runs_bat: int = 0
    legal: bool = True
    striker_idx: int = 0
    non_striker_idx: int = 1
    batsman_out_idx: Optional[int] = None
    how_out: str = ""


class Innings:
    def __init__(self, batting_team: str, bowling_team: str, batting_order: List[str], max_overs: int = 20):
        self.batting_team = batting_team
        self.bowling_team = bowling_team
        self.max_overs = max_overs

        self.batting_order: List[str] = [p.strip() for p in (batting_order or []) if p and p.strip()]
        if not self.batting_order:
            self.batting_order = [f"{batting_team} Player {i}" for i in range(1, 12)]

        self.stats: Dict[str, BatterStat] = {n: BatterStat(name=n) for n in self.batting_order}

        self.striker_i: int = 0
        self.non_striker_i: int = 1 if len(self.batting_order) > 1 else 0

        self.runs: int = 0
        self.wickets: int = 0
        self.legal_balls: int = 0
        self.balls: List[Delivery] = []

        # Undo/redo snapshots
        self._undo_stack: List[Dict] = []
        self._redo_stack: List[Dict] = []

        # bowling
        self.current_bowler: Optional[str] = None
        self.bowlers: Dict[str, BowlerStat] = {}

        # over-change rule
        self.last_over_bowler: Optional[str] = None
        self.require_new_bowler: bool = False
        self.current_bowler_balls: int = 0

        # NEW: wicket-change rule (block scoring until next batter selected)
        self.require_new_batter_end: Optional[str] = None  # "str" or "non"

        # fall of wickets
        self.fow: List[Dict] = []

    # ---------- state helpers ----------
    def max_wickets(self) -> int:
        # all out depends on configured players
        return max(0, len(self.batting_order) - 1)

    def is_complete(self) -> bool:
        return self.wickets >= self.max_wickets() or self.legal_balls >= self.max_overs * 6

    def striker_name(self) -> str:
        return self.batting_order[self.striker_i] if self.batting_order else ""

    def non_striker_name(self) -> str:
        return self.batting_order[self.non_striker_i] if self.batting_order else ""

    def swap_strike(self):
        self.striker_i, self.non_striker_i = self.non_striker_i, self.striker_i

    def available_batters_not_out(self) -> List[str]:
        return [p for p in self.batting_order if (p in self.stats and not self.stats[p].out)]

    def available_next_batters(self) -> List[str]:
        cur = {self.striker_name(), self.non_striker_name()}
        return [p for p in self.available_batters_not_out() if p not in cur]

    def set_batters(self, striker: str, non_striker: str):
        striker = (striker or "").strip()
        non_striker = (non_striker or "").strip()
        if not striker or not non_striker or striker == non_striker:
            raise ValueError("Select two different batters")
        if striker not in self.batting_order or non_striker not in self.batting_order:
            raise ValueError("Invalid batter selection")
        if self.stats[striker].out or self.stats[non_striker].out:
            raise ValueError("Out batter cannot be selected")

        self.striker_i = self.batting_order.index(striker)
        self.non_striker_i = self.batting_order.index(non_striker)
        self._recalc_missing_batter_end()

    def force_new_batter(self, end: str, batter_name: str):
        batter_name = (batter_name or "").strip()
        if not batter_name:
            raise ValueError("Select next batter")
        if batter_name not in self.batting_order:
            raise ValueError("Invalid batter")
        if self.stats[batter_name].out:
            raise ValueError("Out batter cannot be selected")
        if batter_name in (self.striker_name(), self.non_striker_name()):
            raise ValueError("Already selected")

        idx = self.batting_order.index(batter_name)
        if end == "non":
            self.non_striker_i = idx
        else:
            self.striker_i = idx

        self._recalc_missing_batter_end()

    # ---------- extras ----------
    def extras_breakdown(self) -> Dict[str, int]:
        b = lb = w = nb = 0
        for d in self.balls:
            if d.kind == "B":
                b += d.runs_total
            elif d.kind == "LB":
                lb += d.runs_total
            elif d.kind == "WD":
                w += d.runs_total
            elif d.kind in ("NB", "NBW"):
                nb += max(0, d.runs_total - d.runs_bat)
        return {"b": b, "lb": lb, "w": w, "nb": nb}

    def did_not_bat(self) -> List[str]:
        cur = {self.striker_name(), self.non_striker_name()}
        out = []
        for name in self.batting_order:
            st = self.stats.get(name)
            if not st:
                out.append(name)
                continue
            if st.balls == 0 and (not st.out) and name not in cur:
                out.append(name)
        return out

    def batters_for_table(self) -> List[str]:
        cur = {self.striker_name(), self.non_striker_name()}
        res = []
        for name in self.batting_order:
            st = self.stats.get(name)
            if not st:
                continue
            if st.out or st.balls > 0 or name in cur:
                res.append(name)
        return res

        # ---------- bowler ----------
    def set_bowler(self, name: str):
        name = (name or "").strip()
        if not name:
            self.current_bowler = None
            return

        # consecutive over rule
        if self.require_new_bowler and self.last_over_bowler and name == self.last_over_bowler:
            raise ValueError("No consecutive overs for same bowler")

        self.current_bowler = name

        if name not in self.bowlers:
            self.bowlers[name] = BowlerStat(name=name)

        # ✅ RESET BALL COUNT FOR NEW BOWLER
        self.current_bowler_balls = 0

        if self.require_new_bowler:
            self.require_new_bowler = False

    # ---------- enforcement ----------
    def _recalc_missing_batter_end(self):
        """
        If striker/non-striker is OUT at the crease, that end is missing a batter.
        If no next batter exists => all out => innings complete.
        """
        missing = None
        if self.stats[self.striker_name()].out:
            missing = "str"
        elif self.stats[self.non_striker_name()].out:
            missing = "non"

        if missing:
            if not self.available_next_batters():
                # all out
                self.wickets = max(self.wickets, self.max_wickets())
                self.require_new_batter_end = None
            else:
                self.require_new_batter_end = missing
        else:
            self.require_new_batter_end = None

    def _enforce_before_ball(self):
        if self.is_complete():
            raise ValueError("Innings completed")
        if self.require_new_batter_end:
            raise ValueError("Please select batter to continue")
        if self.require_new_bowler:
            raise ValueError("Over completed. Please select NEW bowler to continue")
        if not self.current_bowler:
            raise ValueError("Please select bowler to continue")

    def _apply_bowler(self, d: Delivery):
        b = self.bowlers.setdefault(self.current_bowler, BowlerStat(name=self.current_bowler))  # type: ignore

        b.runs += int(d.runs_total)

        if d.kind == "WD":
            b.wides += 1
        if d.kind in ("NB", "NBW"):
            b.noballs += 1

        if d.legal:
            b.balls += 1

        # run out is NOT a bowler wicket
        how = (d.how_out or "").lower().strip()
        is_runout = how.startswith("run out")
        if d.kind == "W" and (not is_runout):
            b.wickets += 1

    # ---------- undo/redo ----------
    def _snapshot(self) -> Dict:
        return {
            "runs": self.runs,
            "wickets": self.wickets,
            "legal_balls": self.legal_balls,
            "striker_i": self.striker_i,
            "non_striker_i": self.non_striker_i,
            "stats": {k: asdict(v) for k, v in self.stats.items()},
            "balls": [asdict(b) for b in self.balls],
            "current_bowler": self.current_bowler,
            "bowlers": {k: asdict(v) for k, v in self.bowlers.items()},
            "last_over_bowler": self.last_over_bowler,
            "require_new_bowler": self.require_new_bowler,
            "require_new_batter_end": self.require_new_batter_end,
            "fow": list(self.fow),
            "current_bowler_balls": self.current_bowler_balls,
        }

    def _restore(self, snap: Dict):
        self.runs = snap["runs"]
        self.wickets = snap["wickets"]
        self.legal_balls = snap["legal_balls"]
        self.striker_i = snap["striker_i"]
        self.non_striker_i = snap["non_striker_i"]
        self.stats = {k: BatterStat(**v) for k, v in snap["stats"].items()}
        self.balls = [Delivery(**b) for b in snap["balls"]]
        self.current_bowler = snap.get("current_bowler")
        self.bowlers = {k: BowlerStat(**v) for k, v in snap.get("bowlers", {}).items()}
        self.last_over_bowler = snap.get("last_over_bowler")
        self.require_new_bowler = bool(snap.get("require_new_bowler", False))
        self.require_new_batter_end = snap.get("require_new_batter_end")
        self.fow = list(snap.get("fow", []))
        self.current_bowler_balls = snap.get("current_bowler_balls", 0)

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())

    def reset(self):
        self.__init__(self.batting_team, self.bowling_team, self.batting_order, self.max_overs)

    # ---------- batting updates ----------
    def _ensure_stat(self, name: str):
        if name not in self.stats:
            self.stats[name] = BatterStat(name=name)

    def _apply_batsman_ball(self, batsman: str, runs_off_bat: int, total_runs: int, count_ball: bool, dot_if_total0: bool):
        self._ensure_stat(batsman)
        st = self.stats[batsman]
        if count_ball:
            st.balls += 1
        st.runs += runs_off_bat
        if runs_off_bat == 4:
            st.fours += 1
        if runs_off_bat == 6:
            st.sixes += 1
        if count_ball and dot_if_total0 and total_runs == 0:
            st.dots += 1

    def add_delivery(self, d: Delivery):
        self._enforce_before_ball()

        self._undo_stack.append(self._snapshot())
        self._redo_stack = []

        self.balls.append(d)
        self.runs += int(d.runs_total)

        if d.legal:
            self.legal_balls += 1

        striker = self.striker_name()

        if d.kind == "RUN":
            self._apply_batsman_ball(striker, d.runs_bat, d.runs_total, True, True)
            if d.runs_total % 2 == 1:
                self.swap_strike()

        elif d.kind in ("B", "LB"):
            self._apply_batsman_ball(striker, 0, d.runs_total, True, True)
            if d.runs_total % 2 == 1:
                self.swap_strike()

        elif d.kind == "WD":
            run_by_running = max(0, int(d.runs_total) - 1)
            if run_by_running % 2 == 1:
                self.swap_strike()

        elif d.kind == "NB":
            self._apply_batsman_ball(striker, d.runs_bat, d.runs_total, True, False)
            if d.runs_bat % 2 == 1:
                self.swap_strike()

        elif d.kind in ("W", "NBW"):
            # ball faced
            self._apply_batsman_ball(striker, 0, d.runs_total, True, False)

            out_idx = d.batsman_out_idx if d.batsman_out_idx is not None else self.striker_i
            out_name = self.batting_order[out_idx]
            self._ensure_stat(out_name)

            st = self.stats[out_name]
            st.out = True
            st.how_out = (d.how_out or "out").strip()

            self.wickets += 1
            self.fow.append({
                "runs": self.runs,
                "wickets": self.wickets,
                "batter": out_name,
                "over": overs_str(self.legal_balls),
            })

            # crossing on odd run-outs
            if int(d.runs_total) % 2 == 1:
                self.swap_strike()

            self._recalc_missing_batter_end()

        self._apply_bowler(d)

                # ✅ FIXED OVER LOGIC (per bowler)
        if d.legal:
            self.current_bowler_balls += 1

        # over ends ONLY when same bowler completes 6 balls
        if d.legal and self.current_bowler_balls >= 6:
            self.swap_strike()

            if not self.is_complete():
                self.last_over_bowler = self.current_bowler
                self.current_bowler = None
                self.require_new_bowler = True

            # reset for next over
            self.current_bowler_balls = 0


class MatchScorer:
    def __init__(self, team1: str, team2: str):
        self.team1 = team1
        self.team2 = team2

        self.team_players: Dict[str, List[str]] = {team1: [], team2: []}
        self.team_player_roles: Dict[str, Dict[str, str]] = {team1: {}, team2: {}}

        self.batting_first: Optional[str] = None
        self.max_overs: int = 20

        self.innings_no: int = 0
        self.innings1: Optional[Innings] = None
        self.innings2: Optional[Innings] = None

    def set_players(self, team: str, players: List[str]):
        self.team_players[team] = [p.strip() for p in players if p and p.strip()]

    def set_player_roles(self, team: str, roles: Dict[str, str]):
        self.team_player_roles[team] = {str(k).strip(): str(v).strip() for k, v in (roles or {}).items() if str(k).strip()}

    def player_role(self, team: str, player_name: str) -> str:
        return (self.team_player_roles.get(team, {}).get(player_name, "") or "").strip()

    def set_batting_first(self, team: str):
        if team not in (self.team1, self.team2):
            raise ValueError("Invalid team")
        self.batting_first = team

    def _batting_order_for(self, team: str) -> List[str]:
        plist = self.team_players.get(team, [])
        if plist:
            return plist
        return [f"{team} Player {i}" for i in range(1, 12)]

    def start_first_innings(self, max_overs: int):
        if not self.batting_first:
            raise ValueError("Batting first not set")
        self.max_overs = max_overs

        batting = self.batting_first
        bowling = self.team2 if batting == self.team1 else self.team1
        self.innings1 = Innings(batting, bowling, self._batting_order_for(batting), max_overs)
        self.innings_no = 1

    def start_second_innings(self):
        if not self.innings1:
            raise ValueError("First innings not started")
        if self.innings_no >= 2:
            return
        batting = self.innings1.bowling_team
        bowling = self.innings1.batting_team
        self.innings2 = Innings(batting, bowling, self._batting_order_for(batting), self.max_overs)
        self.innings_no = 2

    def current_innings(self) -> Optional[Innings]:
        if self.innings_no == 1:
            return self.innings1
        if self.innings_no == 2:
            return self.innings2
        return None

    def target(self) -> Optional[int]:
        if self.innings1 and self.innings_no == 2:
            return self.innings1.runs + 1
        return None

    def chase_completed(self) -> bool:
        inn = self.current_innings()
        tgt = self.target()
        return bool(inn and tgt and self.innings_no == 2 and inn.runs >= tgt)

    def is_match_complete(self) -> bool:
        return bool(self.innings2 and (self.innings2.is_complete() or self.chase_completed()))

    def result(self) -> Tuple[Optional[str], str]:
        if not (self.innings1 and self.innings2):
            return (None, "Match not completed")
        r1 = self.innings1.runs
        r2 = self.innings2.runs
        if r2 > r1:
            wk_left = max(0, self.innings2.max_wickets() - self.innings2.wickets)
            return (self.innings2.batting_team, f"{self.innings2.batting_team} won by {wk_left} wickets")
        if r1 > r2:
            return (self.innings1.batting_team, f"{self.innings1.batting_team} won by {r1 - r2} runs")
        return (None, "Match tied")

    # admin selection
    def set_bowler(self, name: str):
        inn = self.current_innings()
        if inn:
            inn.set_bowler(name)

    def set_batters(self, striker: str, non_striker: str):
        inn = self.current_innings()
        if inn:
            inn.set_batters(striker, non_striker)

    def force_new_batter(self, end: str, batter_name: str):
        inn = self.current_innings()
        if inn:
            inn.force_new_batter(end, batter_name)

    # deliveries
    def add_runs(self, runs: int):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")
        inn.add_delivery(Delivery("RUN", runs_total=int(runs), runs_bat=int(runs), legal=True))

    def add_wide(self, run_by_running: int = 0):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")
        total = 1 + max(0, int(run_by_running))
        inn.add_delivery(Delivery("WD", runs_total=total, legal=False))

    def add_no_ball(self, bat_runs: int = 0):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")
        total = 1 + max(0, int(bat_runs))
        inn.add_delivery(Delivery("NB", runs_total=total, runs_bat=max(0, int(bat_runs)), legal=False))

    def add_bye(self, runs: int):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")
        inn.add_delivery(Delivery("B", runs_total=int(runs), legal=True))

    def add_leg_bye(self, runs: int):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")
        inn.add_delivery(Delivery("LB", runs_total=int(runs), legal=True))

    def add_wicket(self, out_role: str, how_out: str, runs: int = 0):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")
        out_idx = inn.non_striker_i if out_role == "non" else inn.striker_i
        inn.add_delivery(Delivery("W", runs_total=max(0, int(runs)), legal=True, batsman_out_idx=out_idx, how_out=how_out))

    def add_no_ball_runout(self, out_role: str, runs_by_running: int = 0, fielder: str = ""):
        inn = self.current_innings()
        if not inn:
            raise ValueError("Innings not started")

        out_idx = inn.non_striker_i if out_role == "non" else inn.striker_i
        rr = max(0, int(runs_by_running))
        fld = (fielder or "").strip()
        extra_txt = f", {rr} run" if rr == 1 else f", {rr} runs"
        how = f"Run Out ({fld}{extra_txt})" if fld else (f"Run Out ({rr} run)" if rr == 1 else f"Run Out ({rr} runs)")

        inn.add_delivery(Delivery("NBW", runs_total=1 + rr, legal=False, batsman_out_idx=out_idx, how_out=how))

    def undo(self):
        inn = self.current_innings()
        if inn:
            inn.undo()

    def redo(self):
        inn = self.current_innings()
        if inn:
            inn.redo()

    def reset_current_innings(self):
        inn = self.current_innings()
        if inn:
            inn.reset()

    # serialization
    def to_dict(self) -> Dict:
        return {
            "team1": self.team1,
            "team2": self.team2,
            "team_players": self.team_players,
            "team_player_roles": self.team_player_roles,
            "batting_first": self.batting_first,
            "max_overs": self.max_overs,
            "innings_no": self.innings_no,
            "innings1": self.innings1.to_dict() if self.innings1 else None,
            "innings2": self.innings2.to_dict() if self.innings2 else None,
        }

    @staticmethod
    def from_dict(d: Dict) -> "MatchScorer":
        ms = MatchScorer(d.get("team1", ""), d.get("team2", ""))
        ms.team_players = d.get("team_players", {ms.team1: [], ms.team2: []})
        ms.team_player_roles = d.get("team_player_roles", {ms.team1: {}, ms.team2: {}})
        ms.batting_first = d.get("batting_first")
        ms.max_overs = int(d.get("max_overs", 20))
        ms.innings_no = int(d.get("innings_no", 0))
        ms.innings1 = Innings.from_dict(d["innings1"]) if d.get("innings1") else None
        ms.innings2 = Innings.from_dict(d["innings2"]) if d.get("innings2") else None
        return ms


# ---- Innings serialization helpers ----
def _innings_to_dict(inn: Innings) -> Dict:
    return {
        "batting_team": inn.batting_team,
        "bowling_team": inn.bowling_team,
        "max_overs": inn.max_overs,
        "batting_order": inn.batting_order,
        "runs": inn.runs,
        "wickets": inn.wickets,
        "legal_balls": inn.legal_balls,
        "striker_i": inn.striker_i,
        "non_striker_i": inn.non_striker_i,
        "stats": {k: asdict(v) for k, v in inn.stats.items()},
        "balls": [asdict(b) for b in inn.balls],
        "current_bowler": inn.current_bowler,
        "bowlers": {k: asdict(v) for k, v in inn.bowlers.items()},
        "last_over_bowler": inn.last_over_bowler,
        "require_new_bowler": inn.require_new_bowler,
        "require_new_batter_end": inn.require_new_batter_end,
        "fow": list(inn.fow),
        "current_bowler_balls": inn.current_bowler_balls,
    }


def _innings_from_dict(d: Dict) -> Innings:
    inn = Innings(
        batting_team=d["batting_team"],
        bowling_team=d["bowling_team"],
        batting_order=d.get("batting_order", []),
        max_overs=int(d.get("max_overs", 20)),
    )
    inn.runs = int(d.get("runs", 0))
    inn.wickets = int(d.get("wickets", 0))
    inn.legal_balls = int(d.get("legal_balls", 0))
    inn.striker_i = int(d.get("striker_i", 0))
    inn.non_striker_i = int(d.get("non_striker_i", 1))
    inn.stats = {k: BatterStat(**v) for k, v in d.get("stats", {}).items()}
    inn.balls = [Delivery(**b) for b in d.get("balls", [])]
    inn.current_bowler = d.get("current_bowler")
    inn.bowlers = {k: BowlerStat(**v) for k, v in d.get("bowlers", {}).items()}
    inn.last_over_bowler = d.get("last_over_bowler")
    inn.require_new_bowler = bool(d.get("require_new_bowler", False))
    inn.require_new_batter_end = d.get("require_new_batter_end")
    inn.fow = list(d.get("fow", []))
    inn.current_bowler_balls = int(d.get("current_bowler_balls", 0))
    inn._undo_stack = []
    inn._redo_stack = []
    
    return inn


Innings.to_dict = _innings_to_dict  # type: ignore
Innings.from_dict = staticmethod(_innings_from_dict)  # type: ignore