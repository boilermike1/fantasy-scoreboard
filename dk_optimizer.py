#!/usr/bin/env python3
"""CLI for the DraftKings NFL optimizer (thin wrapper over core/).

  python3 dk_optimizer.py projections.csv
  python3 dk_optimizer.py projections.csv -n 10 --include "Gibbs" --exclude "Achane"
  python3 dk_optimizer.py projections.csv --top-plays top_plays.csv --top-play-bonus 3 --min-top-plays 4
  python3 dk_optimizer.py projections.csv --stack --double-stack-bonus 1.5 --bringback-bonus 1 --block-dst
"""
import argparse
import csv
import os

from core.loader import load_projections, load_top_plays
from core.optimizer import Optimizer, OptimizerSettings, OptimizeError, lineups_to_upload_rows
from core.analysis import value_report


def print_lineup(i, L):
    print(f"\n=== Lineup {i}: {L['proj']} pts (adj {L['adj']}) | ${L['salary']:,} | avg own {L['avg_own']}% | "
          f"ceil {L['ceil']} | top plays {L['top_plays']} | {L['stack']} ===")
    print(f"{'SLOT':<5}{'PLAYER':<24}{'TM':<5}{'OPP':<5}{'SAL':>8}{'PROJ':>7}{'OWN':>7}{'VAL':>6}  TP")
    for p in L["players"]:
        print(f"{p['slot']:<5}{p['name']:<24}{p['team']:<5}{p['opp']:<5}${p['sal']:>7,}{p['proj']:>7.1f}"
              f"{p['own']:>6.1f}%{p['value']:>6.2f}  {'*' if p['top_play'] else ''}")


def print_table(title, rows):
    print(f"\n--- {title} ---")
    print(f"{'PLAYER':<24}{'POS':<4}{'TM':<5}{'OPP':<5}{'SAL':>8}{'PROJ':>6}{'CEIL':>6}{'VAL':>6}{'OWN':>7}{'LEV':>7}")
    for r in rows:
        print(f"{r['name']:<24}{r['pos']:<4}{r['team']:<5}{r['opp']:<5}${r['sal']:>7,}{r['proj']:>6.1f}{r['ceil']:>6.1f}"
              f"{r['value']:>6.2f}{r['own']:>6.1f}%{r['leverage']:>+7.0f}")


def main():
    p = argparse.ArgumentParser(description="DraftKings NFL optimizer for ETR CSVs")
    p.add_argument("csv")
    p.add_argument("-n", "--lineups", type=int, default=10)
    p.add_argument("--ownership", choices=["small", "large"], default="small")
    p.add_argument("--include", nargs="*", default=[])
    p.add_argument("--exclude", nargs="*", default=[])
    p.add_argument("--include-team", nargs="*", default=[])
    p.add_argument("--exclude-team", nargs="*", default=[])
    p.add_argument("--max-own", type=float)
    p.add_argument("--min-salary", type=int, default=0)
    p.add_argument("--min-diff", type=int, default=1)
    p.add_argument("--top-plays", help="CSV or text file of top plays")
    p.add_argument("--top-play-bonus", type=float, default=3.0, help="pts added per top play (x tier weight)")
    p.add_argument("--min-top-plays", type=int, default=0)
    p.add_argument("--stack", action="store_true", help="require QB + >=1 same-team WR/TE")
    p.add_argument("--double-stack-bonus", type=float, default=0.0)
    p.add_argument("--bringback-bonus", type=float, default=0.0)
    p.add_argument("--require-double-stack", action="store_true")
    p.add_argument("--require-bringback", action="store_true")
    p.add_argument("--block-dst", action="store_true", help="DST never vs. its opponent's offense")
    p.add_argument("--out", help="output dir (omit to print only)")
    a = p.parse_args()

    df = load_projections(a.csv)
    tp = load_top_plays(a.top_plays) if a.top_plays else None
    s = OptimizerSettings(
        n_lineups=a.lineups, ownership=a.ownership, include=a.include, exclude=a.exclude,
        include_teams=a.include_team, exclude_teams=a.exclude_team, max_own=a.max_own,
        min_salary=a.min_salary, min_diff=a.min_diff,
        top_play_bonus=a.top_play_bonus if tp is not None else 0.0, min_top_plays=a.min_top_plays,
        require_stack=a.stack, double_stack_bonus=a.double_stack_bonus, bringback_bonus=a.bringback_bonus,
        require_double_stack=a.require_double_stack, require_bringback=a.require_bringback,
        block_dst_vs_offense=a.block_dst,
    )
    opt = Optimizer(df, s, tp)
    for w in opt.warnings: print("WARNING:", w)
    if tp is not None:
        r = opt.top_play_report
        print(f"Top plays matched {len(r['matched'])}; unmatched: {r['unmatched'] or 'none'}; ambiguous: {r['ambiguous'] or 'none'}")
    try:
        lineups = opt.solve()
    except OptimizeError as e:
        raise SystemExit(str(e))

    print(f"\nLoaded {len(df)} players | ownership={a.ownership} | stack={'on' if a.stack else 'off'} | DST block={'on' if a.block_dst else 'off'}")
    for i, L in enumerate(lineups, 1): print_lineup(i, L)
    under, over = value_report(df, a.ownership)
    print_table("UNDEROWNED VALUE (within-position rank)", under)
    print_table("OVEROWNED relative to value", over)

    if a.out:
        os.makedirs(a.out, exist_ok=True)
        header, rows = lineups_to_upload_rows(lineups)
        with open(os.path.join(a.out, "dk_upload.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(header); w.writerows(rows)
        with open(os.path.join(a.out, "lineups.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["lineup", "slot", "player", "pos", "team", "opp", "salary", "proj", "own", "value", "top_play", "lineup_proj", "lineup_salary", "stack"])
            for i, L in enumerate(lineups, 1):
                for pl in L["players"]:
                    w.writerow([i, pl["slot"], pl["name"], pl["pos"], pl["team"], pl["opp"], pl["sal"], pl["proj"], pl["own"], pl["value"], pl["top_play"], L["proj"], L["salary"], L["stack"]])
        print(f"\nWrote {a.out}/lineups.csv and {a.out}/dk_upload.csv")


if __name__ == "__main__":
    main()
