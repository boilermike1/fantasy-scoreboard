"""DraftKings NFL Classic lineup optimizer (MILP via scipy).

Roster: QB, RB, RB, WR, WR, WR, TE, FLEX(RB/WR/TE), DST  |  $50,000 cap.
All stack / DST / ownership rules are OPT-IN via OptimizerSettings.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

from .loader import match_names

SALARY_CAP = 50000
ROSTER = 9
PASS_CATCHERS = ("WR", "TE")
BASE_SLOTS = {"RB": 2, "WR": 3, "TE": 1}
SLOT_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5}


@dataclass
class OptimizerSettings:
    n_lineups: int = 10
    ownership: str = "small"            # "small" | "large"
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    include_teams: List[str] = field(default_factory=list)
    exclude_teams: List[str] = field(default_factory=list)
    min_diff: int = 1                   # each lineup differs by >= this many players
    min_salary: int = 0
    max_own: Optional[float] = None     # cap on avg lineup ownership %
    # top plays
    top_play_bonus: float = 0.0         # objective pts added per top play (x weight)
    min_top_plays: int = 0              # hard floor on # of top plays per lineup
    # stacking (opt-in)
    require_stack: bool = False         # QB + >=1 same-team WR/TE
    double_stack_bonus: float = 0.0     # objective bonus for QB + 2 same-team WR/TE
    bringback_bonus: float = 0.0        # objective bonus for >=1 opposing WR/TE
    require_double_stack: bool = False
    require_bringback: bool = False
    # DST
    block_dst_vs_offense: bool = False  # DST never paired with opposing offensive players


class OptimizeError(Exception):
    pass


class Optimizer:
    def __init__(self, players: pd.DataFrame, settings: OptimizerSettings, top_plays: Optional[pd.DataFrame] = None):
        self.df = players.reset_index(drop=True).copy()
        self.s = settings
        self.n = len(self.df)
        own_col = "own_small" if settings.ownership == "small" else "own_large"
        self.df["own"] = self.df[own_col].astype(float)

        # top-play weights per player (0 if not a top play)
        self.df["tp_weight"] = 0.0
        self.top_play_report = {"matched": [], "unmatched": [], "ambiguous": []}
        if top_plays is not None and len(top_plays):
            matched, unmatched, ambiguous = [], [], []
            for _, tp in top_plays.iterrows():
                idx, un, amb = match_names(self.df, [tp["name"]])
                if idx:
                    self.df.at[idx[0], "tp_weight"] = float(tp["weight"])
                    matched.append(self.df.at[idx[0], "name"])
                unmatched += un
                ambiguous += [f"{r} -> {', '.join(o)}" for r, o in amb]
            self.top_play_report = {"matched": matched, "unmatched": unmatched, "ambiguous": ambiguous}

        self.teams = sorted(self.df.team.unique())
        self.T = len(self.teams)
        self.tix = {t: i for i, t in enumerate(self.teams)}
        # vars: [x_players | d_team (double stack) | b_team (bringback)]
        self.nv = self.n + 2 * self.T
        self.rows, self.lo, self.hi = [], [], []
        self.lb = np.zeros(self.nv)
        self.ub = np.ones(self.nv)
        self.warnings = []
        self._build()

    # ---- helpers ----
    def _row(self):
        return np.zeros(self.nv)

    def _mask(self, cond, weights=None):
        r = self._row()
        r[: self.n] = cond.astype(float) * (weights if weights is not None else 1.0)
        return r

    def _add(self, row, lo, hi):
        self.rows.append(row); self.lo.append(lo); self.hi.append(hi)

    # ---- model ----
    def _build(self):
        df, s = self.df, self.s
        pos = df.pos.values
        ones = np.ones(self.n, dtype=bool)

        self._add(self._mask(ones, df.sal.values), s.min_salary, SALARY_CAP)
        self._add(self._mask(ones), ROSTER, ROSTER)
        self._add(self._mask(pos == "QB"), 1, 1)
        self._add(self._mask(pos == "DST"), 1, 1)
        self._add(self._mask(pos == "RB"), 2, 3)
        self._add(self._mask(pos == "WR"), 3, 4)
        self._add(self._mask(pos == "TE"), 1, 2)

        if s.max_own is not None:
            self._add(self._mask(ones, df.own.values), 0, s.max_own * ROSTER)

        if s.min_top_plays > 0:
            self._add(self._mask(df.tp_weight.values > 0), s.min_top_plays, np.inf)

        for t in self.teams:
            ti = self.tix[t]
            is_qb_t = (df.team.values == t) & (pos == "QB")
            is_pc_t = (df.team.values == t) & np.isin(pos, PASS_CATCHERS)
            opp = df.loc[df.team == t, "opp"].iloc[0]
            is_pc_opp = (df.team.values == opp) & np.isin(pos, PASS_CATCHERS)

            if s.require_stack or s.require_double_stack:
                r = self._row(); r[: self.n] = is_pc_t.astype(float) - is_qb_t.astype(float)
                self._add(r, 0, np.inf)

            # d_t <= qb_t ; 2*d_t <= pc_t
            r = self._row(); r[: self.n] = -is_qb_t.astype(float); r[self.n + ti] = 1; self._add(r, -np.inf, 0)
            r = self._row(); r[: self.n] = -is_pc_t.astype(float); r[self.n + ti] = 2; self._add(r, -np.inf, 0)
            # b_t <= qb_t ; b_t <= pc_opp
            r = self._row(); r[: self.n] = -is_qb_t.astype(float); r[self.n + self.T + ti] = 1; self._add(r, -np.inf, 0)
            r = self._row(); r[: self.n] = -is_pc_opp.astype(float); r[self.n + self.T + ti] = 1; self._add(r, -np.inf, 0)

            if s.block_dst_vs_offense:
                is_dst_t = (df.team.values == t) & (pos == "DST")
                is_off_opp = (df.team.values == opp) & (pos != "DST")
                if is_dst_t.any():
                    r = self._row(); r[: self.n] = is_off_opp.astype(float) + 9 * is_dst_t.astype(float)
                    self._add(r, -np.inf, 9)

        if s.require_double_stack:
            r = self._row(); r[self.n: self.n + self.T] = 1; self._add(r, 1, 1)
        if s.require_bringback:
            r = self._row(); r[self.n + self.T:] = 1; self._add(r, 1, 1)

        # include / exclude
        inc, un, amb = match_names(df, s.include)
        for i in inc: self.lb[i] = 1
        exc, un2, amb2 = match_names(df, s.exclude)
        for i in exc: self.ub[i] = 0
        for raw in un + un2: self.warnings.append(f"No match for '{raw}'")
        for raw, opts in amb + amb2: self.warnings.append(f"'{raw}' ambiguous: {', '.join(opts)}")
        for t in [x.upper() for x in s.exclude_teams]:
            self.ub[: self.n][df.team.values == t] = 0
        for t in [x.upper() for x in s.include_teams]:
            self._add(self._mask(df.team.values == t), 1, np.inf)

        # objective (minimize negative)
        self.c = np.zeros(self.nv)
        self.c[: self.n] = -(df.proj.values + s.top_play_bonus * df.tp_weight.values)
        self.c[self.n: self.n + self.T] = -s.double_stack_bonus
        self.c[self.n + self.T:] = -s.bringback_bonus

    def solve(self):
        lineups = []
        for _ in range(self.s.n_lineups):
            res = milp(self.c, constraints=LinearConstraint(np.array(self.rows), self.lo, self.hi),
                       integrality=np.ones(self.nv), bounds=Bounds(self.lb, self.ub))
            if res.status != 0 or res.x is None:
                if not lineups:
                    raise OptimizeError("No feasible lineup with these constraints.")
                break
            x = np.round(res.x[: self.n]).astype(int)
            lineups.append(self._format(self.df[x == 1].copy()))
            self._add(self._mask(x.astype(bool)), -np.inf, ROSTER - self.s.min_diff)
        return lineups

    def _format(self, L):
        L = L.sort_values("proj", ascending=False).copy()
        cnt = {"RB": 0, "WR": 0, "TE": 0}
        slots = []
        for _, r in L.iterrows():
            if r.pos in cnt:
                if cnt[r.pos] < BASE_SLOTS[r.pos]:
                    cnt[r.pos] += 1; slots.append(r.pos)
                else:
                    slots.append("FLEX")
            else:
                slots.append(r.pos)
        L["slot"] = slots
        L["_o"] = L.slot.map(SLOT_ORDER)
        L = L.sort_values(["_o", "proj"], ascending=[True, False]).drop(columns="_o")

        qb = L[L.pos == "QB"].iloc[0]
        same = int(((L.team == qb.team) & L.pos.isin(PASS_CATCHERS)).sum())
        bring = int(((L.team == qb.opp) & L.pos.isin(PASS_CATCHERS)).sum())
        stack = f"{qb.team} QB+{same}" + (f" / bringback {qb.opp}+{bring}" if bring else "")
        adj = L.proj.sum() + self.s.top_play_bonus * L.tp_weight.sum() \
            + (self.s.double_stack_bonus if same >= 2 else 0) + (self.s.bringback_bonus if bring >= 1 else 0)

        players = [{
            "slot": r.slot, "name": r["name"], "pos": r.pos, "team": r.team, "opp": r.opp,
            "sal": int(r.sal), "proj": round(float(r.proj), 1), "own": round(float(r.own), 1),
            "value": round(float(r.value), 2), "ceil": round(float(r.ceil), 1),
            "top_play": bool(r.tp_weight > 0), "id": r.id,
        } for _, r in L.iterrows()]
        return {
            "players": players,
            "proj": round(float(L.proj.sum()), 1),
            "adj": round(float(adj), 1),
            "salary": int(L.sal.sum()),
            "avg_own": round(float(L.own.mean()), 1),
            "ceil": round(float(L.ceil.sum()), 1),
            "top_plays": int((L.tp_weight > 0).sum()),
            "stack": stack,
        }


def lineups_to_upload_rows(lineups):
    """DK bulk-upload rows (ids, falling back to names)."""
    header = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
    rows = []
    for L in lineups:
        by = {}
        for p in L["players"]:
            by.setdefault(p["slot"], []).append(p["id"] or p["name"])
        rows.append([by["QB"][0], *by["RB"], *by["WR"], by["TE"][0], by["FLEX"][0], by["DST"][0]])
    return header, rows


def settings_from_dict(d):
    allowed = {f for f in OptimizerSettings.__dataclass_fields__}
    clean = {k: v for k, v in d.items() if k in allowed and v is not None}
    return OptimizerSettings(**clean)


def settings_to_dict(s):
    return asdict(s)
