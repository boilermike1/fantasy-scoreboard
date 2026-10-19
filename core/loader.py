"""Name matching shared by the salary lookup and the solvers."""
import re


def _norm(name):
    """Normalize a player name for fuzzy matching: lowercase, strip punctuation/suffixes."""
    n = str(name).lower().strip()
    n = re.sub(r"[.'\-]", "", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def match_names(df, names):
    """Map a list of user-provided names to row indices in `df`.

    Returns (indices, unmatched, ambiguous). Exact normalized match wins; else unique substring.
    """
    normed = df.name.map(_norm)
    idx, unmatched, ambiguous = [], [], []
    for raw in names:
        q = _norm(raw)
        if not q:
            continue
        exact = df.index[normed == q].tolist()
        if exact:
            idx.append(exact[0]); continue
        sub = df.index[normed.str.contains(re.escape(q))].tolist()
        if len(sub) == 1:
            idx.append(sub[0])
        elif len(sub) == 0:
            unmatched.append(raw)
        else:
            ambiguous.append((raw, df.loc[sub, "name"].tolist()))
    return idx, unmatched, ambiguous
