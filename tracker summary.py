#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════
#   TRACKER SUMMARY — DIP & RIP BOT v10.0
#   Script analisis mandiri untuk tracker.json
#
#   CARA PAKAI:
#   python3 tracker_summary.py
#   python3 tracker_summary.py --min 30   (filter min 30 alert)
#   python3 tracker_summary.py --tier T1  (filter tier tertentu)
#   python3 tracker_summary.py --export   (simpan ke summary.json)
# ══════════════════════════════════════════════════════════

import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict

TRACKER_FILE = Path("tracker.json")

# ── WARNA TERMINAL ─────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def c(text, color):
    return f"{color}{text}{RESET}"

def pct_color(val):
    if val is None:
        return c("N/A", YELLOW)
    if val >= 0:
        return c(f"+{val:.1f}%", GREEN)
    return c(f"{val:.1f}%", RED)

# ── LOAD DATA ──────────────────────────────────────────
def load_tracker() -> list:
    if not TRACKER_FILE.exists():
        print(c("tracker.json tidak ditemukan.", RED))
        print("Pastikan bot sudah jalan dan ada alert yang terekam.")
        sys.exit(1)
    with open(TRACKER_FILE) as f:
        data = json.load(f)
    return data.get("alerts", [])

# ── FILTER ─────────────────────────────────────────────
def filter_alerts(alerts: list, args) -> list:
    result = alerts.copy()
    if args.tier:
        result = [a for a in result if a.get("tier") == args.tier.upper()]
    if args.grade:
        result = [a for a in result if a.get("grade") == args.grade.upper()]
    if args.outcome:
        result = [a for a in result
                  if a.get("outcome", "").startswith(args.outcome.lower())]
    # Hanya yang sudah closed (tidak open)
    closed = [a for a in result if a.get("outcome") != "open"]
    return closed, result  # (closed, all_filtered)

# ── STATISTIK DASAR ────────────────────────────────────
def basic_stats(closed: list) -> dict:
    if not closed:
        return {}

    wins    = [a for a in closed if a.get("outcome", "").startswith("win")]
    tp1     = [a for a in closed if a.get("outcome") == "win_tp1"]
    tp2     = [a for a in closed if a.get("outcome") == "win_tp2"]
    losses  = [a for a in closed if a.get("outcome") == "loss_sl"]
    rugs    = [a for a in closed if a.get("outcome") == "rug_suspected"]
    expired = [a for a in closed if a.get("outcome") == "expired"]

    n        = len(closed)
    win_rate = len(wins) / n * 100 if n > 0 else 0

    results  = [a["result_pct"] for a in closed
                if a.get("result_pct") is not None]
    avg_result = sum(results) / len(results) if results else 0

    win_results = [a["result_pct"] for a in wins
                   if a.get("result_pct") is not None]
    loss_results = [a["result_pct"] for a in losses
                    if a.get("result_pct") is not None]
    max_gains = [a.get("max_gain_pct", 0) for a in closed]

    times = [a.get("minutes_to_outcome", 0) for a in closed
             if a.get("minutes_to_outcome") is not None]

    return {
        "total":       n,
        "wins":        len(wins),
        "tp1":         len(tp1),
        "tp2":         len(tp2),
        "losses":      len(losses),
        "rugs":        len(rugs),
        "expired":     len(expired),
        "win_rate":    round(win_rate, 1),
        "avg_result":  round(avg_result, 1),
        "avg_win":     round(sum(win_results) / len(win_results), 1)
                       if win_results else 0,
        "avg_loss":    round(sum(loss_results) / len(loss_results), 1)
                       if loss_results else 0,
        "avg_max_gain":round(sum(max_gains) / len(max_gains), 1)
                       if max_gains else 0,
        "avg_time_min":round(sum(times) / len(times), 0) if times else 0,
        "best_result": round(max(results), 1) if results else 0,
        "worst_result":round(min(results), 1) if results else 0,
    }

# ── BREAKDOWN PER FIELD ────────────────────────────────
def breakdown(closed: list, field: str, buckets: list) -> dict:
    """
    Breakdown win rate per nilai field.
    buckets = list of (label, filter_fn)
    """
    result = {}
    for label, fn in buckets:
        group = [a for a in closed if fn(a)]
        if not group:
            continue
        wins     = [a for a in group if a.get("outcome", "").startswith("win")]
        results  = [a["result_pct"] for a in group
                    if a.get("result_pct") is not None]
        avg_r    = round(sum(results) / len(results), 1) if results else 0
        win_rate = round(len(wins) / len(group) * 100, 1)
        result[label] = {
            "count":    len(group),
            "wins":     len(wins),
            "win_rate": win_rate,
            "avg_result": avg_r,
        }
    return result

# ── EXPECTANCY ─────────────────────────────────────────
def calc_expectancy(stats: dict) -> float:
    """
    Expectancy = (win_rate × avg_win) + ((1-win_rate) × avg_loss)
    Positif = edge, negatif = sistem kalah jangka panjang.
    """
    if not stats:
        return 0.0
    wr  = stats["win_rate"] / 100
    aw  = stats["avg_win"]
    al  = stats["avg_loss"]
    return round((wr * aw) + ((1 - wr) * al), 2)

# ── PRINT SECTION ──────────────────────────────────────
def print_section(title: str):
    print()
    print(c(f"{'─' * 60}", CYAN))
    print(c(f"  {title}", BOLD))
    print(c(f"{'─' * 60}", CYAN))

def print_breakdown(data: dict, title: str):
    if not data:
        print(f"  Tidak ada data cukup untuk {title}")
        return
    print(f"\n  {c(title, BOLD)}")
    print(f"  {'Bucket':<22} {'N':>5} {'Win%':>7} {'Avg result':>12}")
    print(f"  {'─'*22} {'─'*5} {'─'*7} {'─'*12}")
    for label, v in sorted(data.items(),
                            key=lambda x: x[1]["win_rate"], reverse=True):
        wr_str  = pct_color(v["win_rate"]).replace("%", "")
        avg_str = pct_color(v["avg_result"])
        print(f"  {label:<22} {v['count']:>5} "
              f"  {v['win_rate']:>5.1f}% "
              f"  {v['avg_result']:>+7.1f}%")

# ── MAIN REPORT ────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Analisis hasil tracker DipRip Bot v10.0")
    parser.add_argument("--tier",    help="Filter tier (T0/T1/T2/T3)")
    parser.add_argument("--grade",   help="Filter grade (A+/A/B)")
    parser.add_argument("--outcome", help="Filter outcome (win/loss/expired/rug)")
    parser.add_argument("--min",     type=int, default=10,
                        help="Minimum alert untuk tampilkan breakdown (default: 10)")
    parser.add_argument("--export",  action="store_true",
                        help="Simpan hasil ke summary.json")
    args = parser.parse_args()

    all_alerts = load_tracker()
    closed, filtered = filter_alerts(all_alerts, args)

    open_count   = sum(1 for a in all_alerts if a.get("outcome") == "open")
    closed_count = len([a for a in all_alerts if a.get("outcome") != "open"])

    # ── HEADER ──────────────────────────────────────────
    print()
    print(c("═" * 60, CYAN))
    print(c("  DIP & RIP TRACKER SUMMARY — v10.0", BOLD))
    print(c("═" * 60, CYAN))
    print(f"  File    : {TRACKER_FILE}")
    print(f"  Total   : {len(all_alerts)} alert "
          f"({open_count} open, {closed_count} closed)")
    if args.tier or args.grade:
        filters = []
        if args.tier:  filters.append(f"Tier={args.tier.upper()}")
        if args.grade: filters.append(f"Grade={args.grade.upper()}")
        print(f"  Filter  : {', '.join(filters)}")
        print(f"  Filtered: {len(closed)} closed alert")

    # Minimum data warning
    if len(closed) < args.min:
        print()
        print(c(f"  ⚠️  Data terlalu sedikit ({len(closed)} closed alert).", YELLOW))
        print(c(f"     Butuh minimal {args.min} untuk analisis reliable.", YELLOW))
        print(c("     Jalankan bot lebih lama, lalu coba lagi.", YELLOW))
        if len(closed) == 0:
            return

    stats = basic_stats(closed)
    expectancy = calc_expectancy(stats)

    # ── OVERALL ─────────────────────────────────────────
    print_section("OVERALL PERFORMANCE")
    print(f"  Total closed  : {c(str(stats['total']), BOLD)}")
    print(f"  Win (TP1+)    : {c(str(stats['wins']), GREEN)} "
          f"({c(str(stats['tp1']), GREEN)} TP1 | "
          f"{c(str(stats['tp2']), GREEN)} TP2)")
    print(f"  Loss (SL)     : {c(str(stats['losses']), RED)}")
    print(f"  Rug suspected : {c(str(stats['rugs']), RED)}")
    print(f"  Expired (4h)  : {c(str(stats['expired']), YELLOW)}")
    print()
    wr_str = f"{stats['win_rate']:.1f}%"
    wr_col = GREEN if stats['win_rate'] >= 50 else RED
    print(f"  Win rate      : {c(wr_str, wr_col)}")
    print(f"  Avg result    : {pct_color(stats['avg_result'])}")
    print(f"  Avg win       : {pct_color(stats['avg_win'])}")
    print(f"  Avg loss      : {pct_color(stats['avg_loss'])}")
    print(f"  Avg max gain  : {pct_color(stats['avg_max_gain'])}")
    print(f"  Avg time      : {stats['avg_time_min']:.0f} menit ke outcome")
    print()
    print(f"  Best result   : {pct_color(stats['best_result'])}")
    print(f"  Worst result  : {pct_color(stats['worst_result'])}")
    print()
    exp_color = GREEN if expectancy > 0 else RED
    print(f"  Expectancy    : {c(f'{expectancy:+.2f}%', exp_color)} per trade")
    if expectancy > 0:
        print(c("  ✅ Sistem punya edge positif", GREEN))
    else:
        print(c("  🔴 Sistem belum menguntungkan — perlu perbaikan", RED))

    # ── PER TIER ────────────────────────────────────────
    print_section("WIN RATE PER TIER")
    tier_breakdown = breakdown(closed, "tier", [
        ("T0 Pre-pump",   lambda a: a.get("tier") == "T0"),
        ("T1 Early",      lambda a: a.get("tier") == "T1"),
        ("T2 Normal",     lambda a: a.get("tier") == "T2"),
        ("T3 Late",       lambda a: a.get("tier") == "T3"),
    ])
    print_breakdown(tier_breakdown, "Tier")

    # ── PER GRADE ───────────────────────────────────────
    print_section("WIN RATE PER GRADE")
    grade_breakdown = breakdown(closed, "grade", [
        ("Grade A+",  lambda a: a.get("grade") == "A+"),
        ("Grade A",   lambda a: a.get("grade") == "A"),
        ("Grade B",   lambda a: a.get("grade") == "B"),
    ])
    print_breakdown(grade_breakdown, "Grade")

    # ── PER SAFETY PCT ──────────────────────────────────
    print_section("WIN RATE PER SAFETY SCORE")
    safety_breakdown = breakdown(closed, "safety_pct", [
        ("Safety ≥ 70  (tinggi)", lambda a: (a.get("safety_pct") or 0) >= 70),
        ("Safety 50-69 (sedang)", lambda a: 50 <= (a.get("safety_pct") or 0) < 70),
        ("Safety 30-49 (rendah)", lambda a: 30 <= (a.get("safety_pct") or 0) < 50),
        ("Safety < 30  (lemah)",  lambda a: (a.get("safety_pct") or 0) < 30),
    ])
    print_breakdown(safety_breakdown, "Safety Score")

    # ── PER LP BURNED ───────────────────────────────────
    print_section("WIN RATE PER LP BURNED")
    lp_breakdown = breakdown(closed, "lp_burned", [
        ("LP 100%",       lambda a: (a.get("lp_burned") or 0) >= 100),
        ("LP 80-99%",     lambda a: 80 <= (a.get("lp_burned") or 0) < 100),
        ("LP 50-79%",     lambda a: 50 <= (a.get("lp_burned") or 0) < 80),
        ("LP < 50%",      lambda a: 0 < (a.get("lp_burned") or 0) < 50),
        ("LP N/A",        lambda a: a.get("lp_burned") is None),
    ])
    print_breakdown(lp_breakdown, "LP Burned")

    # ── PER DIP PCT ─────────────────────────────────────
    print_section("WIN RATE PER DIP DARI HIGH")
    dip_breakdown = breakdown(closed, "dip_pct", [
        ("Dip ≥ 50%  (dalam)",   lambda a: (a.get("dip_pct") or 0) >= 50),
        ("Dip 35-49% (ideal)",   lambda a: 35 <= (a.get("dip_pct") or 0) < 50),
        ("Dip 20-34% (cukup)",   lambda a: 20 <= (a.get("dip_pct") or 0) < 35),
        ("Dip < 20%  (dangkal)", lambda a: (a.get("dip_pct") or 0) < 20),
    ])
    print_breakdown(dip_breakdown, "Dip %")

    # ── PER BUNDLE PCT ──────────────────────────────────
    print_section("WIN RATE PER BUNDLE %")
    bnd_breakdown = breakdown(closed, "bundle_pct", [
        ("Bundle ≤ 2%  (bersih)",  lambda a: (a.get("bundle_pct") or 0) <= 2
                                              and a.get("bundle_pct") is not None),
        ("Bundle 3-10% (aman)",    lambda a: 2 < (a.get("bundle_pct") or 0) <= 10),
        ("Bundle > 10% (waspada)", lambda a: (a.get("bundle_pct") or 0) > 10),
        ("Bundle N/A",             lambda a: a.get("bundle_pct") is None),
    ])
    print_breakdown(bnd_breakdown, "Bundle %")

    # ── PER HOLD C1 ─────────────────────────────────────
    print_section("WIN RATE: HOLD C1 vs TIDAK")
    c1_breakdown = breakdown(closed, "holds_c1", [
        ("Hold C1 ✅", lambda a: a.get("holds_c1") is True),
        ("Tidak hold ❌", lambda a: a.get("holds_c1") is False),
    ])
    print_breakdown(c1_breakdown, "Hold C1")

    # ── PER WAKTU OUTCOME ───────────────────────────────
    print_section("DISTRIBUSI WAKTU KE OUTCOME")
    time_breakdown = breakdown(closed, "minutes", [
        ("< 15 menit",  lambda a: (a.get("minutes_to_outcome") or 0) < 15),
        ("15-30 menit", lambda a: 15 <= (a.get("minutes_to_outcome") or 0) < 30),
        ("30-60 menit", lambda a: 30 <= (a.get("minutes_to_outcome") or 0) < 60),
        ("1-2 jam",     lambda a: 60 <= (a.get("minutes_to_outcome") or 0) < 120),
        ("2-4 jam",     lambda a: (a.get("minutes_to_outcome") or 0) >= 120),
    ])
    print_breakdown(time_breakdown, "Waktu ke Outcome")

    # ── REKOMENDASI ─────────────────────────────────────
    print_section("INSIGHT OTOMATIS")
    insights = []

    if len(closed) >= args.min:
        # Grade B check
        b_data = grade_breakdown.get("Grade B", {})
        if b_data.get("count", 0) >= 5 and b_data.get("win_rate", 100) < 40:
            insights.append(
                f"🔴 Grade B win rate {b_data['win_rate']:.0f}% — "
                f"pertimbangkan hapus grade B dari alert")

        # LP check
        lp_na = lp_breakdown.get("LP N/A", {})
        lp_low = lp_breakdown.get("LP < 50%", {})
        if lp_na.get("count", 0) >= 5 and lp_na.get("win_rate", 100) < 40:
            insights.append(
                f"⚠️ Token tanpa data LP win rate "
                f"{lp_na.get('win_rate', 0):.0f}% — "
                f"pertimbangkan naikkan threshold safety")
        if lp_low.get("count", 0) >= 3 and lp_low.get("win_rate", 100) < 35:
            insights.append(
                f"🔴 LP < 50% win rate {lp_low.get('win_rate', 0):.0f}% — "
                f"naikkan MIN_LP_BURNED_T1 ke 70%")

        # Dip check
        dip_shallow = dip_breakdown.get("Dip < 20%  (dangkal)", {})
        dip_ideal   = dip_breakdown.get("Dip 35-49% (ideal)", {})
        if (dip_shallow.get("count", 0) >= 5
                and dip_shallow.get("win_rate", 100) < 35):
            insights.append(
                f"⚠️ Dip dangkal (<20%) win rate "
                f"{dip_shallow.get('win_rate', 0):.0f}% — "
                f"naikkan MIN_DIP_PCT ke 25%")
        if dip_ideal.get("win_rate", 0) >= 65:
            insights.append(
                f"✅ Dip 35-49% adalah sweet spot "
                f"({dip_ideal.get('win_rate', 0):.0f}% win rate) — "
                f"pertahankan range ini")

        # Hold C1 check
        no_c1 = c1_breakdown.get("Tidak hold ❌", {})
        if no_c1.get("count", 0) >= 5 and no_c1.get("win_rate", 100) < 30:
            insights.append(
                f"🔴 Token tidak hold C1 win rate "
                f"{no_c1.get('win_rate', 0):.0f}% — "
                f"naikkan penalti score untuk kondisi ini")

        # Tier check
        for tier_label, tier_data in tier_breakdown.items():
            if (tier_data.get("count", 0) >= 8
                    and tier_data.get("win_rate", 100) < 35):
                insights.append(
                    f"🔴 {tier_label} win rate "
                    f"{tier_data['win_rate']:.0f}% — "
                    f"pertimbangkan naikkan threshold grade minimum")

        # Expectancy
        if expectancy > 20:
            insights.append(
                f"✅ Expectancy {expectancy:+.1f}% — sistem performa sangat baik")
        elif expectancy > 0:
            insights.append(
                f"✅ Expectancy {expectancy:+.1f}% — sistem profitable, "
                f"terus kumpulkan data")
        elif expectancy < -10:
            insights.append(
                f"🔴 Expectancy {expectancy:+.1f}% — "
                f"sistem merugi, review parameter utama")

    if insights:
        for ins in insights:
            print(f"  {ins}")
    else:
        print(f"  Belum ada insight — butuh minimal "
              f"{args.min} closed alert per kategori")

    # ── 10 ALERT TERBARU ────────────────────────────────
    print_section("10 ALERT TERBARU")
    recent = sorted(
        [a for a in all_alerts if a.get("outcome") != "open"],
        key=lambda x: x.get("alert_time", 0),
        reverse=True)[:10]

    if recent:
        print(f"  {'Symbol':<10} {'Tier':>4} {'Grade':>6} "
              f"{'Result':>10} {'Outcome':<18} {'Waktu'}")
        print(f"  {'─'*10} {'─'*4} {'─'*6} "
              f"{'─'*10} {'─'*18} {'─'*16}")
        for a in recent:
            res = a.get("result_pct")
            res_str = f"{res:+.1f}%" if res is not None else "N/A"
            outcome = a.get("outcome", "open")
            outcome_color = (GREEN if outcome.startswith("win")
                             else RED if outcome in ("loss_sl", "rug_suspected")
                             else YELLOW)
            time_str = a.get("alert_time_str", "?")[:16]
            print(f"  {a['symbol']:<10} {a.get('tier','?'):>4} "
                  f"{a.get('grade','?'):>6} "
                  f"  {res_str:>8}  "
                  f"{c(f'{outcome:<16}', outcome_color)} "
                  f"  {time_str}")
    else:
        print("  Belum ada closed alert")

    # ── FOOTER ──────────────────────────────────────────
    print()
    print(c("═" * 60, CYAN))
    print(c("  ⚠️  Gunakan insight ini sebagai panduan, bukan aturan.", YELLOW))
    print(c("     Minimum 50+ closed alert untuk keputusan yang reliable.", YELLOW))
    print(c("═" * 60, CYAN))
    print()

    # ── EXPORT ──────────────────────────────────────────
    if args.export:
        export = {
            "generated_at":  __import__("datetime").datetime.now().isoformat(),
            "total_alerts":  len(all_alerts),
            "open":          open_count,
            "closed":        closed_count,
            "stats":         stats,
            "expectancy":    expectancy,
            "tier":          tier_breakdown,
            "grade":         grade_breakdown,
            "safety":        safety_breakdown,
            "lp_burned":     lp_breakdown,
            "dip_pct":       dip_breakdown,
            "bundle":        bnd_breakdown,
            "hold_c1":       c1_breakdown,
            "insights":      insights,
        }
        with open("summary.json", "w") as f:
            json.dump(export, f, indent=2)
        print(f"  ✅ Disimpan ke summary.json")

if __name__ == "__main__":
    main()
