import os
import time
import requests
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
LUNARCRUSH_KEY = os.environ.get("LUNARCRUSH_KEY", "")
HELIUS_KEY     = os.environ.get("HELIUS_KEY",     "")  # 🆕 v9.1

# ── STRATEGY PARAMETERS v9.1 ────────────────────────────

# 🟣 TIER 1 — EARLY ENTRY (Lucia pattern)
T1_MIN_MCAP      = 50000    # MCAP minimal $50K
T1_MAX_MCAP      = 300000   # MCAP maksimal $300K
T1_MIN_LIQUIDITY = 20000    # Liq minimal $20K
T1_MIN_VOL_1H    = 30000    # Vol 1H minimal $30K
T1_MAX_POSITION  = 0.05     # Max 0.05 SOL

# 🟢 TIER 2 — NORMAL ENTRY
T2_MIN_MCAP      = 300000
T2_MAX_MCAP      = 1000000
T2_MIN_LIQUIDITY = 50000
T2_MIN_VOL_1H    = 50000
T2_MAX_POSITION  = 0.1

# 🔵 TIER 3 — LATE/SAFE ENTRY
T3_MIN_MCAP      = 1000000
T3_MIN_LIQUIDITY = 100000
T3_MIN_VOL_1H    = 100000
T3_MAX_POSITION  = 0.2

# Safety thresholds (dari Lucia lesson)
MAX_BUNDLE_PCT    = 10.0    # Bundle max 10%
MAX_DEV_PCT       = 5.0     # Dev hold max 5%
MAX_SNIPER_PCT    = 5.0     # Sniper hold max 5%
MAX_TOP10_PCT     = 25.0    # Top 10 holder max 25%
MIN_LP_BURNED_PCT = 50.0    # LP Burned minimal 50%

# 🆕 v9.1 Hard Reject Filters (Dislike lesson)
# Token dengan kondisi ini langsung REJECT tanpa alert
MIN_TOKEN_AGE_T1  = 1.0     # T1: Token minimal 1 jam
MIN_TOKEN_AGE_T2  = 0.5     # T2: Token minimal 30 menit
MIN_LP_BURNED_T1  = 50.0    # T1: LP Burned minimal 50%
MIN_LP_BURNED_T2  = 20.0    # T2: LP Burned minimal 20%
MIN_HOLDERS_T1    = 100     # T1: Minimal 100 holders
MIN_HOLDERS_T2    = 50      # T2: Minimal 50 holders

# Pattern parameters
MIN_PUMP_PCT           = 150
MIN_DIP_PCT            = 20   # Lebih longgar untuk early
MAX_DIP_PCT            = 70
MIN_M5_REVERSAL        = 3.0  # Lebih longgar untuk early
MAX_VOLUME_DROP_PCT    = 80
MAX_SINGLE_CANDLE_DROP = 25
MIN_RECENT_TXNS        = 5

SCAN_INTERVAL_SEC   = 30
ALERT_COOLDOWN_SEC  = 300

# ── CHANGELOG ────────────────────────────────────────────
# v9.1 — Hard Reject Filters (18 Mar 2026):
#   Belajar dari Dislike token:
#   + Hard reject: age <1h untuk T1
#   + Hard reject: LP Burned <50% untuk T1
#   + Hard reject: holders <100 untuk T1
#   + Hard reject: age <30m untuk T2
#   + Hard reject: LP Burned <20% untuk T2
#
# v9.0 — Early Detection System (18 Mar 2026):
#   Belajar dari Lucia:
#   - Entry di MCAP $200K jauh lebih baik
#   - Safety first: LP Burned + Bundle + Dev %
#   + 3 Tier Alert (Early/Normal/Late)
#   + Helius API: holder concentration
#   + Safety Score system
#   + Early Detection (MCAP <$300K)
#   + Volume Staircase (improved)
#   + Liquidity Sweep (improved)
#   + Position sizing per tier

# ── STATE ────────────────────────────────────────────────
alerted_tokens = {}
social_cache   = {}
gmgn_cache     = {}
helius_cache   = {}
CACHE_SEC      = 300

# ── TELEGRAM ─────────────────────────────────────────────
def send_telegram(message: str):
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message,
                "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

# ── DEX SCREENER ─────────────────────────────────────────
def get_trending_solana_tokens():
    try:
        r = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=15)
        if r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:50]
    except Exception as e:
        print(f"[SCAN ERROR] {e}"); return []

def get_new_tokens_solana():
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        if r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:30]
    except Exception as e:
        print(f"[NEW TOKEN ERROR] {e}"); return []

def get_token_pairs(token_address: str):
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=15)
        if r.status_code != 200: return None
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs, key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)[0]
    except Exception as e:
        print(f"[PAIR ERROR] {e}"); return None

# ── 🆕 HELIUS API — HOLDER CONCENTRATION ─────────────────
def get_holder_data(token_address: str) -> dict:
    """
    Ambil data holder dari Helius:
    - Total holder count
    - Top holder concentration
    - Gini coefficient (distribusi)
    - Hub & Spoke detection proxy
    """
    cache_key = f"helius_{token_address}"
    if cache_key in helius_cache:
        ct, cd = helius_cache[cache_key]
        if time.time() - ct < CACHE_SEC: return cd

    result = {"available": False}

    if not HELIUS_KEY:
        return result

    try:
        # Get token largest accounts
        url  = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_address]
        }
        r = requests.post(url, json=payload, timeout=15)

        if r.status_code != 200:
            print(f"[HELIUS ERROR] Status: {r.status_code}")
            return result

        data    = r.json()
        accounts = data.get("result", {}).get("value", [])

        if not accounts:
            return result

        # Get total supply
        supply_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getTokenSupply",
            "params": [token_address]
        }
        r2   = requests.post(url, json=supply_payload, timeout=15)
        supply_data = r2.json() if r2.status_code == 200 else {}
        total_supply = float(
            supply_data.get("result", {})
            .get("value", {})
            .get("uiAmount", 0) or 0
        )

        if total_supply <= 0:
            return result

        # Kalkulasi konsentrasi
        balances = [float(acc.get("uiAmount", 0) or 0) for acc in accounts]
        balances = [b for b in balances if b > 0]

        if not balances:
            return result

        top1_pct  = (balances[0] / total_supply * 100) if len(balances) >= 1 else 0
        top5_pct  = (sum(balances[:5]) / total_supply * 100) if len(balances) >= 5 else (sum(balances) / total_supply * 100)
        top10_pct = (sum(balances[:10]) / total_supply * 100) if len(balances) >= 10 else (sum(balances) / total_supply * 100)

        # Gini coefficient (0 = merata, 1 = sangat terkonsentrasi)
        n = len(balances)
        if n > 1:
            sorted_b = sorted(balances)
            gini = sum(
                abs(sorted_b[i] - sorted_b[j])
                for i in range(n)
                for j in range(n)
            ) / (2 * n * n * (sum(sorted_b) / n)) if sum(sorted_b) > 0 else 0
        else:
            gini = 1.0

        # Hub & Spoke detection proxy:
        # Kalau top1 >> top2, kemungkinan hub & spoke
        hub_spoke_risk = False
        if len(balances) >= 2 and balances[1] > 0:
            ratio = balances[0] / balances[1]
            if ratio > 5:  # Top 1 lebih dari 5x top 2
                hub_spoke_risk = True

        result = {
            "available":      True,
            "top1_pct":       round(top1_pct, 2),
            "top5_pct":       round(top5_pct, 2),
            "top10_pct":      round(top10_pct, 2),
            "gini":           round(gini, 3),
            "hub_spoke_risk": hub_spoke_risk,
            "total_accounts": len(accounts),
        }

        print(f"[HELIUS] {token_address[:8]} | Top1:{top1_pct:.1f}% Top10:{top10_pct:.1f}% Gini:{gini:.2f}")

    except Exception as e:
        print(f"[HELIUS ERROR] {e}")

    helius_cache[cache_key] = (time.time(), result)
    return result

def score_holder_data(holder: dict) -> tuple:
    """Score distribusi holder dari Helius"""
    if not holder.get("available"):
        return 0, [], []

    score    = 0
    warnings = []
    signals  = []

    top1  = holder.get("top1_pct", 100)
    top5  = holder.get("top5_pct", 100)
    top10 = holder.get("top10_pct", 100)
    gini  = holder.get("gini", 1.0)
    hub   = holder.get("hub_spoke_risk", False)

    # Top 1 holder
    if top1 <= 2:
        score += 4; signals.append(f"✅ Top 1 holder hanya {top1:.1f}% — SANGAT MERATA!")
    elif top1 <= 5:
        score += 3; signals.append(f"✅ Top 1 holder: {top1:.1f}%")
    elif top1 <= 10:
        score += 1; signals.append(f"✅ Top 1 holder: {top1:.1f}%")
    elif top1 > 20:
        score -= 3; warnings.append(f"🔴 Top 1 holder: {top1:.1f}% — KONSENTRASI TINGGI!")
    else:
        score -= 1; warnings.append(f"⚠️ Top 1 holder: {top1:.1f}%")

    # Top 10 holder
    if top10 <= 10:
        score += 4; signals.append(f"✅ Top 10 hanya {top10:.1f}% — distribusi sempurna!")
    elif top10 <= MAX_TOP10_PCT:
        score += 3; signals.append(f"✅ Top 10: {top10:.1f}% — sehat")
    elif top10 <= 40:
        score += 1; warnings.append(f"⚠️ Top 10: {top10:.1f}%")
    else:
        score -= 2; warnings.append(f"🔴 Top 10: {top10:.1f}% — terkonsentrasi!")

    # Gini coefficient
    if gini <= 0.3:
        score += 3; signals.append(f"✅ Gini score: {gini:.2f} — distribusi sangat merata")
    elif gini <= 0.5:
        score += 2; signals.append(f"✅ Gini score: {gini:.2f} — distribusi sehat")
    elif gini <= 0.7:
        score += 1
    else:
        score -= 1; warnings.append(f"⚠️ Gini score: {gini:.2f} — distribusi tidak merata")

    # Hub & Spoke risk
    if hub:
        score -= 3
        warnings.append("🔴 Hub & Spoke pattern terdeteksi!")
    else:
        score += 1
        signals.append("✅ Tidak ada Hub & Spoke pattern")

    return score, warnings, signals

# ── 🆕 GMGN SAFETY DATA ───────────────────────────────────
def get_gmgn_safety(token_address: str) -> dict:
    """Ambil safety data dari GMGN"""
    cache_key = f"gmgn_{token_address}"
    if cache_key in gmgn_cache:
        ct, cd = gmgn_cache[cache_key]
        if time.time() - ct < CACHE_SEC: return cd

    result = {"available": False}
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://gmgn.ai/"
        }
        url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{token_address}"
        r   = requests.get(url, headers=headers, timeout=10)

        if r.status_code == 200:
            token = r.json().get("data", {}).get("token", {})
            if token:
                created_at = token.get("open_timestamp", 0)
                age_hours  = (time.time() - created_at) / 3600 if created_at else 0

                result = {
                    "available":      True,
                    "age_hours":      round(age_hours, 2),
                    "holder_count":   token.get("holder_count", 0) or 0,
                    "top10_pct":      float(token.get("top_10_holder_rate", 0) or 0) * 100,
                    "dev_pct":        float(token.get("dev_token_burn_ratio", 0) or 0) * 100,
                    "sniper_count":   token.get("sniper_count", 0) or 0,
                    "smart_buy_24h":  token.get("smart_buy_24h", 0) or 0,
                    "smart_sell_24h": token.get("smart_sell_24h", 0) or 0,
                    "burn_ratio":     float(token.get("burn_ratio", 0) or 0),
                    "is_honeypot":    token.get("is_honeypot", False),
                    "rug_ratio":      float(token.get("rug_ratio", 0) or 0),
                    "lp_burned_pct":  float(token.get("burn_ratio", 0) or 0) * 100,
                    "bundle_pct":     float(token.get("bundle_pct", 0) or 0) * 100,
                    "dev_hold_pct":   float(token.get("dev_token_burn_ratio", 0) or 0) * 100,
                }
                print(f"[GMGN] {token_address[:8]} | Age:{age_hours:.1f}h | LP:{result['lp_burned_pct']:.0f}% | Bundle:{result['bundle_pct']:.1f}%")

    except Exception as e:
        print(f"[GMGN ERROR] {e}")

    gmgn_cache[cache_key] = (time.time(), result)
    return result

def score_safety(gmgn: dict) -> tuple:
    """
    Safety Score berdasarkan Lucia criteria:
    LP Burned, Bundle, Dev %, Snipers
    """
    if not gmgn.get("available"):
        return 0, [], [], 0

    score    = 0
    warnings = []
    signals  = []
    safety_score = 0  # 0-100

    is_honeypot  = gmgn.get("is_honeypot", False)
    rug_ratio    = gmgn.get("rug_ratio", 0)
    lp_burned    = gmgn.get("lp_burned_pct", 0)
    bundle_pct   = gmgn.get("bundle_pct", 0)
    dev_pct      = gmgn.get("dev_hold_pct", 0)
    sniper_count = gmgn.get("sniper_count", 0)
    holders      = gmgn.get("holder_count", 0)
    age_hours    = gmgn.get("age_hours", 0)
    smart_buy    = gmgn.get("smart_buy_24h", 0)
    smart_sell   = gmgn.get("smart_sell_24h", 0)

    # Hard reject
    if is_honeypot:
        return -100, ["🔴 HONEYPOT!"], [], 0
    if rug_ratio > 0.8:
        return -100, ["🔴 RUG RATIO TINGGI!"], [], 0

    # 1. LP Burned (PALING PENTING — Lucia lesson!)
    if lp_burned >= 100:
        score += 8; safety_score += 30
        signals.append("🔒 LP Burned: 100% — TIDAK BISA RUG!")
    elif lp_burned >= 80:
        score += 6; safety_score += 25
        signals.append(f"🔒 LP Burned: {lp_burned:.0f}% — sangat aman")
    elif lp_burned >= 50:
        score += 3; safety_score += 15
        signals.append(f"🔒 LP Burned: {lp_burned:.0f}%")
    elif lp_burned >= 20:
        score += 1; safety_score += 5
        warnings.append(f"⚠️ LP Burned rendah: {lp_burned:.0f}%")
    else:
        score -= 2
        warnings.append(f"🔴 LP Tidak diburn: {lp_burned:.0f}% — risiko rug!")

    # 2. Bundle % (Lucia: 1.04% = PERFECT)
    if bundle_pct <= 2:
        score += 6; safety_score += 25
        signals.append(f"✅ Bundle: {bundle_pct:.1f}% — SANGAT BERSIH!")
    elif bundle_pct <= MAX_BUNDLE_PCT:
        score += 4; safety_score += 15
        signals.append(f"✅ Bundle: {bundle_pct:.1f}% — aman")
    elif bundle_pct <= 20:
        score -= 1; safety_score += 5
        warnings.append(f"⚠️ Bundle: {bundle_pct:.1f}% — perhatikan")
    else:
        score -= 4
        warnings.append(f"🔴 Bundle tinggi: {bundle_pct:.1f}% — koordinasi wallet!")

    # 3. Dev Holdings (Lucia: 0.35% = PERFECT)
    if dev_pct <= 1:
        score += 5; safety_score += 20
        signals.append(f"✅ Dev hold: {dev_pct:.1f}% — hampir nol!")
    elif dev_pct <= MAX_DEV_PCT:
        score += 3; safety_score += 10
        signals.append(f"✅ Dev hold: {dev_pct:.1f}% — wajar")
    elif dev_pct <= 15:
        score -= 1; safety_score += 5
        warnings.append(f"⚠️ Dev hold: {dev_pct:.1f}%")
    else:
        score -= 3
        warnings.append(f"🔴 Dev hold tinggi: {dev_pct:.1f}% — risiko dump!")

    # 4. Snipers (Lucia: 0.35% = PERFECT)
    if holders > 0:
        sniper_ratio = sniper_count / holders if holders > 0 else 0
        if sniper_ratio <= 0.01:
            score += 3; safety_score += 15
            signals.append(f"✅ Snipers: {sniper_count} — hampir tidak ada!")
        elif sniper_ratio <= 0.05:
            score += 2; safety_score += 8
            signals.append(f"✅ Snipers: {sniper_count} — wajar")
        elif sniper_ratio <= 0.1:
            warnings.append(f"⚠️ Snipers: {sniper_count} ({sniper_ratio*100:.0f}%)")
        else:
            score -= 2
            warnings.append(f"🔴 Banyak snipers: {sniper_count} ({sniper_ratio*100:.0f}%)")

    # 5. Token Age
    if age_hours >= 6:
        score += 2; signals.append(f"✅ Token age: {age_hours:.0f}h")
    elif age_hours >= 2:
        score += 1; signals.append(f"✅ Token age: {age_hours:.1f}h")
    elif age_hours >= 0.5:
        warnings.append(f"⚠️ Token sangat baru: {age_hours:.1f}h")
    else:
        score -= 2
        warnings.append(f"🔴 Token <30 menit! Sangat berisiko")

    # 6. Smart Money
    sm_net = smart_buy - smart_sell
    if sm_net > 5:
        score += 3; signals.append(f"✅ Smart money NET BUY: +{sm_net}")
    elif sm_net > 0:
        score += 1
    elif sm_net < -5:
        score -= 2; warnings.append(f"⚠️ Smart money NET SELL: {sm_net}")

    # 7. Holder Count
    if holders >= 2000:
        score += 3; signals.append(f"✅ Holders: {holders:,}")
    elif holders >= 1000:
        score += 2; signals.append(f"✅ Holders: {holders:,}")
    elif holders >= 500:
        score += 1
    elif holders < 100:
        warnings.append(f"⚠️ Holders sedikit: {holders:,}")

    return score, warnings, signals, safety_score

# ── PATTERN DETECTION ─────────────────────────────────────
def detect_volume_staircase(vol_m5, vol_h1, vol_h6, vol_h24) -> tuple:
    score = 0; signals = []
    avg_h1 = vol_h1 / 12 if vol_h1 > 0 else 0
    avg_h6 = vol_h6 / 6  if vol_h6 > 0 else 0
    if avg_h1 > 0:
        r1 = vol_m5 / avg_h1
        if r1 >= 2.0:   score += 4; signals.append(f"🪜 Volume Staircase KUAT: {r1:.1f}x!")
        elif r1 >= 1.5: score += 3; signals.append(f"🪜 Volume Staircase: {r1:.1f}x")
        elif r1 >= 1.0: score += 1
    if avg_h6 > 0:
        r2 = vol_h1 / avg_h6
        if r2 >= 1.5:   score += 2; signals.append(f"📈 Vol momentum h1/h6: {r2:.1f}x")
        elif r2 >= 1.0: score += 1
    return score, signals

def detect_liquidity_sweep(change_m5, change_h1, buys_m5, sells_m5) -> tuple:
    score = 0; signals = []
    if change_h1 < -30 and change_m5 > 10:
        score += 4; signals.append(f"⚡ Liquidity Sweep: h1{change_h1:.0f}% → m5+{change_m5:.0f}%!")
    elif change_h1 < -20 and change_m5 > 5:
        score += 2; signals.append(f"⚡ Possible Sweep: h1{change_h1:.0f}% → m5+{change_m5:.0f}%")
    r_m5 = buys_m5 / max(sells_m5, 1)
    if change_h1 < -20 and r_m5 >= 2.0:
        score += 2; signals.append(f"✅ Buyer flip setelah dip: {r_m5:.1f}x")
    return score, signals

# ── ANTI FALSE POSITIVE ───────────────────────────────────
def check_volume_collapse(vol_m5, vol_h1, vol_h6):
    avg_5m = vol_h1 / 12 if vol_h1 > 0 else 0
    avg_h6 = vol_h6 / 6  if vol_h6 > 0 else 0
    if avg_5m > 0 and vol_m5 / avg_5m < 0.05:
        return False, "🔴 Volume collapse!"
    if avg_h6 > 0 and vol_h1 > 0 and vol_h1 / avg_h6 < 0.2:
        return False, "🔴 Volume drop >80%!"
    return True, ""

def check_price_velocity(change_m5, buys_m5, sells_m5):
    if change_m5 < -MAX_SINGLE_CANDLE_DROP:
        return False, f"🔴 Dump masif m5: {change_m5:.0f}%!"
    if change_m5 < -15 and buys_m5 / max(sells_m5, 1) < 0.3:
        return False, "🔴 Dump aktif!"
    return True, ""

# ── DETERMINE TIER ────────────────────────────────────────
def determine_tier(mcap_usd, liquidity_usd, vol_h1) -> str | None:
    if (T1_MIN_MCAP <= mcap_usd <= T1_MAX_MCAP and
        liquidity_usd >= T1_MIN_LIQUIDITY and
        vol_h1 >= T1_MIN_VOL_1H):
        return "T1"
    if (T2_MIN_MCAP <= mcap_usd <= T2_MAX_MCAP and
        liquidity_usd >= T2_MIN_LIQUIDITY and
        vol_h1 >= T2_MIN_VOL_1H):
        return "T2"
    if (mcap_usd >= T3_MIN_MCAP and
        liquidity_usd >= T3_MIN_LIQUIDITY and
        vol_h1 >= T3_MIN_VOL_1H):
        return "T3"
    return None

# ── LUNARCRUSH ────────────────────────────────────────────
def get_social_data(token_symbol: str) -> dict:
    cache_key = f"lunar_{token_symbol.lower()}"
    if cache_key in social_cache:
        ct, cd = social_cache[cache_key]
        if time.time() - ct < CACHE_SEC: return cd
    if not LUNARCRUSH_KEY: return {"available": False}
    try:
        headers = {"Authorization": f"Bearer {LUNARCRUSH_KEY}"}
        r = requests.get(
            "https://lunarcrush.com/api4/public/coins/list/v2",
            headers=headers,
            params={"sort": "galaxy_score", "limit": 5, "search": token_symbol},
            timeout=10)
        if r.status_code != 200: return {"available": False}
        coins = r.json().get("data", [])
        if not coins: return {"available": False}
        coin  = next((c for c in coins if c.get("symbol","").upper() == token_symbol.upper()), coins[0])
        v24h  = coin.get("social_volume_24h", 0) or 0
        vprev = coin.get("social_volume_prev", v24h) or v24h
        trend = ((v24h - vprev) / vprev * 100) if vprev > 0 else 0
        result = {
            "available":     True,
            "galaxy_score":  round(coin.get("galaxy_score", 0) or 0, 1),
            "alt_rank":      coin.get("alt_rank", 9999) or 9999,
            "mention_trend": round(trend, 1),
            "kol_active":    (coin.get("interactions_24h", 0) or 0) > 10000,
        }
        social_cache[cache_key] = (time.time(), result)
        return result
    except: return {"available": False}

def score_social(social: dict) -> tuple:
    if not social.get("available"): return 0, []
    score = 0; signals = []
    gs  = social.get("galaxy_score", 0)
    ar  = social.get("alt_rank", 9999)
    mt  = social.get("mention_trend", 0)
    kol = social.get("kol_active", False)
    if gs >= 60:   score += 4; signals.append(f"🌟 Galaxy: {gs}/100")
    elif gs >= 40: score += 3; signals.append(f"🌟 Galaxy: {gs}/100")
    elif gs >= 20: score += 1
    if ar <= 100:   score += 3; signals.append(f"🏅 AltRank: #{ar}")
    elif ar <= 500: score += 2
    if mt >= 100:  score += 4; signals.append(f"🔥 Hype +{mt:.0f}%!")
    elif mt >= 50: score += 3; signals.append(f"📈 Hype +{mt:.0f}%")
    elif mt >= 20: score += 2
    elif mt >= 0:  score += 1
    if kol: score += 3; signals.append("👑 KOL aktif!")
    return score, signals

# ── MAIN ANALYZE v9.0 ────────────────────────────────────
def analyze_pair(pair: dict):
    try:
        token_address = pair.get("baseToken", {}).get("address", "")
        token_name    = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_address  = pair.get("pairAddress", "")
        chain_id      = pair.get("chainId", "")
        dex_id        = pair.get("dexId", "")
        price_usd     = float(pair.get("priceUsd", 0) or 0)

        pc         = pair.get("priceChange", {})
        change_m5  = float(pc.get("m5",  0) or 0)
        change_h1  = float(pc.get("h1",  0) or 0)
        change_h6  = float(pc.get("h6",  0) or 0)
        change_h24 = float(pc.get("h24", 0) or 0)

        vol        = pair.get("volume", {})
        vol_m5     = float(vol.get("m5",  0) or 0)
        vol_h1     = float(vol.get("h1",  0) or 0)
        vol_h6     = float(vol.get("h6",  0) or 0)
        vol_h24    = float(vol.get("h24", 0) or 0)

        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        mcap_usd      = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)

        txns_data = pair.get("txns", {})
        txns_h24  = sum([int(txns_data.get("h24", {}).get("buys", 0) or 0),
                         int(txns_data.get("h24", {}).get("sells", 0) or 0)])
        txns_m5   = txns_data.get("m5", {})
        txns_h1   = txns_data.get("h1", {})
        buys_m5   = int(txns_m5.get("buys",  0) or 0)
        sells_m5  = int(txns_m5.get("sells", 0) or 0)
        buys_h1   = int(txns_h1.get("buys",  0) or 0)
        sells_h1  = int(txns_h1.get("sells", 0) or 0)

        # ── Filter dasar ──────────────────────────────────
        if price_usd <= 0 or mcap_usd <= 0: return None

        # Tentukan tier dulu
        tier = determine_tier(mcap_usd, liquidity_usd, vol_h1)
        if not tier: return None

        # Pump check
        pump_pct = change_h24 if change_h24 >= MIN_PUMP_PCT else (
                   change_h6  if change_h6  >= MIN_PUMP_PCT else None)
        if pump_pct is None: return None

        # Harus ada aktivitas
        currently_dipping  = (change_h1 < -10) or (change_m5 < -5)
        still_momentum     = (change_h6 > 20 and change_m5 >= 0)
        early_accumulation = (tier == "T1" and change_h24 > 100 and vol_h1 > T1_MIN_VOL_1H)

        if not currently_dipping and not still_momentum and not early_accumulation:
            return None

        # Dip calculation
        if change_h1 < 0:
            est_high      = price_usd / (1 + change_h1 / 100)
            dip_from_high = ((est_high - price_usd) / est_high) * 100
        else:
            dip_from_high = abs(change_m5) if change_m5 < 0 else 0

        if currently_dipping:
            if dip_from_high > MAX_DIP_PCT: return None
            if dip_from_high < MIN_DIP_PCT and not early_accumulation: return None
            min_m5 = MIN_M5_REVERSAL if tier != "T1" else 0
            if change_m5 < min_m5 and not still_momentum and not early_accumulation: return None

        holds_open_c1 = change_h24 > 20

        # ── Anti false positive ───────────────────────────
        vol_ok, vol_msg = check_volume_collapse(vol_m5, vol_h1, vol_h6)
        if not vol_ok: print(f"[REJECT] {token_symbol} {vol_msg}"); return None

        vel_ok, vel_msg = check_price_velocity(change_m5, buys_m5, sells_m5)
        if not vel_ok: print(f"[REJECT] {token_symbol} {vel_msg}"); return None

        # ── SAFETY CHECK (PRIORITY #1) ────────────────────
        gmgn_data = get_gmgn_safety(token_address)
        safety_score_val, s_warn, s_sig, safety_pct = score_safety(gmgn_data)

        # Hard reject dari safety
        if safety_score_val <= -100:
            print(f"[REJECT SAFETY] {token_symbol} {s_warn[0]}")
            return None

        # 🆕 v9.1 HARD REJECT FILTERS
        if gmgn_data.get("available"):
            age_h       = gmgn_data.get("age_hours", 0)
            lp_burned   = gmgn_data.get("lp_burned_pct", 0)
            holders     = gmgn_data.get("holder_count", 0)

            if tier == "T1":
                # T1 paling ketat — harus sudah proven minimal
                if age_h < MIN_TOKEN_AGE_T1:
                    print(f"[REJECT v9.1] {token_symbol} — T1 age {age_h:.1f}h < {MIN_TOKEN_AGE_T1}h")
                    return None
                if lp_burned < MIN_LP_BURNED_T1:
                    print(f"[REJECT v9.1] {token_symbol} — T1 LP {lp_burned:.0f}% < {MIN_LP_BURNED_T1}%")
                    return None
                if holders < MIN_HOLDERS_T1:
                    print(f"[REJECT v9.1] {token_symbol} — T1 holders {holders} < {MIN_HOLDERS_T1}")
                    return None

            elif tier == "T2":
                # T2 lebih longgar tapi tetap ada minimum
                if age_h < MIN_TOKEN_AGE_T2:
                    print(f"[REJECT v9.1] {token_symbol} — T2 age {age_h:.1f}h < {MIN_TOKEN_AGE_T2}h")
                    return None
                if lp_burned < MIN_LP_BURNED_T2:
                    print(f"[REJECT v9.1] {token_symbol} — T2 LP {lp_burned:.0f}% < {MIN_LP_BURNED_T2}%")
                    return None
                if holders < MIN_HOLDERS_T2:
                    print(f"[REJECT v9.1] {token_symbol} — T2 holders {holders} < {MIN_HOLDERS_T2}")
                    return None

        # Untuk T1, safety sangat ketat
        critical_safety = [w for w in s_warn if w.startswith("🔴")]
        if tier == "T1" and len(critical_safety) >= 1:
            print(f"[REJECT T1 SAFETY] {token_symbol} — {critical_safety[0]}")
            return None
        if len(critical_safety) >= 2:
            print(f"[REJECT SAFETY] {token_symbol} — 2+ safety warnings")
            return None

        # ── HOLDER CONCENTRATION (Helius) ─────────────────
        holder_data = get_holder_data(token_address)
        h_score, h_warn, h_sig = score_holder_data(holder_data)

        # ── PATTERN SCORING ───────────────────────────────
        all_warnings = list(s_warn) + list(h_warn)
        all_signals  = list(s_sig)  + list(h_sig)
        total_score  = safety_score_val + h_score

        # Volume staircase
        vs_score, vs_sig = detect_volume_staircase(vol_m5, vol_h1, vol_h6, vol_h24)
        total_score += vs_score; all_signals.extend(vs_sig)

        # Liquidity sweep
        ls_score, ls_sig = detect_liquidity_sweep(change_m5, change_h1, buys_m5, sells_m5)
        total_score += ls_score; all_signals.extend(ls_sig)

        # Reversal strength
        r_m5 = buys_m5 / max(sells_m5, 1)
        r_h1 = buys_h1 / max(sells_h1, 1)
        if change_m5 >= 20:   total_score += 4; all_signals.append(f"✅ Reversal KUAT: +{change_m5:.0f}%")
        elif change_m5 >= 10: total_score += 3; all_signals.append(f"✅ Reversal bagus: +{change_m5:.0f}%")
        elif change_m5 >= 5:  total_score += 2; all_signals.append(f"✅ Reversal mulai: +{change_m5:.0f}%")
        if r_m5 >= 2.0: total_score += 3; all_signals.append(f"✅ Buyer dominan m5: {r_m5:.1f}x")
        elif r_m5 >= 1.5: total_score += 2
        elif r_m5 >= 1.0: total_score += 1

        # Dip quality
        if dip_from_high >= 50:   total_score += 3; all_signals.append(f"✅ Dip ideal: -{dip_from_high:.0f}%")
        elif dip_from_high >= 35: total_score += 2
        if holds_open_c1: total_score += 2

        # Pump strength
        if pump_pct >= 500:   total_score += 3
        elif pump_pct >= 200: total_score += 2
        elif pump_pct >= 150: total_score += 1

        # Social
        social_data = get_social_data(token_symbol)
        soc_score, soc_sig = score_social(social_data)
        total_score += soc_score; all_signals.extend(soc_sig)

        # DEX bonus
        dex_lower = dex_id.lower()
        if "raydium" in dex_lower or "orca" in dex_lower:
            total_score += 2; all_signals.append("✅ DEX terpercaya: Raydium/Orca")
        elif "pump" in dex_lower:
            all_warnings.append("⚠️ PumpSwap — risiko lebih tinggi")

        # Volume bonus
        if vol_h24 >= 3000000: total_score += 2
        elif vol_h24 >= 1000000: total_score += 1

        # ── GRADE PER TIER ────────────────────────────────
        if tier == "T1":
            if total_score >= 20:   grade, status = "A", "🟢 Early setup bagus!"
            elif total_score >= 14: grade, status = "B", "🟡 Early setup cukup"
            elif total_score >= 8:  grade, status = "C", "🟠 Early setup lemah"
            else: return None
        elif tier == "T2":
            if total_score >= 25:   grade, status = "A+", "💎 Setup premium!"
            elif total_score >= 20: grade, status = "A",  "🟢 Setup sangat bagus!"
            elif total_score >= 14: grade, status = "B",  "🟡 Setup cukup"
            elif total_score >= 8:  grade, status = "C",  "🟠 Setup lemah"
            else: return None
        else:  # T3
            if total_score >= 28:   grade, status = "A+", "💎 Setup premium — MCAP besar!"
            elif total_score >= 22: grade, status = "A",  "🟢 Setup bagus"
            elif total_score >= 15: grade, status = "B",  "🟡 Setup cukup"
            elif total_score >= 8:  grade, status = "C",  "🟠 Setup lemah"
            else: return None

        # Position sizing
        pos_map = {"T1": T1_MAX_POSITION, "T2": T2_MAX_POSITION, "T3": T3_MAX_POSITION}

        return {
            "token_name": token_name, "token_symbol": token_symbol,
            "token_address": token_address,
            "price_usd": price_usd, "pump_pct": round(pump_pct, 1),
            "dip_pct": round(dip_from_high, 1), "holds_open_c1": holds_open_c1,
            "vol_m5": vol_m5, "vol_h1": vol_h1, "vol_h6": vol_h6, "vol_h24": vol_h24,
            "liquidity_usd": liquidity_usd, "mcap_usd": mcap_usd, "txns_h24": txns_h24,
            "buys_h1": buys_h1, "sells_h1": sells_h1,
            "buys_m5": buys_m5, "sells_m5": sells_m5,
            "change_m5": change_m5, "change_h1": change_h1,
            "change_h6": change_h6, "change_h24": change_h24,
            "total_score": total_score, "pattern_status": status, "pattern_grade": grade,
            "tier": tier, "max_position": pos_map[tier],
            "safety_pct": safety_pct,
            "signals": all_signals[:5], "warnings": all_warnings[:3],
            # Safety data
            "gmgn_available": gmgn_data.get("available", False),
            "lp_burned_pct":  gmgn_data.get("lp_burned_pct", 0),
            "bundle_pct":     gmgn_data.get("bundle_pct", 0),
            "dev_hold_pct":   gmgn_data.get("dev_hold_pct", 0),
            "sniper_count":   gmgn_data.get("sniper_count", 0),
            "holder_count":   gmgn_data.get("holder_count", 0),
            "age_hours":      gmgn_data.get("age_hours", 0),
            "smart_buy":      gmgn_data.get("smart_buy_24h", 0),
            "smart_sell":     gmgn_data.get("smart_sell_24h", 0),
            # Helius data
            "helius_available": holder_data.get("available", False),
            "top1_pct":   holder_data.get("top1_pct", 0),
            "top10_pct":  holder_data.get("top10_pct", 0),
            "gini":       holder_data.get("gini", 0),
            "hub_spoke":  holder_data.get("hub_spoke_risk", False),
            # Social
            "galaxy_score":   social_data.get("galaxy_score", 0),
            "mention_trend":  social_data.get("mention_trend", 0),
            "kol_active":     social_data.get("kol_active", False),
            "sl_price": price_usd * 0.85,
            "tp1_price": price_usd * 2.0,
            "tp2_price": price_usd * 3.0,
            "chart_url":  f"https://dexscreener.com/{chain_id}/{pair_address}",
            "gmgn_url":   f"https://gmgn.ai/sol/token/{token_address}",
            "axiom_url":  f"https://axiom.xyz/sol/{token_address}",
            "dex_id": dex_id,
        }

    except Exception as e:
        print(f"[ANALYZE ERROR] {e}")
        return None

# ── FORMAT ALERT v9.0 ─────────────────────────────────────
def format_alert(s: dict) -> str:
    tier_info = {
        "T1": ("🟣", "EARLY ENTRY", f"MCAP ${s['mcap_usd']/1000:.0f}K — potensi 5-10x!", f"Max {s['max_position']} SOL (HIGH RISK!)"),
        "T2": ("🟢", "NORMAL ENTRY", f"MCAP ${s['mcap_usd']/1000:.0f}K — momentum bagus", f"Max {s['max_position']} SOL"),
        "T3": ("🔵", "LATE/SAFE ENTRY", f"MCAP ${s['mcap_usd']/1000:.0f}K — established token", f"Max {s['max_position']} SOL"),
    }
    emoji, tier_label, tier_desc, pos_text = tier_info.get(s["tier"], ("⚪", "", "", ""))
    grade_emoji = {"A+": "💎", "A": "🏆", "B": "🥈", "C": "⚡"}.get(s["pattern_grade"], "")

    signals_text = "\n".join(s["signals"][:4]) if s["signals"] else "—"
    warn_text    = "\n".join(s["warnings"][:3]) if s["warnings"] else "✅ Tidak ada warning"

    # Safety block
    lp_text      = f"{s['lp_burned_pct']:.0f}%"
    lp_emoji     = "🔒" if s['lp_burned_pct'] >= 80 else "⚠️" if s['lp_burned_pct'] >= 50 else "🔴"
    bundle_emoji = "✅" if s['bundle_pct'] <= 5 else "⚠️" if s['bundle_pct'] <= 15 else "🔴"
    dev_emoji    = "✅" if s['dev_hold_pct'] <= 5 else "⚠️" if s['dev_hold_pct'] <= 15 else "🔴"
    sm_net       = s['smart_buy'] - s['smart_sell']
    sm_text      = f"+{sm_net} NET BUY 🟢" if sm_net > 0 else (f"{sm_net} NET SELL 🔴" if sm_net < 0 else "Netral")

    safety_bar   = "█" * int(s['safety_pct'] / 10) + "░" * (10 - int(s['safety_pct'] / 10))

    # Helius block
    if s["helius_available"]:
        hub_text    = "⚠️ TERDETEKSI!" if s["hub_spoke"] else "✅ Tidak ada"
        helius_block = (
            f"\n📊 <b>HOLDER ANALYSIS (Helius):</b>\n"
            f"  Top 1: <b>{s['top1_pct']:.1f}%</b> | "
            f"Top 10: <b>{s['top10_pct']:.1f}%</b>\n"
            f"  Gini: <b>{s['gini']:.2f}</b> | "
            f"Hub&Spoke: {hub_text}"
        )
    else:
        helius_block = ""

    vol24_text = f"${s['vol_h24']/1000000:.1f}M" if s['vol_h24'] >= 1e6 else f"${s['vol_h24']/1000:.0f}K"

    return f"""
🚨 <b>DIP &amp; RIP ALERT v9.0!</b>

{emoji} <b>{tier_label}</b>
📍 {tier_desc}
💰 Rekomendasi: <b>{pos_text}</b>

🪙 <b>{s['token_name']} ({s['token_symbol']})</b>
📊 DEX: {s['dex_id'].upper()} | Solana | ⏱ {s['age_hours']:.1f}h

{grade_emoji} <b>{s['pattern_status']}</b>
📊 Score: {s['total_score']} | Grade: {s['pattern_grade']}

💹 Harga: <b>${s['price_usd']:.8f}</b>
📈 Pump: <b>+{s['pump_pct']}%</b> | Dip: <b>-{s['dip_pct']}%</b>
📊 m5: <b>{s['change_m5']:+.1f}%</b> | h1: {s['change_h1']:+.1f}% | h6: {s['change_h6']:+.1f}%

🔒 <b>SAFETY SCORE: {s['safety_pct']}/100</b>
  [{safety_bar}]
  {lp_emoji} LP Burned: <b>{lp_text}</b>
  {bundle_emoji} Bundle: <b>{s['bundle_pct']:.1f}%</b>
  {dev_emoji} Dev Hold: <b>{s['dev_hold_pct']:.1f}%</b>
  🎯 Snipers: <b>{s['sniper_count']}</b>
  👥 Holders: <b>{s['holder_count']:,}</b>
  💡 Smart $: <b>{sm_text}</b>
{helius_block}

✅ <b>SINYAL POSITIF:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warn_text}

📦 MCAP: <b>${s['mcap_usd']/1000:.0f}K</b> | Liq: <b>${s['liquidity_usd']/1000:.0f}K</b>
📊 Vol 24H: {vol24_text} | Txns: {s['txns_h24']:,}

💰 <b>ENTRY ZONE:</b>
  Beli:        <b>${s['price_usd']:.8f}</b>
  🔴 Stop Loss: <b>${s['sl_price']:.8f}</b> (-15%)
  🟡 Target 1:  <b>${s['tp1_price']:.8f}</b> (+100%)
  🟢 Target 2:  <b>${s['tp2_price']:.8f}</b> (+200%)

🔗 <a href="{s['chart_url']}">DEX</a> | <a href="{s['gmgn_url']}">GMGN</a> | <a href="{s['axiom_url']}">Axiom</a>

⚡ Selalu konfirmasi di GMGN + Axiom!
⚠️ BUKAN financial advice. DYOR!
""".strip()

# ── MAIN ─────────────────────────────────────────────────
def scan_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    trending   = get_trending_solana_tokens()
    new_tokens = get_new_tokens_solana()
    all_addr   = set()
    for t in trending + new_tokens:
        addr = t.get("tokenAddress", "") or t.get("address", "")
        if addr: all_addr.add(addr)

    print(f"[SCAN] {len(all_addr)} token...")
    t1 = t2 = t3 = 0
    for addr in list(all_addr)[:40]:
        if time.time() - alerted_tokens.get(addr, 0) < ALERT_COOLDOWN_SEC: continue
        pair = get_token_pairs(addr)
        if not pair: continue
        signal = analyze_pair(pair)
        if signal:
            if signal["tier"] == "T1": t1 += 1
            elif signal["tier"] == "T2": t2 += 1
            else: t3 += 1
            print(f"[{signal['tier']}] {signal['token_symbol']} "
                  f"Grade:{signal['pattern_grade']} Score:{signal['total_score']} "
                  f"Safety:{signal['safety_pct']}/100 LP:{signal['lp_burned_pct']:.0f}%")
            if send_telegram(format_alert(signal)):
                alerted_tokens[addr] = time.time()
        time.sleep(0.5)
    print(f"[DONE] T1:{t1} T2:{t2} T3:{t3}")

def main():
    helius_status = "✅ Aktif" if HELIUS_KEY else "⚠️ Belum ada key"
    lunar_status  = "✅ Aktif" if LUNARCRUSH_KEY else "⚠️ Belum ada key"
    print("=" * 60)
    print("  DIP & RIP BOT v9.1 — EARLY DETECTION + SAFETY FIRST")
    print("=" * 60)
    print(f"  Helius API  : {helius_status}")
    print(f"  LunarCrush : {lunar_status}")
    print(f"  🟣 T1 Early : MCAP ${T1_MIN_MCAP/1000:.0f}K-${T1_MAX_MCAP/1000:.0f}K")
    print(f"  🟢 T2 Normal: MCAP ${T2_MIN_MCAP/1000:.0f}K-${T2_MAX_MCAP/1000:.0f}K")
    print(f"  🔵 T3 Late  : MCAP >${T3_MIN_MCAP/1000:.0f}K")
    print("=" * 60)

    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v9.1 aktif!</b>\n\n"
        "🆕 Hard Reject Filters:\n"
        f"⏱ T1: Min age <b>{MIN_TOKEN_AGE_T1}h</b> | LP <b>{MIN_LP_BURNED_T1:.0f}%</b> | Holders <b>{MIN_HOLDERS_T1}</b>\n"
        f"⏱ T2: Min age <b>{MIN_TOKEN_AGE_T2}h</b> | LP <b>{MIN_LP_BURNED_T2:.0f}%</b> | Holders <b>{MIN_HOLDERS_T2}</b>\n\n"
        "📚 Belajar dari Dislike token:\n"
        "✅ Token 0h + LP 0% = auto REJECT!\n"
        "✅ Holders 0 = auto REJECT!\n\n"
        "🟣 T1 Early | 🟢 T2 Normal | 🔵 T3 Late\n"
        "🚨 Scan setiap 30 detik!"
    )
    while True:
        try: scan_once()
        except Exception as e: print(f"[MAIN ERROR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
