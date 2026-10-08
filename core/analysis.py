"""Value / ownership analysis."""
import pandas as pd


def value_report(df, ownership="small", min_own=2.0, top=20):
    """Underowned-value and overowned lists. Ranks are within position so QBs don't dominate."""
    own_col = "own_small" if ownership == "small" else "own_large"
    pool = df[(df[own_col] >= min_own) & (df.pos != "DST")].copy()
    pool["own"] = pool[own_col]
    pool["value_pct"] = pool.groupby("pos").value.rank(pct=True) * 100
    pool["own_pct"] = pool.groupby("pos").own.rank(pct=True) * 100
    pool["leverage"] = (pool.value_pct - pool.own_pct).round(0)
    cols = ["name", "pos", "team", "opp", "sal", "proj", "ceil", "value", "own", "leverage"]
    under = pool.sort_values("leverage", ascending=False).head(top)[cols]
    over = pool.sort_values("leverage").head(max(5, top // 2))[cols]
    rnd = lambda t: t.assign(value=t.value.round(2), proj=t.proj.round(1), ceil=t.ceil.round(1), own=t.own.round(1))
    return rnd(under).to_dict("records"), rnd(over).to_dict("records")
