import os
import time
import json
import requests
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════
#   DIP & RIP BOT v9.9.3 — CHART IS KING
#   Core Philosophy: Chart Dulu, Data Pendukung Kemudian
#
#   ARSITEKTUR BARU:
#   [1] Filter dasar (harga, mcap, tier)
#   [2] PATTERN CHECK — PRIMER (dari DexScreener saja)
#       Chart tidak bisa bohong. Jika tidak ada pattern → skip.
#   [3] HARD REJECT — hanya untuk bahaya yang TERBUKTI:
#       Honeypot, Mint/Freeze Authority, Copycat/risk kritis,
#       Top10 > 70%, Hub&Spoke untuk T1/T2/T3
#   [4] SAFETY SCORING — SEKUNDER (menambah/mengurangi keyakinan)
#       Data ada = ±score. Data tidak ada = 0 (netral, bukan reject).
#   [5] Grade B minimum → alert dikirim
#
#   FILOSOFI:
#   Data luar chart (GMGN/Rugcheck/Helius) adalah "confidence booster"
#   atau "danger flag". Chart yang memutuskan. Token dengan chart kuat
#   tetap dipertimbangkan meski data luar kosong.
#
#   CHANGELOG v9.9 (26 Mar 2026):
#   [v9.9-1] ARSITEKTUR: Pattern check SEBELUM API calls external.
#            Jika chart tidak ada pattern → langsung skip tanpa
#            membuang waktu call GMGN/Rugcheck/Helius.
#   [v9.9-2] HARD REJECT: hanya untuk bahaya konkrit. Dihapus:
#            - T1/T2/T3 wajib GMGN (data absen ≠ token bahaya)
#            - T0 Rugcheck ≥ 60 (skor rendah ≠ token jelek)
#   [v9.9-3] Hub & Spoke: T0 = -5 score (bukan reject). Token
#            ultra-early wajar belum punya distribusi merata.
#            T1/T2/T3 = tetap hard reject.
#   [v9.9-4] Named risks hard reject: "copycat", "freeze authority",
#            "honeypot" dalam daftar risks Rugcheck = reject langsung.
#   [v9.9-5] Top10 > 70% = hard reject. Di atas ini 10 wallet
#            memegang hampir seluruh supply = ekstrem berbahaya.
#   [v9.9-6] Safety scoring netral saat data kosong (0, bukan penalti).
#            GMGN tidak ada = pattern score saja yang menentukan.
#   [v9.9-7] Sniper count dikembalikan dari v8.0 — sinyal manipulasi
#            awal yang relevan.
#   [v9.9-8] Safety score bar visual (0–100) dikembalikan dari v9.0.
#   [v9.9-9] Dual alert: token yang sama dalam 15 menit berlabel
#            🔄 UPDATE bukan alert baru.
#   [CARRIED] Semua fix v9.6–v9.8 tetap aktif.
#
#   CHANGELOG v9.9.1 (28 Mar 2026):
#   [v9.9.1-F1] SPIKE TOO LATE: m5 > 150% → skip langsung.
#               Evidence: pisscoin +262%→1x, Dojo +323%→2x.
#               Win rate 0% di atas threshold ini (3 kasus).
#   [v9.9.1-F2] ALREADY PEAKED: h1 < -30% + Hold C1 TIDAK → skip.
#               Evidence: USD, GAMEOVER, Mythos semua <1.2x.
#               Konfirmasi distribusi aktif sebelum alert masuk.
#   [v9.9.1-F3] MCAP LATE ENTRY: MCAP > threshold tier → skip.
#               T0 max $85K, dengan pengurangan untuk token muda.
#               Token age N/A: ×0.4 | <0.5h: ×0.3 | <1h: ×0.5
#               Evidence: 5/5 token Batch 4 loss karena late MCAP.
#               Filter dipasang SEBELUM API calls → hemat resources.
#
#   CHANGELOG v9.9.2 (29 Mar 2026):
#   [v9.9.2-F4] PRICE POSITION AWARENESS: filter baru setelah API
#               calls. Hitung posisi harga dalam range h1 (high/low).
#               Jika harga sudah di >85% puncak range → SKIP.
#               Evidence: bot reaktif tidak bisa beda spike vs akumulasi.
#   [v9.9.2-PM] PUMP MEMORY WATCHLIST: bot catat token yang pernah
#               pump >150% MCAP. Setelah dump >65% dari peak, token
#               masuk watchlist. Alert khusus saat buyer_ratio >10x
#               + price_position rendah = akumulasi terdeteksi.
#   [v9.9.2-FX1] F3 threshold T0: $85K → $80K.
#               Evidence: MCAP $80-85K win rate hanya 11% dari dataset.
#   [v9.9.2-FX2] Age resolution: resolve age SEBELUM F3 menggunakan
#               min(GMGN age, DexScreener fallback) untuk konsistensi.
#               Fix bug BBQ (age 0.6h tidak tertangkap F3).
#   [v9.9.2-FX3] Hold C1: skip check jika token age < 1h.
#               Evidence: WON (0.5h, Hold C1 TIDAK) → 3.8x.
#               Display holds_c1 juga neutral (True) untuk age < 1h.
#   [v9.9.2-FIX1] Pump Memory: update_pump_memory SETELAH hard reject.
#               Token honeypot tidak masuk watchlist bounce.
#   [v9.9.2-FIX2] Single call check_watchlist_bounce (eliminasi
#               double call yang berpotensi state race condition).
#   [v9.9.2-FIX3] age_h_fallback di-clamp >= 0 (guard timestamp
#               masa depan dari API yang bermasalah).
# ══════════════════════════════════════════════════════════

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
HELIUS_KEY     = os.environ.get("HELIUS_KEY",     "")
LUNARCRUSH_KEY = os.environ.get("LUNARCRUSH_KEY", "")

# ── PERSISTENT STATE ─────────────────────────────────────
STATE_FILE            = Path("bot_state.json")
PRICE_TRACKER_MAX_AGE = 7 * 86400

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                print(f"[STATE] Loaded: {len(data.get('price_tracker',{}))} C1, "
                      f"{len(data.get('alerted_tokens',{}))} alerts")
                return data
        except Exception as e:
            print(f"[STATE ERR] {e} — fresh start")
    return {"price_tracker": {}, "alerted_tokens": {}}

def save_state():
    now = time.time()
    clean_prices = {
        k: v for k, v in price_tracker.items()
        if isinstance(v, dict) and now - v.get("ts", 0) < PRICE_TRACKER_MAX_AGE
        or not isinstance(v, dict)
    }
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({
                "price_tracker":  clean_prices,
                "alerted_tokens": {k: v for k, v in alerted_tokens.items()
                                   if now - v < 86400},
                "pump_memory":    {k: v for k, v in pump_memory.items()
                                   if now - v.get("peak_ts", 0) < 7 * 86400}
            }, f)
    except Exception as e:
        print(f"[STATE ERR] save: {e}")

_state         = load_state()
alerted_tokens = _state.get("alerted_tokens", {})
price_tracker  = _state.get("price_tracker", {})
pump_memory    = _state.get("pump_memory", {})   # [v9.9.2-PM]

# ── TIER PARAMETERS ─────────────────────────────────────
T0_MIN_MCAP      = 25_000
T0_MAX_MCAP      = 100_000
T0_MIN_LIQUIDITY = 8_000
T0_MIN_VOL_1H    = 5_000
T0_MAX_POSITION  = 0.02

T1_MIN_MCAP      = 100_000
T1_MAX_MCAP      = 300_000
T1_MIN_LIQUIDITY = 20_000
T1_MIN_VOL_1H    = 15_000
T1_MAX_POSITION  = 0.05

T2_MIN_MCAP      = 300_000
T2_MAX_MCAP      = 1_000_000
T2_MIN_LIQUIDITY = 50_000
T2_MIN_VOL_1H    = 50_000
T2_MAX_POSITION  = 0.1

T3_MIN_MCAP      = 1_000_000
T3_MAX_MCAP      = 50_000_000
T3_MIN_LIQUIDITY = 80_000
T3_MIN_VOL_1H    = 80_000
T3_MAX_POSITION  = 0.2

# ── PATTERN PARAMETERS ──────────────────────────────────
MIN_PUMP_PCT    = 150
T0_MIN_PUMP_PCT = 30
MIN_DIP_PCT     = 20
MAX_DIP_PCT     = 70
MIN_M5_SIGNAL   = 3.0
MAX_CANDLE_DROP = 25

# T0 enhancement
LIQ_MCAP_RATIO_MIN  = 0.15
LIQ_MCAP_RATIO_GOOD = 0.30
VOL_SPIKE_EXTREME   = 10.0
VOL_SPIKE_STRONG    = 5.0
TX_ACCEL_STRONG     = 3.0
TX_ACCEL_EXTREME    = 6.0

# ── SAFETY PARAMETERS ───────────────────────────────────
MIN_LP_BURNED_T1        = 50.0
MIN_LP_BURNED_T2        = 20.0
MAX_BUNDLE_PCT          = 10.0
MAX_DEV_BURNED_PCT_WARN = 20.0
MAX_TOP10_PCT           = 30.0
HARD_REJECT_TOP10       = 70.0   # [v9.9-5] > 70% = reject
MIN_TOKEN_AGE_T0        = 0.1
MIN_TOKEN_AGE_T1        = 0.5
MIN_TOKEN_AGE_T2        = 0.5
MIN_HOLDERS_T0          = 20
MIN_HOLDERS_T1          = 50
MIN_HOLDERS_T2          = 50
HELIUS_MAX_TOP10        = 30.0

RUGCHECK_MAX_RISKS         = 2
RUGCHECK_HARD_REJECT_RISKS = 5
# [v9.9-4] Nama risk dari Rugcheck yang langsung hard reject
RUGCHECK_DANGEROUS_RISKS   = {"copycat", "honeypot", "freeze", "blacklist", "rugpull"}

SCAN_INTERVAL_SEC       = 30
ALERT_COOLDOWN_SEC      = 300
UPDATE_LABEL_WINDOW_SEC = 900  # [v9.9-9] 15 menit = label UPDATE

# ── [v9.9.2] PRICE POSITION & PUMP MEMORY CONSTANTS ─────
# [F4] Price Position Awareness
F4_PEAK_POSITION        = 0.85   # Harga di >85% dari range h1 → SKIP
F4_BASE_POSITION_BONUS  = 0.30   # Harga di <30% dari range → bonus score +3

# [PM] Pump Memory Watchlist — v9.9.3 constants
PM_PUMP_THRESHOLD       = 150.0  # h6 atau h1 > 150% → catat sebagai pump besar
PM_BUYER_RATIO_MIN      = 10.0   # buyer_ratio_m5 minimum untuk trigger watchlist alert
PM_POSITION_MAX         = 0.25   # price_position harus < 25% dari range (di dasar)
PM_WATCHLIST_COOLDOWN   = 3600   # 1 jam cooldown antar watchlist alert

# [v9.9.3-BF] Birth Floor constants
PM_BIRTH_FLOOR_RATIO_MIN = 1.00  # MCAP minimal 1.0x dari birth floor (tidak di bawah)
PM_BIRTH_FLOOR_RATIO_MAX = 1.80  # MCAP maksimal 1.8x dari birth floor (masih dekat)
PM_BIRTH_PEAK_MIN_RATIO  = 3.0   # Peak minimal 3x dari birth floor (pump signifikan)
PM_FLOOR_UNCERTAIN_AGE   = 6.0   # Token > 6h saat pertama terdeteksi → floor tidak reliable

# [v9.9.3-HD] Healthy Dump constants
PM_HD_H1_H6_MAX_RATIO    = 0.70  # h1_magnitude max 70% dari h6_magnitude
PM_HD_M5_MAX_DUMP        = -15.0 # m5 tidak boleh < -15% (dump masih aktif)
PM_HD_MIN_SCAN_COUNT     = 6     # Minimal 6 scan bertahap (3 menit) untuk dump sehat

# ── CACHE ────────────────────────────────────────────────
gmgn_cache     = {}
helius_cache   = {}
rugcheck_cache = {}
social_cache   = {}
CACHE_SEC      = 300

# ── TELEGRAM ─────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message,
                "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

# ── API HELPERS ───────────────────────────────────────────
def api_get(url: str, headers: dict = None, params: dict = None,
            timeout: int = 10, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API GET] Rate limit, retry {wait}s")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.Timeout:
            print(f"[API GET] Timeout {attempt+1}/{retries+1}")
            if attempt < retries: time.sleep(1)
        except Exception as e:
            print(f"[API GET ERR] {e}"); break
    return None

def api_post(url: str, json_body: dict = None,
             timeout: int = 15, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=json_body, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API POST] Rate limit, retry {wait}s")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.Timeout:
            print(f"[API POST] Timeout {attempt+1}/{retries+1}")
            if attempt < retries: time.sleep(1)
        except Exception as e:
            print(f"[API POST ERR] {e}"); break
    return None

# ── DEX SCREENER ─────────────────────────────────────────
def get_trending_tokens() -> list:
    try:
        r = api_get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=15)
        if not r or r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:50]
    except: return []

def get_new_tokens() -> list:
    try:
        r = api_get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        if not r or r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:30]
    except: return []

def get_pair(token_address: str) -> dict | None:
    try:
        r = api_get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                    timeout=15)
        if not r or r.status_code != 200: return None
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs,
                      key=lambda x: x.get("volume", {}).get("h24", 0),
                      reverse=True)[0]
    except: return None

# ── GMGN ─────────────────────────────────────────────────
def get_gmgn(token_address: str) -> dict:
    ck = f"gmgn_{token_address}"
    if ck in gmgn_cache:
        ct, cd = gmgn_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd
    result = {"available": False}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 10)",
                   "Accept": "application/json", "Referer": "https://gmgn.ai/"}
        r = api_get(f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{token_address}",
                    headers=headers, timeout=10)
        if r and r.status_code == 200:
            token = r.json().get("data", {}).get("token", {})
            if token:
                created_at = token.get("open_timestamp", 0)
                age_hours  = (time.time() - created_at) / 3600 if created_at else 0

                raw_lp  = float(token.get("burn_ratio", 0) or 0) * 100
                raw_bnd = float(token.get("bundle_pct", 0) or 0) * 100
                raw_dev = float(token.get("dev_token_burn_ratio", 0) or 0) * 100

                # Guard nilai melebihi 100% — deteksi perubahan API
                if raw_lp > 100 or raw_bnd > 100 or raw_dev > 100:
                    print(f"[GMGN WARN] {token_address[:8]} nilai >100% — "
                          f"LP:{raw_lp:.0f}% Bnd:{raw_bnd:.0f}% Dev:{raw_dev:.0f}%")

                result = {
                    "available":     True,
                    "age_hours":     round(age_hours, 2),
                    "holders":       token.get("holder_count", 0) or 0,
                    "lp_burned":     min(raw_lp, 100.0),
                    "bundle_pct":    min(raw_bnd, 100.0),
                    "dev_burned_pct":min(raw_dev, 100.0),
                    # [v9.9-7] sniper_count dikembalikan dari v8.0
                    "sniper_count":  token.get("sniper_count", 0) or 0,
                    "is_honeypot":   token.get("is_honeypot", False),
                    "rug_ratio":     float(token.get("rug_ratio", 0) or 0),
                    "smart_buy":     token.get("smart_buy_24h", 0) or 0,
                    "smart_sell":    token.get("smart_sell_24h", 0) or 0,
                }
                print(f"[GMGN] {token_address[:8]} Age:{age_hours:.1f}h "
                      f"LP:{result['lp_burned']:.0f}% Bnd:{result['bundle_pct']:.1f}% "
                      f"Dev:{result['dev_burned_pct']:.0f}% Sniper:{result['sniper_count']}")
    except Exception as e:
        print(f"[GMGN ERR] {e}")
    gmgn_cache[ck] = (time.time(), result)
    return result

# ── RUGCHECK ─────────────────────────────────────────────
def get_rugcheck(token_address: str) -> dict:
    ck = f"rugcheck_{token_address}"
    if ck in rugcheck_cache:
        ct, cd = rugcheck_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd
    result = {"available": False}
    try:
        r = api_get(f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report/summary",
                    timeout=10, headers={"Accept": "application/json"})
        if r and r.status_code == 200:
            data       = r.json()
            risks      = data.get("risks", []) or []
            risk_names = [risk.get("name", "") for risk in risks]

            raw_score = float(data.get("score", 0) or 0)
            if raw_score > 100:
                print(f"[RUGCHECK WARN] {token_address[:8]} score {raw_score:.0f} → ÷10")
                raw_score = raw_score / 10
            risk_level = min(int(raw_score), 100)

            markets    = data.get("markets", []) or []
            lp_locked  = False
            lp_burned  = False
            for m in markets:
                lp_info = m.get("lp", {}) or {}
                if lp_info.get("lpLockedPct", 0) > 0:  lp_locked = True
                if lp_info.get("lpBurnedPct", 0) > 80: lp_burned = True

            token_meta  = data.get("token", {}) or {}
            mint_auth   = token_meta.get("mintAuthority")  is not None
            freeze_auth = token_meta.get("freezeAuthority") is not None

            top_holders = data.get("topHolders", []) or []
            raw_pcts    = [float(h.get("pct", 0) or 0) for h in top_holders[:10]]
            top10_sum   = (sum(raw_pcts) if raw_pcts and max(raw_pcts) > 1.0
                          else sum(raw_pcts) * 100)

            result = {
                "available":  True,
                "score":      risk_level,
                "risks":      risk_names[:5],
                "risk_names_lower": [r.lower() for r in risk_names],
                "risk_count": len(risks),
                "lp_locked":  lp_locked,
                "lp_burned":  lp_burned,
                "mint_auth":  mint_auth,
                "freeze_auth":freeze_auth,
                "top10_pct":  round(top10_sum, 2),
            }
            print(f"[RUGCHECK] {token_address[:8]} Score:{risk_level} "
                  f"LP_burned:{lp_burned} Mint:{mint_auth} Risks:{len(risks)}")
    except Exception as e:
        print(f"[RUGCHECK ERR] {e}")
    rugcheck_cache[ck] = (time.time(), result)
    return result

# ── HELIUS ───────────────────────────────────────────────
def get_helius(token_address: str) -> dict:
    ck = f"helius_{token_address}"
    if ck in helius_cache:
        ct, cd = helius_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd
    result = {"available": False}
    if not HELIUS_KEY: return result
    try:
        url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
        r   = api_post(url, json_body={"jsonrpc": "2.0", "id": 1,
                       "method": "getTokenLargestAccounts",
                       "params": [token_address]})
        if not r or r.status_code != 200: return result
        accounts = r.json().get("result", {}).get("value", [])
        if not accounts: return result

        r2    = api_post(url, json_body={"jsonrpc": "2.0", "id": 2,
                         "method": "getTokenSupply",
                         "params": [token_address]})
        total = float(r2.json().get("result", {}).get("value", {})
                      .get("uiAmount", 0) or 0) if r2 and r2.status_code == 200 else 0
        if total <= 0: return result

        balances  = [float(a.get("uiAmount", 0) or 0) for a in accounts
                     if float(a.get("uiAmount", 0) or 0) > 0]
        top10_pct = (sum(balances[:10]) / total * 100
                     if len(balances) >= 10 else sum(balances) / total * 100)
        hub_spoke = (len(balances) >= 2 and balances[1] > 0
                     and balances[0] / balances[1] > 5)

        result = {
            "available": True,
            "top10_pct": round(top10_pct, 2),
            "top1_pct":  round(balances[0] / total * 100, 2) if balances else 0,
            "hub_spoke": hub_spoke,
        }
        print(f"[HELIUS] {token_address[:8]} Top10:{top10_pct:.1f}% Hub:{hub_spoke}")
    except Exception as e:
        print(f"[HELIUS ERR] {e}")
    helius_cache[ck] = (time.time(), result)
    return result

# ── LUNARCRUSH ───────────────────────────────────────────
def get_social(symbol: str, token_address: str = "") -> dict:
    ck = f"lunar_{token_address if token_address else symbol.lower()}"
    if ck in social_cache:
        ct, cd = social_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd
    result = {"available": False}
    if not LUNARCRUSH_KEY:
        social_cache[ck] = (time.time(), result)
        return result
    try:
        r = api_get("https://lunarcrush.com/api4/public/coins/list/v2",
                    headers={"Authorization": f"Bearer {LUNARCRUSH_KEY}"},
                    params={"sort": "galaxy_score", "limit": 5, "search": symbol},
                    timeout=10)
        if r and r.status_code == 200:
            coins = r.json().get("data", [])
            if coins:
                coin  = next((c for c in coins
                              if c.get("symbol", "").upper() == symbol.upper()), coins[0])
                v24h  = coin.get("social_volume_24h", 0) or 0
                vprev = coin.get("social_volume_prev", v24h) or v24h
                trend = ((v24h - vprev) / vprev * 100) if vprev > 0 else 0
                result = {
                    "available":     True,
                    "galaxy_score":  round(coin.get("galaxy_score", 0) or 0, 1),
                    "alt_rank":      coin.get("alt_rank", 0) or 0,
                    "mention_trend": round(trend, 1),
                    "kol_active":    (coin.get("interactions_24h", 0) or 0) > 10000,
                }
    except: pass
    social_cache[ck] = (time.time(), result)
    return result

# ── C1 & HIGH TRACKING ────────────────────────────────────
def get_open_c1(token_address: str, price: float) -> float:
    entry = price_tracker.get(token_address)
    if entry is None:
        price_tracker[token_address] = {"price": price, "high": price, "ts": time.time()}
        print(f"[C1] {token_address[:8]} = ${price:.8f}")
    elif isinstance(entry, dict):
        if price > entry.get("high", 0):
            price_tracker[token_address]["high"] = price
    else:
        price_tracker[token_address] = {
            "price": float(entry), "high": max(float(entry), price), "ts": time.time()}
    entry = price_tracker[token_address]
    return entry["price"] if isinstance(entry, dict) else float(entry)

def get_tracked_high(token_address: str) -> float:
    entry = price_tracker.get(token_address)
    if entry is None:    return 0.0
    if isinstance(entry, dict): return entry.get("high", 0.0)
    return float(entry)

def calc_dip_from_high(price: float, tracked_high: float,
                       change_h1: float, change_m5: float) -> float:
    if tracked_high > 0 and price < tracked_high:
        return ((tracked_high - price) / tracked_high) * 100
    if change_h1 < 0:
        est_high = price / (1 + change_h1 / 100)
        return ((est_high - price) / est_high) * 100
    if change_m5 < 0:
        return abs(change_m5)
    return 0.0

# ── [v9.9-1/2] PATTERN CHECK — PRIMER ────────────────────
# Dipanggil SEBELUM API calls external. Jika tidak ada pattern,
# tidak perlu buang waktu call GMGN/Rugcheck/Helius.
def check_pattern_exists(tier: str, m5: float, h1: float, h6: float,
                         h24: float, vm5: float, vh1: float) -> bool:
    """Gate cepat: apakah token layak diproses lebih lanjut?"""
    if tier == "T0":
        avg5m         = vh1 / 12 if vh1 > 0 else 0
        has_vol_spike = avg5m > 0 and vm5 / avg5m >= 2.0
        has_momentum  = h6 >= T0_MIN_PUMP_PCT or h1 >= 15 or has_vol_spike
        if not has_momentum:          return False
        if m5 < -MAX_CANDLE_DROP:     return False
        return True
    else:
        pump = h24 if h24 >= MIN_PUMP_PCT else (h6 if h6 >= MIN_PUMP_PCT else 0)
        if pump < MIN_PUMP_PCT:       return False
        has_signal = (h1 < -10) or (m5 < -5) or (h6 > 20 and m5 >= 0)
        if not has_signal:            return False
        if m5 < -MAX_CANDLE_DROP:     return False
        avg5m = vh1 / 12 if vh1 > 0 else 0
        if avg5m > 0 and vm5 / avg5m < 0.05: return False
        return True

# ── [v9.9-2/4/5] HARD REJECT — BAHAYA KONKRIT SAJA ───────
def check_hard_reject(gmgn: dict, rugcheck: dict,
                      helius: dict, tier: str) -> tuple:
    """
    Returns (rejected: bool, reason: str)
    Hanya menolak jika ada bukti nyata bahaya.
    Data tidak tersedia BUKAN alasan reject.
    """
    # Rugcheck: bahaya terbukti
    if rugcheck.get("available"):
        if rugcheck.get("mint_auth"):
            return True, "🔴 MINT AUTHORITY — dev bisa cetak token!"
        if rugcheck.get("freeze_auth"):
            return True, "🔴 FREEZE AUTHORITY — dev bisa freeze wallet!"
        if rugcheck.get("risk_count", 0) > RUGCHECK_HARD_REJECT_RISKS:
            return True, f"🔴 Rugcheck: {rugcheck['risk_count']} risiko!"
        # [v9.9-4] Named risks berbahaya
        risk_lower = rugcheck.get("risk_names_lower", [])
        for dangerous in RUGCHECK_DANGEROUS_RISKS:
            for r in risk_lower:
                if dangerous in r:
                    return True, f"🔴 Rugcheck: '{r}' terdeteksi!"

    # GMGN: bahaya terbukti
    if gmgn.get("is_honeypot"):
        return True, "🔴 HONEYPOT!"
    if gmgn.get("rug_ratio", 0) > 0.8:
        return True, "🔴 RUG RATIO TINGGI!"

    # Helius: konsentrasi ekstrem
    if helius.get("available"):
        top10 = helius.get("top10_pct", 0)
        # [v9.9-5] Top10 > 70% = hard reject
        if top10 > HARD_REJECT_TOP10:
            return True, f"🔴 Top 10 ekstrem: {top10:.0f}% — 10 wallet kuasai supply!"
        # [v9.9-3] Hub & Spoke: T1+ = reject, T0 = penalti score saja
        if helius.get("hub_spoke") and tier in ["T1", "T2", "T3"]:
            return True, "🔴 Hub & Spoke pada token established!"

    # Per-tier minimum dari GMGN (hanya jika tersedia)
    if gmgn.get("available"):
        age = gmgn.get("age_hours", 0)
        holders = gmgn.get("holders", 0)
        lp = gmgn.get("lp_burned", 0)
        if tier == "T0":
            if age < MIN_TOKEN_AGE_T0:  return True, ""
            if holders < MIN_HOLDERS_T0: return True, ""
        elif tier == "T1":
            if age < MIN_TOKEN_AGE_T1:   return True, ""
            if lp < MIN_LP_BURNED_T1:    return True, ""
            if holders < MIN_HOLDERS_T1: return True, ""
        elif tier == "T2":
            if age < MIN_TOKEN_AGE_T2:   return True, ""
            if lp < MIN_LP_BURNED_T2:    return True, ""
            if holders < MIN_HOLDERS_T2: return True, ""

    return False, ""

# ── SAFETY SCORING — SEKUNDER ────────────────────────────
def score_safety(gmgn: dict, rugcheck: dict, helius: dict,
                 tier: str) -> tuple:
    """
    [v9.9-6] Data tidak ada = 0 (netral). Bukan penalti.
    Safety hanya menambah atau mengurangi keyakinan.
    Returns: (score, signals, warnings, safety_pct)
    """
    score    = 0
    signals  = []
    warnings = []

    # ── GMGN scoring ─────────────────────────────────────
    if gmgn.get("available"):
        lp  = gmgn.get("lp_burned", 0)
        bnd = gmgn.get("bundle_pct", 0)
        dev = gmgn.get("dev_burned_pct", None)
        holders = gmgn.get("holders", 0)
        sniper  = gmgn.get("sniper_count", 0)
        sm_buy  = gmgn.get("smart_buy", 0)
        sm_sell = gmgn.get("smart_sell", 0)

        # LP Burned
        if lp >= 100:
            score += 8; signals.append("🔒 LP Burned 100% — tidak bisa rug!")
        elif lp >= 80:
            score += 6; signals.append(f"🔒 LP Burned {lp:.0f}%")
        elif lp >= 50:
            score += 3; signals.append(f"🔒 LP Burned {lp:.0f}%")
        elif lp > 0:
            score += 1; warnings.append(f"⚠️ LP Burned rendah: {lp:.0f}%")
        else:
            if tier not in ["T0", "T1"]:
                score -= 3; warnings.append("🔴 LP tidak diburn!")
            else:
                warnings.append("⚠️ LP belum diburn (wajar token sangat baru)")

        # Bundle
        if bnd <= 2:
            score += 5; signals.append(f"✅ Bundle {bnd:.1f}% — sangat bersih!")
        elif bnd <= MAX_BUNDLE_PCT:
            score += 3; signals.append(f"✅ Bundle {bnd:.1f}%")
        elif bnd <= 20:
            score -= 1; warnings.append(f"⚠️ Bundle {bnd:.1f}%")
        else:
            score -= 4; warnings.append(f"🔴 Bundle tinggi {bnd:.1f}%!")

        # Dev Burned (tinggi = bagus)
        if dev is not None:
            if dev >= 80:
                score += 4; signals.append(f"✅ Dev burned {dev:.0f}% — komitmen!")
            elif dev >= 50:
                score += 2; signals.append(f"✅ Dev burned {dev:.0f}%")
            elif dev >= MAX_DEV_BURNED_PCT_WARN:
                score += 1
            elif dev > 0:
                warnings.append(f"⚠️ Dev burned rendah: {dev:.0f}%")
            else:
                if tier not in ["T0", "T1"]:
                    score -= 2; warnings.append("🔴 Dev belum burn token!")
                else:
                    warnings.append("⚠️ Dev belum burn (umum token baru)")

        # Holders
        if holders >= 2000:
            score += 3; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 500:
            score += 2; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 100:
            score += 1

        # [v9.9-7] Sniper count
        if sniper == 0:
            score += 2; signals.append("✅ Tidak ada sniper")
        elif sniper <= 3:
            score += 1
        elif sniper <= 10:
            warnings.append(f"⚠️ {sniper} sniper terdeteksi")
        else:
            score -= 2; warnings.append(f"🔴 Banyak sniper: {sniper}!")

        # Smart Money
        sm = sm_buy - sm_sell
        if sm > 5:
            score += 2; signals.append(f"✅ Smart money NET BUY +{sm}")
        elif sm < -5:
            score -= 1; warnings.append(f"⚠️ Smart money NET SELL {sm}")

    # ── Rugcheck scoring ──────────────────────────────────
    if rugcheck.get("available"):
        rc = rugcheck.get("score", 0)
        rc_risks = rugcheck.get("risk_count", 0)
        if rc >= 90:
            score += 4; signals.append(f"✅ Rugcheck {rc}/100 — AMAN!")
        elif rc >= 70:
            score += 3; signals.append(f"✅ Rugcheck {rc}/100")
        elif rc >= 50:
            score += 1; signals.append(f"⚠️ Rugcheck {rc}/100")
        else:
            score -= 2; warnings.append(f"🔴 Rugcheck rendah: {rc}/100!")
        if rc_risks == 0:
            score += 2; signals.append("✅ Rugcheck: 0 risiko!")
        elif rc_risks <= RUGCHECK_MAX_RISKS:
            score += 1
        else:
            warnings.append(f"⚠️ Rugcheck: {rc_risks} risiko: "
                           f"{', '.join(rugcheck.get('risks', [])[:2])}")

    # ── Helius scoring ────────────────────────────────────
    if helius.get("available"):
        top10 = helius.get("top10_pct", 0)
        hub   = helius.get("hub_spoke", False)

        # [v9.9-3] Hub & Spoke T0 = penalti score, bukan reject
        if hub and tier == "T0":
            score -= 5; warnings.append("🔴 Hub & Spoke (T0 — token sangat baru)")
        elif top10 <= 10:
            score += 3; signals.append(f"✅ Top 10: {top10:.1f}% — sempurna!")
        elif top10 <= MAX_TOP10_PCT:
            score += 2; signals.append(f"✅ Top 10: {top10:.1f}%")
        elif top10 <= 40:
            score -= 1; warnings.append(f"⚠️ Top 10 agak tinggi: {top10:.1f}%")
        elif top10 <= HARD_REJECT_TOP10:
            score -= 2; warnings.append(f"🔴 Top 10 tinggi: {top10:.1f}%!")

    # Hitung safety_pct (0–100) untuk display bar
    # Basis: score bisa dari sekitar -15 sampai +35
    # Map ke 0–100 dengan clamp
    safety_pct = max(0, min(100, int((score + 15) / 50 * 100)))

    return score, signals, warnings, safety_pct

# ── PRE-PUMP PATTERN SCORING (T0) ─────────────────────────
def score_prepump(price: float, open_c1: float,
                  m5: float, h1: float, h6: float,
                  vm5: float, vh1: float,
                  bm5: int, sm5: int, bh1: int, sh1: int,
                  age_h: float, liq: float, mcap: float) -> tuple:
    score = 0; signals = []; warnings = []
    r_m5  = bm5 / max(sm5, 1)
    r_h1  = bh1 / max(sh1, 1)
    avg5m = vh1 / 12 if vh1 > 0 else 0

    # Volume spike
    if avg5m > 0:
        vr = vm5 / avg5m
        if vr >= VOL_SPIKE_EXTREME:
            score += 8; signals.append(f"🚨 Volume EXTREME {vr:.1f}x!")
        elif vr >= VOL_SPIKE_STRONG:
            score += 6; signals.append(f"🔥 Volume spike {vr:.1f}x")
        elif vr >= 3.0:
            score += 4; signals.append(f"📊 Volume naik {vr:.1f}x")
        elif vr >= 2.0:
            score += 2; signals.append(f"📊 Volume naik {vr:.1f}x")
        elif vr < 0.5:
            warnings.append("⚠️ Volume sepi")

    # TX acceleration
    tx_m5  = bm5 + sm5
    avg_tx = (bh1 + sh1) / 12 if (bh1 + sh1) > 0 else 0
    if avg_tx > 0:
        ta = tx_m5 / avg_tx
        if ta >= TX_ACCEL_EXTREME:
            score += 5; signals.append(f"🌊 TX acceleration {ta:.1f}x!")
        elif ta >= TX_ACCEL_STRONG:
            score += 3; signals.append(f"📈 TX naik {ta:.1f}x")
        elif ta >= 1.5:
            score += 1

    # Pump awal
    if h6 >= T0_MIN_PUMP_PCT:
        score += 3; signals.append(f"📈 Pump awal h6: +{h6:.0f}%")
    if h1 >= 20:
        score += 2; signals.append(f"📈 h1 kuat: +{h1:.0f}%")
    if m5 >= 5:
        score += 2; signals.append(f"🚀 m5 hijau: +{m5:.1f}%")
    elif m5 < -MAX_CANDLE_DROP:
        return 0, [], ["🔴 Dump masif m5!"], 0.0

    # Buyer dominan
    if r_m5 >= 3.0:
        score += 4; signals.append(f"✅ Buyer sangat dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 2.0:
        score += 3; signals.append(f"✅ Buyer dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 1.5:
        score += 1; signals.append(f"✅ Buyer m5: {r_m5:.1f}x")
    else:
        warnings.append(f"⚠️ Buyer ratio rendah: {r_m5:.1f}x")
    if r_h1 >= 1.5:
        score += 2; signals.append(f"✅ Buyer dominan h1: {r_h1:.1f}x")

    # Token fresh
    if 0.1 <= age_h <= 1.0:
        score += 3; signals.append(f"🆕 Token sangat fresh: {age_h:.1f}h!")
    elif age_h <= 3.0:
        score += 1; signals.append(f"🆕 Token fresh: {age_h:.1f}h")

    # C1 — toleransi 10% untuk konsistensi dengan holds_c1
    # [v9.9.2-FX3] Skip Hold C1 check jika token < 1h
    # Evidence: WON (0.5h, Hold C1 TIDAK) → 3.8x. C1 belum establish di token sangat fresh.
    if age_h < 1.0:
        pass  # tidak cek C1, token terlalu fresh untuk C1 yang valid
    elif open_c1 > 0 and price >= open_c1 * 0.9:
        above_pct = ((price - open_c1) / open_c1 * 100)
        score += 2; signals.append(f"✅ Harga di atas C1 (+{above_pct:.0f}%)")
    elif open_c1 > 0:
        warnings.append("⚠️ Harga di bawah C1")

    # Liq/MCAP ratio
    if mcap > 0 and liq > 0:
        lr = liq / mcap
        if lr >= LIQ_MCAP_RATIO_GOOD:
            score += 3; signals.append(f"💧 Pool dalam: {lr:.0%}")
        elif lr >= LIQ_MCAP_RATIO_MIN:
            score += 1; signals.append(f"💧 Pool cukup: {lr:.0%}")

    return score, signals, warnings, 0.0

# ── DIP & RIP PATTERN SCORING (T1/T2/T3) ─────────────────
def score_core_pattern(price: float, open_c1: float,
                       m5: float, h1: float, h6: float, h24: float,
                       vm5: float, vh1: float, vh6: float,
                       bm5: int, sm5: int, bh1: int, sh1: int,
                       tracked_high: float = 0) -> tuple:
    score = 0; signals = []; warnings = []
    r_m5 = bm5 / max(sm5, 1)
    r_h1 = bh1 / max(sh1, 1)

    # Pump awal
    pump = h24 if h24 >= MIN_PUMP_PCT else (h6 if h6 >= MIN_PUMP_PCT else 0)
    if pump >= 500:
        score += 4; signals.append(f"🚀 Pump sangat kuat: +{pump:.0f}%")
    elif pump >= 200:
        score += 3; signals.append(f"🚀 Pump kuat: +{pump:.0f}%")
    elif pump >= 150:
        score += 2; signals.append(f"📈 Pump: +{pump:.0f}%")
    else:
        return 0, [], [], 0

    # Dip dari high
    dip = calc_dip_from_high(price, tracked_high, h1, m5)
    if dip > MAX_DIP_PCT:
        warnings.append(f"🔴 Dip terlalu dalam: -{dip:.0f}%")
        return score, signals, warnings, dip
    elif dip >= 35:
        score += 3; signals.append(f"✅ Dip ideal: -{dip:.0f}%")
    elif dip >= MIN_DIP_PCT:
        score += 2; signals.append(f"✅ Dip cukup: -{dip:.0f}%")
    elif h1 > 0:
        score += 1
    else:
        warnings.append(f"⚠️ Dip dangkal: -{dip:.0f}%")

    # C1 hold — konsisten di 0.9x threshold
    if open_c1 > 0:
        if price >= open_c1 * 0.9:
            score += 3; signals.append(f"✅ Hold C1 (+{((price-open_c1)/open_c1*100):.0f}%)")
        else:
            warnings.append("🔴 Harga tembus Open C1!"); score -= 2

    # Volume staircase
    avg5m = vh1 / 12 if vh1 > 0 else 0
    avg_h6 = vh6 / 6 if vh6 > 0 else 0
    if avg5m > 0:
        vr = vm5 / avg5m
        if vr >= 2.0:
            score += 3; signals.append(f"📊 Volume naik {vr:.1f}x!")
        elif vr >= 1.5:
            score += 2; signals.append(f"📊 Volume naik {vr:.1f}x")
        elif vr >= 1.0:
            score += 1
        elif vr < 0.3:
            warnings.append("⚠️ Volume sangat sepi")
    if avg_h6 > 0 and vh1 / avg_h6 >= 1.5:
        score += 2; signals.append(f"📈 Volume momentum h1/h6: {vh1/avg_h6:.1f}x")

    # Konsolidasi + entry
    is_consol = -20 <= h1 <= 20
    if is_consol:
        if avg5m > 0 and vm5 / avg5m < 0.7:
            score += 2; signals.append("🔄 Konsolidasi sehat")
        if m5 >= 10:
            score += 5; signals.append(f"🚀 BREAKOUT konsolidasi! m5: +{m5:.1f}%")
        elif m5 >= MIN_M5_SIGNAL:
            score += 3; signals.append(f"📈 Entry signal m5: +{m5:.1f}%")
        elif m5 >= 0:
            score += 1; signals.append("⏳ Konsolidasi berlanjut")
        else:
            warnings.append(f"⚠️ m5 masih negatif: {m5:.1f}%")
    else:
        if m5 >= 10:
            score += 4; signals.append(f"✅ Reversal kuat m5: +{m5:.1f}%")
        elif m5 >= MIN_M5_SIGNAL:
            score += 2; signals.append(f"✅ Reversal m5: +{m5:.1f}%")
        elif m5 < -MAX_CANDLE_DROP:
            return score, signals, ["🔴 Dump masif m5!"], dip

    # Buyer ratio
    if r_m5 >= 2.0:
        score += 3; signals.append(f"✅ Buyer dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 1.5:
        score += 2; signals.append(f"✅ Buyer m5: {r_m5:.1f}x")
    elif r_m5 >= 1.0:
        score += 1
    else:
        warnings.append(f"⚠️ Seller dominan m5: {r_m5:.1f}x")
    if r_h1 >= 1.5:
        score += 2; signals.append(f"✅ Buyer dominan h1: {r_h1:.1f}x")
    elif r_h1 >= 1.0:
        score += 1

    return score, signals, warnings, dip

# ── DETERMINE TIER ────────────────────────────────────────
def get_tier(mcap: float, liq: float, vol_h1: float) -> str | None:
    if T0_MIN_MCAP <= mcap <= T0_MAX_MCAP and liq >= T0_MIN_LIQUIDITY and vol_h1 >= T0_MIN_VOL_1H:
        if mcap > 0 and liq / mcap < LIQ_MCAP_RATIO_MIN:
            print(f"[T0 SKIP] liq/mcap={liq/mcap:.2f} < {LIQ_MCAP_RATIO_MIN}")
            return None
        return "T0"
    if T1_MIN_MCAP <= mcap <= T1_MAX_MCAP and liq >= T1_MIN_LIQUIDITY and vol_h1 >= T1_MIN_VOL_1H:
        return "T1"
    if T2_MIN_MCAP <= mcap <= T2_MAX_MCAP and liq >= T2_MIN_LIQUIDITY and vol_h1 >= T2_MIN_VOL_1H:
        return "T2"
    if T3_MIN_MCAP <= mcap <= T3_MAX_MCAP and liq >= T3_MIN_LIQUIDITY and vol_h1 >= T3_MIN_VOL_1H:
        return "T3"
    return None

# ── DYNAMIC SL/TP ─────────────────────────────────────────
def get_sl_tp(price: float, tier: str) -> tuple:
    sl_map  = {"T0": 0.80, "T1": 0.85, "T2": 0.88, "T3": 0.90}
    tp1_map = {"T0": 3.0,  "T1": 2.0,  "T2": 1.8,  "T3": 1.5}
    tp2_map = {"T0": 5.0,  "T1": 3.0,  "T2": 2.5,  "T3": 2.0}
    sl_pct  = sl_map.get(tier, 0.85)
    return (price * sl_pct,
            price * tp1_map.get(tier, 2.0),
            price * tp2_map.get(tier, 3.0),
            int((1 - sl_pct) * 100))

# ── [v9.9.3-PM3] PUMP MEMORY FUNCTIONS v3 ───────────────

def update_pump_memory(addr: str, mcap: float, h6: float, h1: float,
                       age_h: float = 0):
    """
    [v9.9.3] Catat token dengan tracking Birth Floor & Healthy Dump.

    Birth Floor: min_mcap_seen = MCAP terendah yang pernah terlihat.
    Ini adalah proxy dari 'birth candle low'. Semakin awal token
    terdeteksi, semakin akurat nilai ini.

    Dump Tracking: dump_scan_count naik setiap scan saat MCAP turun,
    reset jika rebound > 5%. Minimal 6 scan = dump bertahap (sehat).
    """
    global pump_memory
    now      = time.time()
    existing = pump_memory.get(addr, {})

    # Inisialisasi record baru jika belum ada
    if not existing:
        pump_memory[addr] = {
            "peak_mcap":       mcap,
            "peak_ts":         now,
            "min_mcap_seen":   mcap,   # [BF] birth floor proxy
            "first_seen_age":  age_h,  # [BF] umur token saat pertama terdeteksi
            "first_seen_ts":   now,
            "last_mcap_seen":  mcap,
            "dump_scan_count": 0,      # [HD] counter scan penurunan bertahap
            "status":          "new",
            "last_alert":      0,
        }
        print(f"[PM NEW] {addr[:8]} init MCAP ${mcap:,.0f} age {age_h:.1f}h")
        return

    # Update peak
    if mcap > existing.get("peak_mcap", 0):
        pump_memory[addr]["peak_mcap"] = mcap
        pump_memory[addr]["peak_ts"]   = now
        pump_memory[addr]["dump_scan_count"] = 0  # reset saat buat peak baru
        print(f"[PM PEAK] {addr[:8]} new peak ${mcap:,.0f} (pump max {max(h6,h1):.0f}%)")

    # [BF] Update birth floor — catat MCAP terendah yang pernah terlihat
    if mcap < existing.get("min_mcap_seen", mcap):
        pump_memory[addr]["min_mcap_seen"] = mcap
        print(f"[PM FLOOR] {addr[:8]} new floor ${mcap:,.0f}")

    # [HD] Dump scan tracking — hitung berapa scan harga turun bertahap
    prev_mcap = existing.get("last_mcap_seen", mcap)
    if mcap < prev_mcap * 0.99:        # turun > 1% dari scan sebelumnya
        pump_memory[addr]["dump_scan_count"] = existing.get("dump_scan_count", 0) + 1
    elif mcap > prev_mcap * 1.05:      # rebound > 5% → reset counter
        pump_memory[addr]["dump_scan_count"] = 0
    pump_memory[addr]["last_mcap_seen"] = mcap

    # Tandai sebagai "watching" jika sudah pernah pump besar
    pump_pct = max(h6, h1)
    if pump_pct >= PM_PUMP_THRESHOLD and existing.get("status") == "new":
        pump_memory[addr]["status"] = "watching"
        print(f"[PM WATCH] {addr[:8]} pump {pump_pct:.0f}% → watching")


def is_healthy_dump(h1: float, h6: float, m5: float,
                    dump_scan_count: int) -> tuple[bool, str]:
    """
    [v9.9.3-HD] Cek apakah dump yang terjadi adalah dump SEHAT.

    Dump sehat = penurunan bertahap oleh 3+ candle, bukan 1-2 candle
    merah panjang (yang bisa jadi rug atau manipulation).

    Dua lapis:
    (A) Timeframe proxy: sebagian besar dump harus terjadi SEBELUM 1 jam
        terakhir. Kalau h1_magnitude > 70% dari h6_magnitude, dump
        masih terlalu baru / cepat.
    (B) Scan tracking: bot harus melihat minimal 6 scan penurunan
        bertahap (= 3 menit dengan interval 30 detik).

    Returns: (is_healthy: bool, reason: str)
    """
    # Cek (B) dulu — paling cepat
    if dump_scan_count < PM_HD_MIN_SCAN_COUNT:
        return False, f"dump_scan {dump_scan_count} < {PM_HD_MIN_SCAN_COUNT} (terlalu cepat)"

    # Cek dump masih aktif
    if m5 < PM_HD_M5_MAX_DUMP:
        return False, f"m5 {m5:.1f}% < {PM_HD_M5_MAX_DUMP}% (dump masih aktif)"

    # Cek (A) — h6 harus jauh lebih besar dari h1 (dump sudah lama)
    h6_mag = abs(h6)
    h1_mag = abs(h1)
    if h6_mag > 0 and h1_mag / h6_mag > PM_HD_H1_H6_MAX_RATIO:
        return False, (f"h1/h6 ratio {h1_mag/h6_mag:.0%} > {PM_HD_H1_H6_MAX_RATIO:.0%} "
                       f"(dump terlalu baru)")

    return True, "OK"


def check_watchlist_bounce(addr: str, mcap: float,
                           buyer_ratio_m5: float,
                           price_position: float,
                           h1: float = 0, h6: float = 0,
                           m5: float = 0) -> bool:
    """
    [v9.9.3-PM3] PM Watchlist v3 — Birth Floor + Healthy Dump.

    Kondisi SEMUA harus terpenuhi:
    1. Token pernah pump > 3x dari birth floor (pump signifikan)
    2. Current MCAP dalam range 1.0x–1.8x dari birth floor
       (harga kembali ke area lahir, bukan dead di bawahnya)
    3. Floor tidak uncertain (token tidak terlalu tua saat pertama terlihat)
    4. Dump sehat — tidak terjadi instan 1-2 candle
    5. Buyer ratio & price position konfirmasi akumulasi
    6. Cooldown belum habis
    """
    mem = pump_memory.get(addr)
    if not mem:
        return False
    if mem.get("status") not in ("watching", "alerted"):
        return False

    peak        = mem.get("peak_mcap", 0)
    birth_floor = mem.get("min_mcap_seen", 0)
    first_age   = mem.get("first_seen_age", 0)
    dump_scans  = mem.get("dump_scan_count", 0)

    if peak <= 0 or birth_floor <= 0:
        return False

    # [BF-1] Floor reliability — token terlalu tua saat pertama terdeteksi
    # Bot mungkin tidak lihat birth candle yang sesungguhnya
    if first_age > PM_FLOOR_UNCERTAIN_AGE:
        print(f"[PM SKIP] {addr[:8]} floor uncertain (first seen {first_age:.1f}h)")
        return False

    # [BF-2] Peak harus minimal 3x dari birth floor (pump cukup besar)
    if peak < birth_floor * PM_BIRTH_PEAK_MIN_RATIO:
        return False

    # [BF-3] Current MCAP harus dalam range birth floor zone
    floor_ratio = mcap / birth_floor
    if not (PM_BIRTH_FLOOR_RATIO_MIN <= floor_ratio <= PM_BIRTH_FLOOR_RATIO_MAX):
        return False

    # [HD] Dump harus sehat — bertahap, bukan instan
    healthy, hd_reason = is_healthy_dump(h1, h6, m5, dump_scans)
    if not healthy:
        print(f"[PM SKIP] {addr[:8]} dump tidak sehat — {hd_reason}")
        return False

    # [ACC] Akumulasi: buyer dominan di dasar range
    if buyer_ratio_m5 < PM_BUYER_RATIO_MIN:
        return False
    if price_position > PM_POSITION_MAX:
        return False

    # Cooldown
    if time.time() - mem.get("last_alert", 0) < PM_WATCHLIST_COOLDOWN:
        return False

    return True


def mark_watchlist_alerted(addr: str):
    """Update timestamp last alert di pump memory."""
    if addr in pump_memory:
        pump_memory[addr]["last_alert"] = time.time()
        pump_memory[addr]["status"]     = "alerted"

# ── [v9.9.2-F4] PRICE POSITION FUNCTION ─────────────────
def get_price_position(price: float, pair: dict) -> float:
    """
    Estimasi posisi harga dalam range 1 jam.
    0.0 = di dasar range (potensi naik)
    1.0 = di puncak range (sudah terlambat)

    Logic:
    - Gunakan h1 untuk estimasi range low/high
    - Gunakan m5 untuk tahu apakah harga masih di puncak atau sudah turun
    - Jika m5 < 0 setelah h1 positif → harga sudah turun dari puncak
    - Jika m5 > 0 dan h1 > 0 → masih di puncak = position tinggi
    """
    try:
        pc  = pair.get("priceChange", {})
        h1  = float(pc.get("h1", 0) or 0)
        m5  = float(pc.get("m5", 0) or 0)

        if h1 > 0:
            # Token pumped dalam 1 jam terakhir
            estimated_low = price / (1 + h1 / 100)  # harga 1 jam lalu

            if m5 < 0:
                # Harga sudah turun dari puncak — estimasi peak lebih tinggi dari sekarang
                # peak = price sebelum m5 negatif terjadi
                estimated_peak = price / (1 + m5 / 100)
            else:
                # Masih naik atau flat — sekarang = peak
                estimated_peak = price

        else:
            # Token dump dalam 1 jam — harga 1 jam lalu lebih tinggi
            estimated_peak = price / (1 + h1 / 100)  # harga 1 jam lalu (lebih tinggi)
            estimated_low  = price                    # sekarang = dasar

        range_size = estimated_peak - estimated_low
        if range_size <= 0:
            return 0.5  # tidak bisa hitung, anggap tengah

        position = (price - estimated_low) / range_size
        return round(max(0.0, min(1.0, position)), 3)

    except Exception as e:
        print(f"[F4 ERR] {e}")
        return -1.0  # tidak tersedia


# ── MAIN ANALYZE ─────────────────────────────────────────
def analyze_pair(pair: dict) -> dict | None:
    try:
        addr    = pair.get("baseToken", {}).get("address", "")
        if not addr: return None
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_id = pair.get("pairAddress", "")
        chain   = pair.get("chainId", "")
        dex     = pair.get("dexId", "")
        price   = float(pair.get("priceUsd", 0) or 0)

        pc  = pair.get("priceChange", {})
        m5  = float(pc.get("m5",  0) or 0)
        h1  = float(pc.get("h1",  0) or 0)
        h6  = float(pc.get("h6",  0) or 0)
        h24 = float(pc.get("h24", 0) or 0)

        vol  = pair.get("volume", {})
        vm5  = float(vol.get("m5",  0) or 0)
        vh1  = float(vol.get("h1",  0) or 0)
        vh6  = float(vol.get("h6",  0) or 0)
        vh24 = float(vol.get("h24", 0) or 0)

        liq  = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        mcap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)

        tx   = pair.get("txns", {})
        bm5  = int(tx.get("m5", {}).get("buys",  0) or 0)
        sm5  = int(tx.get("m5", {}).get("sells", 0) or 0)
        bh1  = int(tx.get("h1", {}).get("buys",  0) or 0)
        sh1  = int(tx.get("h1", {}).get("sells", 0) or 0)
        txh24 = sum([int(tx.get("h24", {}).get("buys",  0) or 0),
                     int(tx.get("h24", {}).get("sells", 0) or 0)])

        if price <= 0 or mcap <= 0: return None

        tier = get_tier(mcap, liq, vh1)
        if not tier: return None

        # ── [v9.9-1] GATE 1: PATTERN CHECK PRIMER ────────
        # Cek dulu dari DexScreener sebelum buang waktu call API luar
        if not check_pattern_exists(tier, m5, h1, h6, h24, vm5, vh1):
            return None

        # C1 & tracked high
        open_c1      = get_open_c1(addr, price)
        tracked_high = get_tracked_high(addr)

        # ── Age fallback sebelum API calls ────────────────
        pair_created = pair.get("pairCreatedAt", 0) or 0
        age_h_fallback = max(0.0, round((time.time() - pair_created / 1000) / 3600, 2)) if pair_created else 0

        # ── [v9.9.1] GATE 1.5: EARLY EXIT FILTERS ────────
        # Evidence-based dari 29 token dataset.
        # Dipasang SEBELUM API calls → hemat GMGN/Rugcheck/Helius.

        # [F1] Spike Too Late
        # m5 > 150% = bot deteksi SAAT candle spike, bukan sebelumnya.
        # Win rate 0% di atas threshold ini. Dataset: pisscoin, Dojo.
        if m5 > 150:
            print(f"[F1 SKIP] {symbol} m5 {m5:.1f}% > 150% — spike too late")
            return None

        # [F2] Already Peaked
        # h1 sangat negatif + harga sudah tembus C1 = distribusi aktif.
        # Dataset: USD h1 -24%→<1x, GAMEOVER distribusi aktif.
        _holds_c1_check = price >= open_c1 * 0.9 if open_c1 > 0 else True
        if h1 < -30 and not _holds_c1_check:
            print(f"[F2 SKIP] {symbol} h1 {h1:.1f}% + no Hold C1 — already peaked")
            return None

        # [F3] MCAP Late Entry
        # Entry terlalu jauh dari kelahiran token = harga sudah naik duluan.
        # Threshold makin ketat untuk token lebih muda (age bands).
        # Dataset: 5/5 token Batch 4 loss karena MCAP terlalu tinggi saat alert.
        # [v9.9.2-FX1] T0 turun dari $85K → $80K
        # Evidence: MCAP $80-85K win rate 11%, MCAP $60-80K win rate 67%
        # [v9.9.2-FX2] Gunakan age_h_fallback (DexScreener) secara konsisten
        # Ini fix bug BBQ: GMGN bilang 0.6h tapi DexScreener > 1h → F3 tidak trigger
        _f3_threshold = {"T0": 80_000, "T1": 200_000,
                         "T2": 700_000, "T3": 2_500_000}.get(tier, 80_000)
        if age_h_fallback == 0:       _f3_threshold *= 0.40  # N/A = unknown = paling konservatif
        elif age_h_fallback < 0.5:    _f3_threshold *= 0.30  # < 30 menit
        elif age_h_fallback < 1.0:    _f3_threshold *= 0.50  # 30–60 menit
        # > 1 jam = threshold normal, tidak ada pengurangan
        if mcap > _f3_threshold:
            print(f"[F3 SKIP] {symbol} MCAP ${mcap:,.0f} > ${_f3_threshold:,.0f} "
                  f"(tier {tier}, age {age_h_fallback:.1f}h) — late entry")
            return None

        gmgn_data     = get_gmgn(addr)
        helius_data   = get_helius(addr)
        rugcheck_data = get_rugcheck(addr)

        # Resolve age_h: GMGN → fallback DexScreener
        age_h = gmgn_data.get("age_hours", 0) or age_h_fallback
        if age_h != gmgn_data.get("age_hours", 0):
            print(f"[AGE FALLBACK] {addr[:8]} {age_h:.1f}h dari DexScreener")
            gmgn_data = {**gmgn_data, "age_hours": age_h}

        # ── [v9.9-2] GATE 3: HARD REJECT (bahaya konkrit) ─
        rejected, reason = check_hard_reject(gmgn_data, rugcheck_data, helius_data, tier)
        if rejected:
            if reason: print(f"[HARD REJECT] {symbol} — {reason}")
            return None

        # ── [v9.9.2-F4] GATE 3.5: PRICE POSITION CHECK ───
        # Hitung posisi harga dalam range h1.
        # Jika harga sudah di >85% dari puncak range → terlambat
        # Evidence: bot tidak bisa bedakan spike vs akumulasi tanpa ini
        r_m5_val = bm5 / max(sm5, 1)
        price_pos = get_price_position(price, pair)

        # [v9.9.2-PM] Update pump memory SETELAH hard reject
        # Token berbahaya (honeypot, dll) tidak masuk watchlist.
        update_pump_memory(addr, mcap, h6, h1, age_h)

        # [FIX] Single call check_watchlist_bounce (sebelumnya double call = bug state race)
        is_watchlist_bounce = check_watchlist_bounce(addr, mcap, r_m5_val, price_pos, h1, h6, m5)

        if price_pos > F4_PEAK_POSITION:
            # Pengecualian: jika dari watchlist bounce → tetap boleh lanjut
            if not is_watchlist_bounce:
                print(f"[F4 SKIP] {symbol} price position {price_pos:.2f} > {F4_PEAK_POSITION} "
                      f"— harga di puncak range h1")
                return None

        # ── [v9.9.2-PM] Watchlist bounce confirm ─────────
        if is_watchlist_bounce:
            print(f"[PM WATCHLIST] {symbol} bounce terdeteksi! "
                  f"MCAP ${mcap:,.0f} buyer {r_m5_val:.1f}x pos {price_pos:.2f}")
            mark_watchlist_alerted(addr)

        # ── GATE 4: PATTERN SCORING (primer) ─────────────
        if tier == "T0":
            p_score, p_sig, p_warn, dip_pct = score_prepump(
                price, open_c1, m5, h1, h6, vm5, vh1,
                bm5, sm5, bh1, sh1, age_h, liq, mcap)
            pump_pct = max(h6, h1)
        else:
            p_score, p_sig, p_warn, dip_pct = score_core_pattern(
                price, open_c1, m5, h1, h6, h24,
                vm5, vh1, vh6, bm5, sm5, bh1, sh1, tracked_high)
            pump_pct = h24 if h24 >= MIN_PUMP_PCT else h6

        # [v9.9.2] Bonus score: harga di dasar range (potensi naik)
        if 0 <= price_pos <= F4_BASE_POSITION_BONUS:
            p_score += 3
            p_sig.append(f"📉 Harga di dasar range ({price_pos:.0%}) — potensi reversal")

        # [v9.9.3] Bonus score: dari watchlist bounce (Birth Floor + Healthy Dump confirmed)
        if is_watchlist_bounce:
            mem = pump_memory.get(addr, {})
            peak        = mem.get("peak_mcap", 0)
            birth_floor = mem.get("min_mcap_seen", 0)
            dump_scans  = mem.get("dump_scan_count", 0)
            if peak > 0:
                peak_str  = f"${peak/1000:.0f}K"
                floor_str = f"${birth_floor/1000:.0f}K" if birth_floor > 0 else "N/A"
                floor_r   = mcap / birth_floor if birth_floor > 0 else 0
                p_score += 5
                p_sig.append(f"🏠 BIRTH FLOOR BOUNCE! Peak {peak_str} → floor {floor_str} "
                             f"(sekarang {floor_r:.1f}x, dump {dump_scans} scan bertahap)")

        # ── GATE 4: SAFETY SCORING (sekunder) ────────────
        s_score, s_sig, s_warn, safety_pct = score_safety(
            gmgn_data, rugcheck_data, helius_data, tier)

        total        = p_score + s_score
        all_warnings = p_warn + s_warn
        all_signals  = p_sig  + s_sig

        # Sort warnings: 🔴 dulu
        red_w   = [w for w in all_warnings if w.startswith("🔴")]
        other_w = [w for w in all_warnings if not w.startswith("🔴")]
        all_warnings = (red_w + other_w)[:4]

        # Reject kalau ada 2+ warning kritis
        if len(red_w) >= 2: return None

        # ── GRADE (Grade C dihapus) ───────────────────────
        if tier == "T0":
            if total >= 15:   grade, status = "A",  "🔥 PRE-PUMP! Entry ultra early!"
            elif total >= 10: grade, status = "B",  "🟡 Sinyal awal ada"
            else: return None
        elif tier == "T1":
            if total >= 20:   grade, status = "A",  "🟢 Setup bagus — EARLY!"
            elif total >= 13: grade, status = "B",  "🟡 Setup cukup"
            else: return None
        elif tier == "T2":
            if total >= 24:   grade, status = "A+", "💎 Setup premium!"
            elif total >= 18: grade, status = "A",  "🟢 Setup sangat bagus!"
            elif total >= 12: grade, status = "B",  "🟡 Setup cukup"
            else: return None
        else:
            if total >= 26:   grade, status = "A+", "💎 Setup premium!"
            elif total >= 20: grade, status = "A",  "🟢 Setup bagus"
            elif total >= 13: grade, status = "B",  "🟡 Setup cukup"
            else: return None

        pos_map = {"T0": T0_MAX_POSITION, "T1": T1_MAX_POSITION,
                   "T2": T2_MAX_POSITION, "T3": T3_MAX_POSITION}
        sl, tp1, tp2, sl_pct = get_sl_tp(price, tier)
        social = get_social(symbol, addr)

        return {
            "name": name, "symbol": symbol, "addr": addr,
            "chain": chain, "dex": dex, "pair_id": pair_id,
            "price": price, "pump_pct": round(pump_pct, 1),
            "dip_pct": round(dip_pct, 1), "open_c1": open_c1,
            # [v9.9.2-FX3] holds_c1 display: token age < 1h → netral (True)
            # Konsisten dengan score_prepump yang skip C1 check untuk token very fresh
            "holds_c1": (True if age_h < 1.0
                         else (price >= open_c1 * 0.9) if open_c1 > 0
                         else True),
            "m5": m5, "h1": h1, "h6": h6, "h24": h24,
            "vm5": vm5, "vh1": vh1, "vh24": vh24,
            "bm5": bm5, "sm5": sm5, "bh1": bh1, "sh1": sh1, "txh24": txh24,
            "liq": liq, "mcap": mcap,
            "total": total, "status": status, "grade": grade,
            "tier": tier, "max_pos": pos_map[tier],
            "signals":    all_signals[:5],
            "warnings":   all_warnings,
            "safety_pct": safety_pct,
            # GMGN
            "lp_burned":     gmgn_data.get("lp_burned")     if gmgn_data.get("available") else (
                             100.0 if rugcheck_data.get("lp_burned") else (
                             50.0  if rugcheck_data.get("lp_locked") else None)),
            "bundle_pct":    gmgn_data.get("bundle_pct")    if gmgn_data.get("available") else None,
            "dev_burned_pct":gmgn_data.get("dev_burned_pct") if gmgn_data.get("available") else None,
            "holders":       gmgn_data.get("holders")        if gmgn_data.get("available") else None,
            "sniper_count":  gmgn_data.get("sniper_count", 0) if gmgn_data.get("available") else None,
            "age_h":         age_h,
            "smart_buy":     gmgn_data.get("smart_buy", 0),
            "smart_sell":    gmgn_data.get("smart_sell", 0),
            "gmgn_ok":       gmgn_data.get("available", False),
            # Rugcheck
            "rc_ok":     rugcheck_data.get("available", False),
            "rc_score":  rugcheck_data.get("score", 0),
            "rc_risks":  rugcheck_data.get("risk_count", 0),
            "mint_auth": rugcheck_data.get("mint_auth", False),
            # Helius
            "helius_ok": helius_data.get("available", False),
            "top10_pct": helius_data.get("top10_pct", 0),
            "hub_spoke": helius_data.get("hub_spoke", False),
            # Social
            "social_ok":     social.get("available", False),
            "galaxy_score":  social.get("galaxy_score", 0),
            "alt_rank":      social.get("alt_rank", 0),
            "mention_trend": social.get("mention_trend", 0),
            "kol_active":    social.get("kol_active", False),
            # SL/TP
            "sl": sl, "tp1": tp1, "tp2": tp2, "sl_pct": sl_pct,
            # Links
            "chart":    f"https://dexscreener.com/{chain}/{pair_id}",
            "gmgn_url": f"https://gmgn.ai/sol/token/{addr}",
            "axiom_url":f"https://axiom.xyz/sol/{addr}",
        }

    except Exception as e:
        print(f"[ANALYZE ERR] {e}")
        return None

# ── FORMAT ALERT ─────────────────────────────────────────
def format_alert(s: dict, is_update: bool = False) -> str:
    tier_map = {
        "T0": ("🔥", "PRE-PUMP ULTRA EARLY",
               f"MCAP ${s['mcap']/1000:.0f}K — potensi 10-50x! SANGAT BERISIKO",
               f"Max {s['max_pos']} SOL ⛔ EXTREME RISK"),
        "T1": ("🟣", "EARLY ENTRY",
               f"MCAP ${s['mcap']/1000:.0f}K — potensi 5-10x!",
               f"Max {s['max_pos']} SOL ⚠️ HIGH RISK"),
        "T2": ("🟢", "NORMAL ENTRY",
               f"MCAP ${s['mcap']/1000:.0f}K — setup bagus",
               f"Max {s['max_pos']} SOL"),
        "T3": ("🔵", "LATE ENTRY",
               f"MCAP ${s['mcap']/1000:.0f}K — established",
               f"Max {s['max_pos']} SOL"),
    }
    temoji, tlabel, tdesc, tpos = tier_map.get(s["tier"], ("⚪","","",""))
    grade_emoji = {"A+":"💎","A":"🏆","B":"🥈"}.get(s["grade"],"")

    # [v9.9-9] Label UPDATE untuk alert token yang sama
    alert_label = "🔄 UPDATE ALERT v9.9.3!" if is_update else "🚨 DIP &amp; RIP ALERT v9.9.3!"
    mode_lbl    = "🔥 PRE-PUMP MODE" if s["tier"] == "T0" else "📉 DIP &amp; RIP MODE"

    signals_text = "\n".join(s["signals"])  if s["signals"]  else "—"
    warn_text    = "\n".join(s["warnings"]) if s["warnings"] else "✅ Tidak ada warning"

    # Safety fields
    lp  = s.get("lp_burned");   bnd = s.get("bundle_pct")
    dev = s.get("dev_burned_pct"); holders = s.get("holders")
    sniper = s.get("sniper_count")

    lp_str  = f"{lp:.0f}%"    if lp  is not None else "N/A"
    bnd_str = f"{bnd:.1f}%"   if bnd is not None else "N/A"
    dev_str = f"{dev:.0f}% burned" if dev is not None else "N/A"
    holders_str = f"{holders:,}" if holders is not None else "N/A"
    sniper_str  = str(sniper) if sniper is not None else "N/A"

    lp_e  = ("🔒" if lp is not None and lp >= 80 else "⚠️" if lp is not None and lp >= 50
             else "🔴" if lp is not None and lp < 50 else "❓")
    bnd_e = ("✅" if bnd is not None and bnd <= 5 else "⚠️" if bnd is not None and bnd <= 15
             else "🔴" if bnd is not None else "❓")
    dev_e = ("✅" if dev is not None and dev >= 80 else "🟡" if dev is not None and dev >= 20
             else "🔴" if dev is not None and dev == 0 else
             "⚠️" if dev is not None else "❓")

    # [v9.9-8] Safety score bar
    safety_pct = s.get("safety_pct", 0)
    bar_fill  = "█" * (safety_pct // 10)
    bar_empty = "░" * (10 - safety_pct // 10)
    safety_color = "🟢" if safety_pct >= 60 else "🟡" if safety_pct >= 30 else "🔴"

    # Smart Money — hanya tampil jika GMGN tersedia
    sm_line = ""
    if s.get("gmgn_ok"):
        sm = s["smart_buy"] - s["smart_sell"]
        sm_t = f"+{sm} NET BUY 🟢" if sm > 0 else (f"{sm} NET SELL 🔴" if sm < 0 else "Netral")
        sm_line = f"\n  💡 Smart $: {sm_t}"

    # Helius block
    helius_block = ""
    if s["helius_ok"]:
        hub_t = "⚠️ ADA!" if s["hub_spoke"] else "✅ Tidak ada"
        helius_block = f"\n  🏦 Top 10: <b>{s['top10_pct']:.1f}%</b> | Hub&amp;Spoke: {hub_t}"

    # Rugcheck block
    rc_block = ""
    if s.get("rc_ok"):
        rc_e   = "✅" if s["rc_score"] >= 70 else "⚠️" if s["rc_score"] >= 50 else "🔴"
        mint_t = "🔴 ADA!" if s.get("mint_auth") else "✅ Disabled"
        rc_block = (f"\n  {rc_e} Rugcheck: <b>{s['rc_score']}/100</b> "
                   f"| Risks: <b>{s['rc_risks']}</b> | Mint: {mint_t}")

    # Social block
    social_block = ""
    if s.get("social_ok"):
        arr  = "📈" if s["mention_trend"] >= 0 else "📉"
        kol  = "👑 YA!" if s["kol_active"] else "—"
        social_block = (f"\n🌐 <b>Social:</b> Galaxy {s['galaxy_score']}/100 | "
                       f"#{s['alt_rank']} | {arr}{s['mention_trend']:+.0f}% | KOL: {kol}")

    vol24_t = f"${s['vh24']/1e6:.1f}M" if s['vh24'] >= 1e6 else f"${s['vh24']/1000:.0f}K"
    c1_t    = f"${s['open_c1']:.8f}"   if s['open_c1'] > 0 else "N/A"
    age_t   = f"{s['age_h']:.1f}h"     if s['age_h']  > 0 else "N/A"
    dip_line = (f"📈 Momentum h6: <b>+{s['pump_pct']}%</b>" if s["tier"] == "T0"
               else f"📉 Dip dari high: <b>-{s['dip_pct']}%</b>")
    footer   = ("⛔ T0 ULTRA EARLY — Max 0.02 SOL. Konfirmasi chart dulu!"
               if s["tier"] == "T0" else "⚡ Konfirmasi di GMGN + Axiom!")

    return f"""
{alert_label} {mode_lbl}

{temoji} <b>{tlabel}</b>
📍 {tdesc}
💰 <b>{tpos}</b>

🪙 <b>{s['name']} ({s['symbol']})</b>
📊 {s['dex'].upper()} | Solana | ⏱ {age_t}

{grade_emoji} <b>{s['status']}</b>
📊 Score: {s['total']} | Grade: {s['grade']}

💹 Harga: <b>${s['price']:.8f}</b>
📈 Pump: <b>+{s['pump_pct']}%</b>
{dip_line}
🏁 Open C1: {c1_t}
{'✅' if s['holds_c1'] else '🔴'} Hold C1: <b>{'YA ✅' if s['holds_c1'] else 'TIDAK ❌'}</b>
📊 m5: <b>{s['m5']:+.1f}%</b> | h1: {s['h1']:+.1f}% | h6: {s['h6']:+.1f}%

🔒 <b>SAFETY</b> {safety_color} {safety_pct}/100
  [{bar_fill}{bar_empty}]
  {lp_e} LP Burned: <b>{lp_str}</b>
  {bnd_e} Bundle: <b>{bnd_str}</b>
  {dev_e} Dev Burned: <b>{dev_str}</b>
  👥 Holders: <b>{holders_str}</b>
  🎯 Snipers: <b>{sniper_str}</b>{sm_line}{rc_block}{helius_block}{social_block}

✅ <b>SINYAL:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warn_text}

💰 <b>ENTRY ZONE:</b>
  Beli:        <b>${s['price']:.8f}</b>
  🔴 Stop Loss: <b>${s['sl']:.8f}</b> (-{s['sl_pct']}%)
  🟡 Target 1:  <b>${s['tp1']:.8f}</b>
  🟢 Target 2:  <b>${s['tp2']:.8f}</b>

📊 Vol 24H: {vol24_t} | Liq: ${s['liq']/1000:.0f}K
🔄 Buy/Sell h1: {s['bh1']/max(s['sh1'],1):.1f}x | m5: {s['bm5']/max(s['sm5'],1):.1f}x

🔗 <a href="{s['chart']}">Chart</a> | <a href="{s['gmgn_url']}">GMGN</a> | <a href="{s['axiom_url']}">Axiom</a>

{footer}
⚠️ BUKAN financial advice. DYOR!
""".strip()

# ── MAIN LOOP ─────────────────────────────────────────────
def scan_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    trending = get_trending_tokens()
    new_tok  = get_new_tokens()
    all_addr = set()
    for t in trending + new_tok:
        a = t.get("tokenAddress","") or t.get("address","")
        if a: all_addr.add(a)

    print(f"[SCAN] {len(all_addr)} token...")
    t0=t1=t2=t3=0
    state_dirty = False
    now = time.time()

    for addr in list(all_addr)[:40]:
        last_alert = alerted_tokens.get(addr, 0)
        if now - last_alert < ALERT_COOLDOWN_SEC: continue

        pair   = get_pair(addr)
        if not pair: continue
        signal = analyze_pair(pair)
        if signal:
            if   signal["tier"] == "T0": t0 += 1
            elif signal["tier"] == "T1": t1 += 1
            elif signal["tier"] == "T2": t2 += 1
            else:                        t3 += 1

            # [v9.9-9] Label UPDATE jika token sama dalam 15 menit
            is_update = (last_alert > 0 and
                        now - last_alert < UPDATE_LABEL_WINDOW_SEC)

            print(f"[{signal['tier']}{'*UPDATE' if is_update else ''}] "
                  f"{signal['symbol']} Grade:{signal['grade']} "
                  f"Score:{signal['total']} Safety:{signal['safety_pct']}/100")

            alerted_tokens[addr] = now
            state_dirty = True
            send_telegram(format_alert(signal, is_update))
        time.sleep(0.5)

    if state_dirty:
        save_state()

    print(f"[DONE] T0:{t0} T1:{t1} T2:{t2} T3:{t3}")

def main():
    print("=" * 60)
    print("  DIP & RIP BOT v9.9.3 — CHART IS KING")
    print("  Pattern primer | Birth Floor | Healthy Dump")
    print("=" * 60)
    print(f"  Helius  : {'✅' if HELIUS_KEY else '⚠️ Belum ada key'}")
    print(f"  Lunar   : {'✅' if LUNARCRUSH_KEY else '⚠️ Belum ada key'}")
    print(f"  State   : {'✅ ' + str(STATE_FILE) if STATE_FILE.exists() else '🆕 Fresh start'}")
    print(f"  T0 Pre-pump : ${T0_MIN_MCAP/1000:.0f}K–${T0_MAX_MCAP/1000:.0f}K")
    print(f"  T1 Early    : ${T1_MIN_MCAP/1000:.0f}K–${T1_MAX_MCAP/1000:.0f}K")
    print(f"  T2 Normal   : ${T2_MIN_MCAP/1000:.0f}K–${T2_MAX_MCAP/1000:.0f}K")
    print(f"  T3 Late     : ${T3_MIN_MCAP/1000:.0f}K–${T3_MAX_MCAP/1_000_000:.0f}M")
    print("=" * 60)

    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v9.9.3 aktif!</b>\n\n"
        "📊 <b>Arsitektur:</b>\n"
        "   1️⃣ Chart pattern → primer\n"
        "   2️⃣ Filter F1/F2/F3 → evidence-based\n"
        "   3️⃣ Price Position F4 → anti spike\n"
        "   4️⃣ Hard reject → bahaya konkrit\n"
        "   5️⃣ Safety data → confidence booster\n\n"
        "🆕 <b>Fitur baru v9.9.3:</b>\n"
        "   [BF] Birth Floor tracking aktif\n"
        "   [HD] Healthy Dump filter (proxy + scan)\n"
        "   [PM3] PM Watchlist v3: Floor Ratio check\n\n"
        "🚨 Scan setiap 30 detik!"
    )

    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"[ERR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
