"""DraftKings NFL Showdown (Captain Mode) optimizer.

Roster (6): 1 CPT + 5 FLEX, $50,000 cap. CPT scores 1.5x and costs 1.5x.
Lineup must include at least one player from each team.
"""
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

from .loader import match_names

SALARY_CAP = 50000
ROSTER = 6
FLEX_SLOTS = 5


@dataclass
class ShowdownSettings:
    n_lineups: int = 10
    include: List[str] = field(default_factory=list)       # must be in every lineup (CPT or FLEX)
    exclude: List[str] = field(default_factory=list)       # never used
    captain_pool: List[str] = field(default_factory=list)  # if set, captain must be one of these
    captain_exclude: List[str] = field(default_factory=list)  # never captain (may still be FLEX)
    min_diff: int = 1
    min_salary: int = 0


class ShowdownError(Exception):
    pass


class ShowdownOptimizer:
    """Variables: [cpt_0..cpt_{n-1} | flex_0..flex_{n-1}]."""

    def __init__(self, players: pd.DataFrame, settings: ShowdownSettings):
        self.df = players.reset_index(drop=True).copy()
        self.s = settings
        self.n = len(self.df)
        self.nv = 2 * self.n
        self.rows, self.lo, self.hi = [], [], []
        self.lb = np.zeros(self.nv)
        self.ub = np.ones(self.nv)
        self.warnings = []
        self._build()

    def _row(self):
        return np.zeros(self.nv)

    def _add(self, row, lo, hi):
        self.rows.append(row); self.lo.append(lo); self.hi.append(hi)

    def _resolve(self, names, label):
        idx, unmatched, ambiguous = match_names(self.df, names)
        for raw in unmatched:
            self.warnings.append(f"{label}: no match for '{raw}'")
        for raw, opts in ambiguous:
            self.warnings.append(f"{label}: '{raw}' is ambiguous ({', '.join(opts)})")
        return idx

    def _build(self):
        df, s, n = self.df, self.s, self.n

        # exactly 1 captain, exactly 5 flex
        r = self._row(); r[:n] = 1; self._add(r, 1, 1)
        r = self._row(); r[n:] = 1; self._add(r, FLEX_SLOTS, FLEX_SLOTS)

        # a player can fill at most one slot
        for i in range(n):
            r = self._row(); r[i] = 1; r[n + i] = 1; self._add(r, 0, 1)

        # salary
        r = self._row(); r[:n] = df.cpt_sal.values; r[n:] = df.sal.values
        self._add(r, s.min_salary, SALARY_CAP)

        # both teams represented
        for t in sorted(df.team.unique()):
            on_team = (df.team.values == t).astype(float)
            r = self._row(); r[:n] = on_team; r[n:] = on_team
            self._add(r, 1, np.inf)

        # include: player appears in some slot
        for i in self._resolve(s.include, "Include"):
            r = self._row(); r[i] = 1; r[n + i] = 1; self._add(r, 1, 1)

        # exclude: never used
        for i in self._resolve(s.exclude, "Exclude"):
            self.ub[i] = 0; self.ub[n + i] = 0

        # never captain (still eligible at FLEX)
        for i in self._resolve(s.captain_exclude, "Never captain"):
            self.ub[i] = 0

        # captain pool: the captain must be one of these
        pool = self._resolve(s.captain_pool, "Captain")
        if pool:
            r = self._row()
            for i in pool:
                r[i] = 1
            self._add(r, 1, 1)

        # objective (minimize negative points)
        self.c = np.zeros(self.nv)
        self.c[:n] = -df.cpt_proj.values
        self.c[n:] = -df.proj.values

    def solve(self):
        lineups = []
        for _ in range(self.s.n_lineups):
            res = milp(self.c, constraints=LinearConstraint(np.array(self.rows), self.lo, self.hi),
                       integrality=np.ones(self.nv), bounds=Bounds(self.lb, self.ub))
            if res.status != 0 or res.x is None:
                if not lineups:
                    raise ShowdownError("No feasible lineup with these constraints.")
                break
            x = np.round(res.x).astype(int)
            lineups.append(self._format(x))
            # a different captain over the same six players counts as a different lineup
            r = self._row(); r[x == 1] = 1
            self._add(r, -np.inf, ROSTER - self.s.min_diff)
        return lineups

    def _format(self, x):
        n = self.n
        cpt_i = int(np.flatnonzero(x[:n])[0])
        flex = self.df[x[n:] == 1].sort_values("proj", ascending=False)
        c = self.df.loc[cpt_i]

        players = [{"slot": "CPT", "name": c["name"], "pos": c.pos, "team": c.team,
                    "sal": int(c.cpt_sal), "proj": round(float(c.cpt_proj), 1), "id": c.id}]
        players += [{"slot": "FLEX", "name": r["name"], "pos": r.pos, "team": r.team,
                     "sal": int(r.sal), "proj": round(float(r.proj), 1), "id": r.id}
                    for _, r in flex.iterrows()]

        counts = pd.Series([p["team"] for p in players]).value_counts()
        return {
            "players": players,
            "proj": round(float(c.cpt_proj + flex.proj.sum()), 1),
            "salary": int(c.cpt_sal + flex.sal.sum()),
            "captain": c["name"],
            "split": " / ".join(f"{t} {k}" for t, k in counts.items()),
        }


