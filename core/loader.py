"""CSV loaders: ETR projections and top-plays lists."""
import io
import re

import pandas as pd

VALID_POS = ["QB", "RB", "WR", "TE", "DST"]


def _clean_money(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")


def _clean_pct(s):
    return pd.to_numeric(s.astype(str).str.rstrip("%"), errors="coerce")


def load_projections(source):
    """Load an ETR DK projections CSV (path, file-like, or bytes) into a normalized DataFrame.

    Columns out: name, pos, team, opp, sal, proj, own_small, own_large, floor, ceil, value, id
    """
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    df = pd.read_csv(source)
    df.columns = [c.strip() for c in df.columns]

    need = ["Player", "DK Pos", "Team", "Opp", "DK Salary", "DK Proj"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing expected columns: {missing}")

    out = pd.DataFrame({
        "name": df["Player"].astype(str).str.strip(),
        "pos": df["DK Pos"].astype(str).str.strip().str.upper(),
        "team": df["Team"].astype(str).str.strip().str.upper(),
        "opp": df["Opp"].astype(str).str.strip().str.upper().str.lstrip("@"),
        "sal": _clean_money(df["DK Salary"]).fillna(0).astype(int),
        "proj": pd.to_numeric(df["DK Proj"], errors="coerce").fillna(0.0),
        "own_small": _clean_pct(df["Small Field"]).fillna(0.0) if "Small Field" in df else 0.0,
        "own_large": _clean_pct(df["Large Field"]).fillna(0.0) if "Large Field" in df else 0.0,
        "floor": pd.to_numeric(df.get("DK Floor", 0), errors="coerce").fillna(0.0),
        "ceil": pd.to_numeric(df.get("DK Ceiling", 0), errors="coerce").fillna(0.0),
        "id": df["id"].astype(str).str.strip() if "id" in df.columns else "",
    })
    total = len(out)
    out = out[out.pos.isin(VALID_POS) & (out.proj > 0) & (out.sal > 0)].reset_index(drop=True)
    if len(out) < 9:
        raise ValueError(
            f"Only {len(out)} of {total} players have a projection above 0, but a lineup needs 9. "
            f"The file's DK Proj column looks empty — re-export it.")
    out["value"] = out.proj / (out.sal / 1000.0)
    return out


def _norm(name):
    """Normalize a player name for fuzzy matching: lowercase, strip punctuation/suffixes."""
    n = str(name).lower().strip()
    n = re.sub(r"[.'\-]", "", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def load_top_plays(source):
    """Load a top-plays list. Flexible: finds a name column, optional tier/weight column.

    Accepts CSV (path/bytes/file-like) or plain text with one name per line.
    Returns DataFrame: name, weight (1.0 default; tiers map 1->1.0, 2->0.75, 3->0.5, else 0.5).
    """
    if isinstance(source, (bytes, bytearray)):
        text = source.decode("utf-8", errors="ignore")
    elif hasattr(source, "read"):
        text = source.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="ignore")
    else:
        with open(source, encoding="utf-8", errors="ignore") as f:
            text = f.read()

    text = text.strip()
    if not text:
        return pd.DataFrame(columns=["name", "weight"])

    # Try CSV with a header first
    try:
        df = pd.read_csv(io.StringIO(text))
        cols = {c.lower().strip(): c for c in df.columns}
        name_col = next((cols[c] for c in cols if c in ("player", "name", "player name")), None)
        if name_col is None:
            name_col = next((cols[c] for c in cols if "player" in c or "name" in c), None)
        if name_col is not None and len(df.columns) >= 1:
            weight_col = next((cols[c] for c in cols if c in ("weight", "bonus")), None)
            tier_col = next((cols[c] for c in cols if c in ("tier", "rank", "tier/rank")), None)
            names = df[name_col].astype(str).str.strip()
            if weight_col:
                w = pd.to_numeric(df[weight_col], errors="coerce").fillna(1.0)
            elif tier_col:
                t = pd.to_numeric(df[tier_col].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
                w = t.map({1: 1.0, 2: 0.75, 3: 0.5}).fillna(0.5)
            else:
                w = 1.0
            out = pd.DataFrame({"name": names, "weight": w})
            return out[out.name.str.len() > 0].drop_duplicates("name").reset_index(drop=True)
    except Exception:
        pass

    # Fallback: one name per line, optional "Name, tier" (1/2/3) or "Name - note"
    tier_w = {1: 1.0, 2: 0.75, 3: 0.5}
    rows = []
    for line in text.splitlines():
        line = line.strip().strip(",-•*").strip()
        if not line:
            continue
        parts = re.split(r"[,\-–—(]", line, maxsplit=1)
        name = parts[0].strip()
        weight = 1.0
        if len(parts) > 1:
            m = re.search(r"\d+", parts[1])
            if m:
                weight = tier_w.get(int(m.group()), 0.5)
        if name:
            rows.append({"name": name, "weight": weight})
    return pd.DataFrame(rows).drop_duplicates("name").reset_index(drop=True)


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


SECTION_ALIASES = {
    "top plays": "top_plays", "topplays": "top_plays", "top": "top_plays", "plays": "top_plays",
    "include": "include", "includes": "include", "lock": "include", "locks": "include",
    "exclude": "exclude", "excludes": "exclude", "fade": "exclude", "fades": "exclude", "ban": "exclude",
}


def parse_paste_block(text):
    """Parse the optimizer paste block.

    Sections are lines like 'TOP PLAYS', 'INCLUDE', 'EXCLUDE' (case-insensitive, optional colon).
    Lines before any header, or a block with no headers at all, are treated as top plays.
    Returns {"top_plays": DataFrame(name, weight), "include": [names], "exclude": [names]}.
    """
    buckets = {"top_plays": [], "include": [], "exclude": []}
    current = "top_plays"
    for raw in (text or "").splitlines():
        line = raw.strip().strip("-•*").strip()
        if not line:
            continue
        key = re.sub(r"[:\s]+$", "", line).lower()
        if key in SECTION_ALIASES:
            current = SECTION_ALIASES[key]
            continue
        buckets[current].append(line)
    tp = load_top_plays("\n".join(buckets["top_plays"]).encode()) if buckets["top_plays"] else pd.DataFrame(columns=["name", "weight"])
    strip = lambda lines: [re.split(r"[,\-–—(]", l)[0].strip() for l in lines]
    return {"top_plays": tp, "include": strip(buckets["include"]), "exclude": strip(buckets["exclude"])}
