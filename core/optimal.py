"""The best lineup that could have been built, from points already scored.

Not a projection — this is the hindsight-optimal lineup given live scoring and DK
salaries, the way DKLive shows it.
"""
import pandas as pd

from .optimizer import Optimizer, OptimizerSettings, OptimizeError
from .salaries import lookup
from .showdown import ShowdownOptimizer, ShowdownSettings, ShowdownError


def _frame(rows, table, showdown):
    """Live rows + salaries -> the frame shape the solvers expect."""
    out = []
    for r in rows:
        sal = lookup(table, r["name"], r["team"], r["pos"])
        if not sal:
            continue                       # no salary, cannot be rostered
        rec = {
            "name": r["name"], "pos": r["pos"], "team": r["team"], "opp": "",
            "sal": sal["sal"], "proj": float(r["pts"]),
            "own_small": 0.0, "own_large": 0.0, "ceil": float(r["pts"]), "id": "",
        }
        if showdown:
            rec["cpt_sal"] = sal.get("cpt_sal", int(round(sal["sal"] * 1.5)))
            rec["cpt_proj"] = float(r["pts"]) * 1.5
        out.append(rec)
    df = pd.DataFrame(out)
    if not df.empty:
        df["value"] = df.proj / (df.sal / 1000.0)
    return df


def optimal_lineup(rows, table, showdown=False, cap=50000):
    """Best possible lineup from what has actually been scored. None if not solvable."""
    if not table:
        return {"available": False,
                "reason": "No DraftKings salaries for this slate — upload DK's salary file "
                          "on the Settings tab."}

    df = _frame(rows, table, showdown)
    needed = 6 if showdown else 9
    if df.empty or len(df) < needed:
        return {"available": False, "reason": "Not enough priced players have scored yet."}

    try:
        if showdown:
            lineups = ShowdownOptimizer(df, ShowdownSettings(n_lineups=1)).solve()
            L = lineups[0]
            players = L["players"]
            total, salary = L["proj"], L["salary"]
        else:
            # every player here already has points; the solver maximizes those
            lineups = Optimizer(df, OptimizerSettings(n_lineups=1)).solve()
            L = lineups[0]
            players = [{"slot": p["slot"], "name": p["name"], "pos": p["pos"],
                        "team": p["team"], "sal": p["sal"], "pts": p["proj"]} for p in L["players"]]
            total, salary = L["proj"], L["salary"]
    except (OptimizeError, ShowdownError) as e:
        return {"available": False, "reason": str(e)}
    except Exception as e:
        return {"available": False, "reason": f"Could not solve: {e}"}

    if showdown:
        players = [{"slot": p["slot"], "name": p["name"], "pos": p["pos"],
                    "team": p["team"], "sal": p["sal"], "pts": p["proj"]} for p in players]

    return {
        "available": True,
        "players": players,
        "points": round(float(total), 2),
        "salary": int(salary),
        "cap": cap,
        "remaining": int(cap - salary),
        "priced_players": int(len(df)),
        "showdown": bool(showdown),
    }
