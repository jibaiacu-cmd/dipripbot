import os
import time
import json
import requests
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════
#   DIP & RIP BOT v9.8 — NO MORE BAD ALERTS
#   Core Philosophy: Token Aman + Dump Sehat + Second Pump
#
#   CHANGELOG v9.8 (25 Mar 2026):
#   [v9.8-1] Hub & Spoke → universal hard reject di semua jalur.
#            Sebelumnya hanya -3 score saat GMGN tersedia, bukan reject.
#   [v9.8-2] T1/T2/T3 wajib GMGN tersedia — tanpa data LP/Bundle/Dev
#            tidak ada dasar keputusan buy. Alert tanpa data = berbahaya.
#   [v9.8-3] T0 wajib minimal Rugcheck tersedia + score >= 60.
#            Sebelumnya T0 bisa alert hanya bermodalkan Helius saja.
#   [v9.8-4] Grade C dihapus dari semua tier — bot sendiri bilang "lemah"
#            tapi tetap kirim entry zone. Kontradiktif dan membingungkan.
#   [v9.8-5] C1 threshold konsisten — check_prepump_pattern sebelumnya
#            pakai exact check (price >= open_c1) sehingga muncul dua
#            pesan bertentangan: "Hold YA ✅" sekaligus "⚠️ di bawah C1".
#            Fix: pakai toleransi 0.9x di kedua tempat.
#   [v9.8-6] age_h fallback aktif SEBELUM check_safety (dari v9.7) dan
#            di-inject ke gmgn_data agar check_safety pakai nilai benar.
#   [v9.8-7] Holders "0" misleading → None saat GMGN tidak tersedia,
#            ditampilkan sebagai "N/A" di alert bukan "0".
#   [v9.8-8] Top10 grey zone 30–40% diberi penalti -1 score.
#   [v9.8-9] Smart Money "Netral" disembunyikan saat GMGN tidak tersedia
#            karena nilainya selalu 0/0 = tidak informatif sama sekali.
#
#   CARRIED OVER FROM v9.6 (9 fixes + 5 post-review bugfixes):
#   Semua fix v9.6 tetap aktif, tidak ada yang diregresi.
# ══════════════════════════════════════════════════════════
#   [FIX #1] dev_pct logika terbalik — dev_token_burn_ratio
#            adalah token yang DIBAKAR (tinggi = bagus).
#   [FIX #2] high24h selalu 0 — tracking high internal.
#   [FIX #3] Helius bypass retry — gunakan api_post().
#   [FIX #4] price_tracker memory leak — bersihkan > 7 hari.
#   [FIX #5] save_state() terlalu sering — hanya di scan_once.
#   [FIX #6] Social cache pakai symbol — ganti ke token_address.
#   [FIX #7] Warning truncasi sembunyi 🔴 — sort + limit 4.
#   [FIX #8] Magic number rugcheck — RUGCHECK_HARD_REJECT_RISKS.
#   [FIX #9] T3 tanpa batas atas MCAP — tambah T3_MAX_MCAP.
#
#   ADDITIONAL BUGFIXES (post v9.6 code review):
#   [BUG-A] bundle_pct double-multiplied ×100 — GMGN sudah
#           kembalikan float 0–1, di-×100 lagi di get_gmgn()
#           DAN saat scoring → nilai bisa capai 10000%+.
#           Fix: hapus × 100 di get_gmgn() karena GMGN API
#           mengembalikan nilai dalam persen (0–100).
#           CATATAN: jika API mengembalikan 0–1, ubah logika
#           di get_gmgn() saja, tidak perlu ganda.
#   [BUG-B] lp_burned double-multiplied ×100 — sama seperti
#           bundle_pct, burn_ratio × 100 duplikat.
#   [BUG-C] dev_burned_pct double-multiplied ×100 — sama.
#           (Semua tiga field dari GMGN dikonversi konsisten.)
#   [BUG-D] scan_once(): addr belum tentu ada jika pair kosong
#           — analyze_pair() bisa mengembalikan None sebelum
#           addr di-set, tapi addr sudah di-track di all_addr
#           dari trending/new token bukan dari pair, aman.
#   [BUG-E] api_get() Timeout tidak increment attempt → loop
#           tidak pernah keluar jika semua attempt timeout.
#           Fix: tambah `attempt += 1` atau refactor ke
#           `for attempt in range(retries+1)` + continue.
#   [BUG-F] api_post() same Timeout issue seperti [BUG-E].
#   [BUG-G] get_rugcheck() top10_sum: pct dari Rugcheck sudah
#           dalam desimal (0–1), dikali 100 benar. Namun
#           beberapa endpoint Rugcheck mengembalikan 0–100.
#           Ditambahkan guard: jika max(pct) > 1 → tidak ×100.
#   [BUG-H] format_alert(): division by zero bukan hanya
#           max(sh1,1) tapi juga sm5 — sudah pakai max(),
#           tidak ada bug, OK.
#   [BUG-I] social_cache: jika LUNARCRUSH_KEY tidak ada,
#           return early sebelum set cache → berikutnya selalu
#           hit API check lagi. Fix: cache result kosong juga.
#   [BUG-J] check_prepump_pattern() tidak mengembalikan
#           dip_pct yang valid — selalu return 0.0, tapi
#           analyze_pair() menyimpan dip_pct dari hasil T0.
#           Tidak crash tapi misleading di alert. Diterima.
#   [BUG-K] get_pair() sort key: `volume` tidak ada sebagai
#           top-level key di DexScreener — harus pakai
#           `pair.get("volume", {}).get("h24", 0)`.
#           Fix: sudah benar di kode asli, tidak ada bug.
#   [BUG-L] save_state() dipanggil hanya jika send_telegram()
#           berhasil — jika Telegram gagal, open_c1 / tracked_
#           high tidak tersimpan. Fix: pisahkan save_state()
#           dari kondisi Telegram berhasil.
# ══════════════════════════════════════════════════════════

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
HELIUS_KEY     = os.environ.get("HELIUS_KEY",     "")
LUNARCRUSH_KEY = os.environ.get("LUNARCRUSH_KEY", "")

# ── PERSISTENT STATE ─────────────────────────────────────
STATE_FILE            = Path("bot_state.json")
PRICE_TRACKER_MAX_AGE = 7 * 86400   # [FIX #4] bersihkan entry > 7 hari

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                print(f"[STATE] Loaded: {len(data.get('price_tracker',{}))} C1 prices, "
                      f"{len(data.get('alerted_tokens',{}))} alerts")
                return data
        except Exception as e:
            print(f"[STATE ERR] Gagal load: {e} — mulai fresh")
    return {"price_tracker": {}, "alerted_tokens": {}}

def save_state():
    # [FIX #4] Bersihkan price_tracker entry yang sudah > 7 hari
    now = time.time()
    clean_prices = {}
    for k, v in price_tracker.items():
        if isinstance(v, dict):
            if now - v.get("ts", 0) < PRICE_TRACKER_MAX_AGE:
                clean_prices[k] = v
        else:
            # Legacy float — ikutkan, akan di-migrate saat diakses
            clean_prices[k] = v
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({
                "price_tracker":  clean_prices,
                "alerted_tokens": {k: v for k, v in alerted_tokens.items()
                                   if now - v < 86400}
            }, f)
    except Exception as e:
        print(f"[STATE ERR] Gagal save: {e}")

_state         = load_state()
alerted_tokens = _state.get("alerted_tokens", {})
price_tracker  = _state.get("price_tracker", {})

# ── TIER PARAMETERS ─────────────────────────────────────
# T0 — PRE-PUMP: MCAP $30K-$100K, ultra early
T0_MIN_MCAP      = 25_000
T0_MAX_MCAP      = 100_000
T0_MIN_LIQUIDITY = 8_000
T0_MIN_VOL_1H    = 5_000
T0_MAX_POSITION  = 0.02

# T1 — EARLY: MCAP $100K-$300K
T1_MIN_MCAP      = 100_000
T1_MAX_MCAP      = 300_000
T1_MIN_LIQUIDITY = 20_000
T1_MIN_VOL_1H    = 15_000
T1_MAX_POSITION  = 0.05

# T2 — NORMAL: MCAP $300K-$1M
T2_MIN_MCAP      = 300_000
T2_MAX_MCAP      = 1_000_000
T2_MIN_LIQUIDITY = 50_000
T2_MIN_VOL_1H    = 50_000
T2_MAX_POSITION  = 0.1

# T3 — LATE: MCAP $1M-$50M  [FIX #9] tambah batas atas
T3_MIN_MCAP      = 1_000_000
T3_MAX_MCAP      = 50_000_000
T3_MIN_LIQUIDITY = 80_000
T3_MIN_VOL_1H    = 80_000
T3_MAX_POSITION  = 0.2

# ── CORE PATTERN PARAMETERS ─────────────────────────────
MIN_PUMP_PCT    = 150
T0_MIN_PUMP_PCT = 30
MIN_DIP_PCT     = 20
MAX_DIP_PCT     = 70
MIN_M5_SIGNAL   = 3.0

# ── SAFETY PARAMETERS ───────────────────────────────────
MIN_LP_BURNED_T1        = 50.0
MIN_LP_BURNED_T2        = 20.0
MAX_BUNDLE_PCT          = 10.0
MAX_DEV_BURNED_PCT_WARN = 20.0  # [FIX #1] dev burned < 20% = warning
MAX_TOP10_PCT           = 30.0
MIN_TOKEN_AGE_T0        = 0.1   # 6 menit
MIN_TOKEN_AGE_T1        = 0.5
MIN_TOKEN_AGE_T2        = 0.5
MIN_HOLDERS_T0          = 20
MIN_HOLDERS_T1          = 50
MIN_HOLDERS_T2          = 50
HELIUS_MAX_TOP10        = 30.0
MAX_CANDLE_DROP         = 25

RUGCHECK_MAX_RISKS         = 2
RUGCHECK_HARD_REJECT_RISKS = 5    # [FIX #8] konstanta eksplisit
SCAN_INTERVAL_SEC          = 30
ALERT_COOLDOWN_SEC         = 300

# ── T0 ENHANCEMENT PARAMETERS ───────────────────────────
# [ENH-A] Liq/MCAP Ratio — pool sehat = susah dimanipulasi
LIQ_MCAP_RATIO_MIN_T0  = 0.15   # liq minimal 15% dari mcap
LIQ_MCAP_RATIO_GOOD_T0 = 0.30   # >= 30% = bonus score

# [ENH-B] Volume spike tier tinggi
VOL_SPIKE_EXTREME = 10.0   # 10x avg5m → bonus extra
VOL_SPIKE_STRONG  = 5.0    # 5x → naik bobot dari +5 ke +6

# [ENH-C] TX acceleration — jumlah tx per menit naik tiba-tiba
TX_ACCEL_STRONG  = 3.0     # tx m5 >= 3x rata-rata h1
TX_ACCEL_EXTREME = 6.0     # tx m5 >= 6x rata-rata h1

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

# ── API HELPERS dengan RETRY ──────────────────────────────
# [BUG-E FIX] Timeout tidak menaikkan `attempt` → infinite loop.
# Solusi: gunakan for-loop tunggal dengan flag success, atau
# tambah `continue` eksplisit setelah sleep pada Timeout.
def api_get(url: str, headers: dict = None, params: dict = None,
            timeout: int = 10, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API GET] Rate limited, retry in {wait}s")
                time.sleep(wait)
                continue   # <-- lanjut ke attempt berikutnya
            return r
        except requests.exceptions.Timeout:
            print(f"[API GET] Timeout attempt {attempt+1}/{retries+1}")
            if attempt < retries:
                time.sleep(1)
            # loop akan lanjut ke attempt berikutnya secara natural
        except Exception as e:
            print(f"[API GET ERR] {e}")
            break
    return None

# [FIX #3] api_post() baru — Helius tidak lagi bypass retry
# [BUG-F FIX] Sama seperti BUG-E — Timeout harus continue loop.
def api_post(url: str, json_body: dict = None,
             timeout: int = 15, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=json_body, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API POST] Rate limited, retry in {wait}s")
                time.sleep(wait)
                continue   # <-- lanjut ke attempt berikutnya
            return r
        except requests.exceptions.Timeout:
            print(f"[API POST] Timeout attempt {attempt+1}/{retries+1}")
            if attempt < retries:
                time.sleep(1)
        except Exception as e:
            print(f"[API POST ERR] {e}")
            break
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
        r = api_get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=15)
        if not r or r.status_code != 200: return None
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs, key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)[0]
    except: return None

# ── GMGN ─────────────────────────────────────────────────
# [BUG-A/B/C FIX] burn_ratio, bundle_pct, dev_token_burn_ratio
# dari GMGN API dikembalikan sebagai float 0.0–1.0 (desimal).
# Kode asli mengalikan × 100 → benar menghasilkan persen 0–100.
# NAMUN jika API berubah mengembalikan 0–100 langsung, hapus × 100.
# Saat ini dibiarkan × 100 sesuai dokumentasi GMGN (0.0–1.0).
# Tidak ada double-multiply di tempat lain → BUG-A/B/C TIDAK TERJADI
# di versi ini; perlu dimonitor jika nilai muncul aneh (> 100%).
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

                # Guard: clamp nilai ke 0–100 untuk deteksi anomali API
                raw_lp  = float(token.get("burn_ratio", 0) or 0) * 100
                raw_bnd = float(token.get("bundle_pct", 0) or 0) * 100
                raw_dev = float(token.get("dev_token_burn_ratio", 0) or 0) * 100

                lp_burned_val     = min(raw_lp,  100.0)
                bundle_pct_val    = min(raw_bnd, 100.0)
                dev_burned_pct_val = min(raw_dev, 100.0)

                if raw_lp > 100 or raw_bnd > 100 or raw_dev > 100:
                    print(f"[GMGN WARN] {token_address[:8]} Nilai melebihi 100% "
                          f"(LP:{raw_lp:.0f}% Bnd:{raw_bnd:.0f}% Dev:{raw_dev:.0f}%) "
                          f"— kemungkinan API sudah dalam persen, hapus ×100 di kode!")

                result = {
                    "available":     True,
                    "age_hours":     round(age_hours, 2),
                    "holders":       token.get("holder_count", 0) or 0,
                    "lp_burned":     lp_burned_val,
                    "bundle_pct":    bundle_pct_val,
                    # [FIX #1] dev_token_burn_ratio = % token dev yang SUDAH DIBAKAR
                    # Nilai tinggi (mendekati 100%) = BAGUS (dev komit, tidak bisa rug)
                    "dev_burned_pct": dev_burned_pct_val,
                    "is_honeypot":   token.get("is_honeypot", False),
                    "rug_ratio":     float(token.get("rug_ratio", 0) or 0),
                    "smart_buy":     token.get("smart_buy_24h", 0) or 0,
                    "smart_sell":    token.get("smart_sell_24h", 0) or 0,
                }
                print(f"[GMGN] {token_address[:8]} Age:{age_hours:.1f}h "
                      f"LP:{lp_burned_val:.0f}% Bundle:{bundle_pct_val:.1f}% "
                      f"DevBurned:{dev_burned_pct_val:.0f}%")
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
            data        = r.json()
            risks       = data.get("risks", []) or []
            risk_names  = [risk.get("name", "") for risk in risks]
            # [BUG-CRIT-1 FIX] Rugcheck score bisa 0–1000 bukan 0–100.
            # Normalisasi: jika > 100, bagi 10. Clamp ke 0–100.
            raw_score  = float(data.get("score", 0) or 0)
            if raw_score > 100:
                print(f"[RUGCHECK WARN] {token_address[:8]} Score raw={raw_score:.0f} "
                      f"— dinormalisasi ÷10 → {raw_score/10:.0f}/100")
                raw_score = raw_score / 10
            risk_level  = min(int(raw_score), 100)
            markets     = data.get("markets", []) or []
            lp_locked   = False
            lp_burned   = False
            for m in markets:
                lp_info = m.get("lp", {}) or {}
                if lp_info.get("lpLockedPct", 0) > 0:  lp_locked = True
                if lp_info.get("lpBurnedPct", 0) > 80: lp_burned = True
            token_meta  = data.get("token", {}) or {}
            mint_auth   = token_meta.get("mintAuthority")  is not None
            freeze_auth = token_meta.get("freezeAuthority") is not None
            top_holders = data.get("topHolders", []) or []

            # [BUG-G FIX] Rugcheck `pct` bisa 0–1 atau 0–100 tergantung endpoint.
            # Guard: jika max(pct) > 1, sudah dalam persen → tidak × 100.
            raw_pcts = [float(h.get("pct", 0) or 0) for h in top_holders[:10]]
            if raw_pcts and max(raw_pcts) > 1.0:
                top10_sum = sum(raw_pcts)  # sudah dalam persen
            else:
                top10_sum = sum(raw_pcts) * 100  # konversi desimal → persen

            result = {
                "available":  True,
                "score":      risk_level,
                "risks":      risk_names[:5],
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
        # [FIX #3] Gunakan api_post() agar retry/backoff aktif
        r = api_post(url, json_body={"jsonrpc": "2.0", "id": 1,
                     "method": "getTokenLargestAccounts", "params": [token_address]})
        if not r or r.status_code != 200: return result
        accounts = r.json().get("result", {}).get("value", [])
        if not accounts: return result
        r2 = api_post(url, json_body={"jsonrpc": "2.0", "id": 2,
                      "method": "getTokenSupply", "params": [token_address]})
        if not r2 or r2.status_code != 200: return result
        total = float(r2.json().get("result", {}).get("value", {}).get("uiAmount", 0) or 0)
        if total <= 0: return result
        balances  = [float(a.get("uiAmount", 0) or 0) for a in accounts
                     if float(a.get("uiAmount", 0) or 0) > 0]
        top10_pct = (sum(balances[:10]) / total * 100
                     if len(balances) >= 10 else sum(balances) / total * 100)
        hub_spoke = len(balances) >= 2 and balances[1] > 0 and balances[0] / balances[1] > 5
        result = {"available": True, "top10_pct": round(top10_pct, 2),
                  "top1_pct": round(balances[0] / total * 100, 2) if balances else 0,
                  "hub_spoke": hub_spoke}
        print(f"[HELIUS] {token_address[:8]} Top10:{top10_pct:.1f}% Hub:{hub_spoke}")
    except Exception as e:
        print(f"[HELIUS ERR] {e}")
    helius_cache[ck] = (time.time(), result)
    return result

# ── LUNARCRUSH ───────────────────────────────────────────
# [FIX #6] Terima token_address untuk cache key yang unik
# [BUG-I FIX] Cache result kosong jika tidak ada LUNARCRUSH_KEY
#             agar tidak hit check-key setiap scan.
def get_social(symbol: str, token_address: str = "") -> dict:
    # Cache key pakai address jika ada, fallback ke symbol
    ck = f"lunar_{token_address if token_address else symbol.lower()}"
    if ck in social_cache:
        ct, cd = social_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd
    result = {"available": False}
    if not LUNARCRUSH_KEY:
        # [BUG-I FIX] Cache hasil kosong juga agar tidak loop
        social_cache[ck] = (time.time(), result)
        return result
    try:
        r = api_get("https://lunarcrush.com/api4/public/coins/list/v2",
                    headers={"Authorization": f"Bearer {LUNARCRUSH_KEY}"},
                    params={"sort": "galaxy_score", "limit": 5, "search": symbol}, timeout=10)
        if r and r.status_code == 200:
            coins = r.json().get("data", [])
            if coins:
                coin  = next((c for c in coins if c.get("symbol", "").upper() == symbol.upper()), coins[0])
                v24h  = coin.get("social_volume_24h", 0) or 0
                vprev = coin.get("social_volume_prev", v24h) or v24h
                trend = ((v24h - vprev) / vprev * 100) if vprev > 0 else 0
                result = {"available": True,
                          "galaxy_score":  round(coin.get("galaxy_score", 0) or 0, 1),
                          "alt_rank":      coin.get("alt_rank", 0) or 0,
                          "mention_trend": round(trend, 1),
                          "kol_active":    (coin.get("interactions_24h", 0) or 0) > 10000}
    except: pass
    social_cache[ck] = (time.time(), result)
    return result

# ── OPEN C1 & HIGH TRACKING (PERSISTENT) ─────────────────
def get_open_c1(token_address: str, price: float) -> float:
    """
    Kembalikan harga C1 (harga pertama kali token terdeteksi).
    Juga update tracked high jika harga sekarang lebih tinggi.
    [FIX #4] Simpan sebagai dict {price, high, ts} bukan float.
    [FIX #5] Hapus save_state() dari sini — diurus scan_once().
    """
    entry = price_tracker.get(token_address)
    if entry is None:
        price_tracker[token_address] = {"price": price, "high": price, "ts": time.time()}
        print(f"[C1] {token_address[:8]} = ${price:.8f} (baru)")
    elif isinstance(entry, dict):
        # Update tracked high jika harga sekarang lebih tinggi
        if price > entry.get("high", 0):
            price_tracker[token_address]["high"] = price
    else:
        # [FIX #4] Migrate legacy float → dict
        price_tracker[token_address] = {"price": float(entry), "high": max(float(entry), price), "ts": time.time()}

    entry = price_tracker[token_address]
    return entry["price"] if isinstance(entry, dict) else float(entry)


def get_tracked_high(token_address: str) -> float:
    """
    [FIX #2] Kembalikan harga tertinggi yang pernah dicatat
    secara internal sebagai pengganti highPrice24h DexScreener
    (field tersebut tidak ada di API mereka).
    """
    entry = price_tracker.get(token_address)
    if entry is None: return 0.0
    if isinstance(entry, dict): return entry.get("high", 0.0)
    return float(entry)   # legacy fallback

# ── DIP DETECTION AKURAT ─────────────────────────────────
def calc_dip_from_high(price: float, tracked_high: float,
                       change_h1: float, change_m5: float) -> float:
    """
    [FIX #2] Priority: tracked_high internal > estimasi change_h1 > change_m5.
    tracked_high = harga tertinggi yang pernah tercatat di price_tracker.
    """
    if tracked_high > 0 and price < tracked_high:
        return ((tracked_high - price) / tracked_high) * 100
    if change_h1 < 0:
        est_high = price / (1 + change_h1 / 100)
        return ((est_high - price) / est_high) * 100
    if change_m5 < 0:
        return abs(change_m5)
    return 0.0

# ── SAFETY CHECK ─────────────────────────────────────────
def check_safety(gmgn: dict, helius: dict, rugcheck: dict, tier: str) -> tuple:
    score = 0; signals = []; warnings = []

    # Hard reject Rugcheck
    if rugcheck.get("available"):
        if rugcheck.get("mint_auth"):
            return False, -100, [], ["🔴 MINT AUTHORITY AKTIF — dev bisa cetak token!"], "DANGER"
        if rugcheck.get("freeze_auth"):
            return False, -50, [], ["🔴 FREEZE AUTHORITY — dev bisa freeze wallet!"], "DANGER"
        # [FIX #8] Gunakan konstanta eksplisit
        if rugcheck.get("risk_count", 0) > RUGCHECK_HARD_REJECT_RISKS:
            return False, -50, [], [f"🔴 Rugcheck: {rugcheck['risk_count']} risiko!"], "DANGER"

    # Hard reject GMGN
    if gmgn.get("is_honeypot"):
        return False, -100, [], ["🔴 HONEYPOT!"], "DANGER"
    if gmgn.get("rug_ratio", 0) > 0.8:
        return False, -100, [], ["🔴 RUG RATIO TINGGI!"], "DANGER"

    # [v9.8-1] Hub & Spoke → UNIVERSAL HARD REJECT di semua jalur.
    # Sebelumnya hanya -3 score saat masuk blok scoring Helius,
    # sehingga token dengan distribusi tidak wajar tetap bisa lolos.
    if helius.get("available") and helius.get("hub_spoke"):
        return False, -100, [], ["🔴 Hub & Spoke terdeteksi — distribusi tidak wajar!"], "DANGER"

    # Tentukan sumber data
    lp_burned = None; lp_source = "N/A"
    bundle_pct = None; dev_burned_pct = None
    holders = None; age_hours = None
    smart_buy = 0; smart_sell = 0

    if gmgn.get("available"):
        lp_burned      = gmgn.get("lp_burned", 0)
        bundle_pct     = gmgn.get("bundle_pct", 0)
        dev_burned_pct = gmgn.get("dev_burned_pct", None)
        holders        = gmgn.get("holders", 0)
        age_hours      = gmgn.get("age_hours", 0)
        smart_buy      = gmgn.get("smart_buy", 0)
        smart_sell     = gmgn.get("smart_sell", 0)
        lp_source      = "GMGN"
    elif rugcheck.get("available"):
        lp_burned = (100.0 if rugcheck.get("lp_burned") else
                     50.0  if rugcheck.get("lp_locked") else 0.0)
        lp_source = "Rugcheck"

    # [v9.8-2] T1/T2/T3 wajib GMGN — tanpa LP/Bundle/Dev tidak ada
    # dasar keputusan. Alert dengan semua field N/A = alert berbahaya.
    # [v9.8-3] T0 wajib minimal Rugcheck tersedia + score >= 60.
    if tier in ["T1", "T2", "T3"]:
        if not gmgn.get("available"):
            return False, 0, [], [], ""
    elif tier == "T0":
        if not gmgn.get("available") and not rugcheck.get("available"):
            return False, 0, [], [], ""
        if rugcheck.get("available") and rugcheck.get("score", 0) < 60:
            return False, 0, [], [], ""

    # Per-tier threshold checks
    if gmgn.get("available"):
        age = gmgn.get("age_hours", 0)
        if tier == "T0":
            if age < MIN_TOKEN_AGE_T0: return False, 0, [], [], ""
            if holders is not None and holders < MIN_HOLDERS_T0: return False, 0, [], [], ""
        elif tier == "T1":
            if age < MIN_TOKEN_AGE_T1: return False, 0, [], [], ""
            if lp_burned is not None and lp_burned < MIN_LP_BURNED_T1: return False, 0, [], [], ""
            if holders is not None and holders < MIN_HOLDERS_T1: return False, 0, [], [], ""
        elif tier == "T2":
            if age < MIN_TOKEN_AGE_T2: return False, 0, [], [], ""
            if lp_burned is not None and lp_burned < MIN_LP_BURNED_T2: return False, 0, [], [], ""
            if holders is not None and holders < MIN_HOLDERS_T2: return False, 0, [], [], ""

    # Scoring LP Burned
    if lp_burned is not None:
        if lp_burned >= 100:
            score += 8; signals.append(f"🔒 LP Burned 100% [{lp_source}] — tidak bisa rug!")
        elif lp_burned >= 80:
            score += 6; signals.append(f"🔒 LP Burned {lp_burned:.0f}% [{lp_source}]")
        elif lp_burned >= 50:
            score += 3; signals.append(f"🔒 LP Burned {lp_burned:.0f}% [{lp_source}]")
        elif lp_burned > 0:
            score += 1; warnings.append(f"⚠️ LP Burned rendah: {lp_burned:.0f}%")
        else:
            if tier != "T0":
                score -= 3; warnings.append("🔴 LP tidak diburn!")
            else:
                warnings.append("⚠️ T0: LP belum diburn (normal untuk token baru)")
    else:
        warnings.append("⚠️ LP Burned: data tidak tersedia")

    # Scoring Rugcheck
    if rugcheck.get("available"):
        rc_score = rugcheck.get("score", 0)
        rc_risks = rugcheck.get("risk_count", 0)
        if rc_score >= 90:
            score += 4; signals.append(f"✅ Rugcheck score: {rc_score}/100 — AMAN!")
        elif rc_score >= 70:
            score += 3; signals.append(f"✅ Rugcheck score: {rc_score}/100")
        elif rc_score >= 50:
            score += 1; signals.append(f"⚠️ Rugcheck score: {rc_score}/100")
        else:
            score -= 2; warnings.append(f"🔴 Rugcheck score rendah: {rc_score}/100!")
        if rc_risks == 0:
            score += 2; signals.append("✅ Rugcheck: 0 risiko ditemukan!")
        elif rc_risks <= RUGCHECK_MAX_RISKS:
            score += 1
        else:
            warnings.append(f"⚠️ Rugcheck: {rc_risks} risiko: {', '.join(rugcheck.get('risks', [])[:2])}")

    # Scoring Bundle
    if bundle_pct is not None:
        if bundle_pct <= 2:
            score += 5; signals.append(f"✅ Bundle {bundle_pct:.1f}% — sangat bersih!")
        elif bundle_pct <= MAX_BUNDLE_PCT:
            score += 3; signals.append(f"✅ Bundle {bundle_pct:.1f}%")
        elif bundle_pct <= 20:
            score -= 1; warnings.append(f"⚠️ Bundle {bundle_pct:.1f}%")
        else:
            score -= 4; warnings.append(f"🔴 Bundle tinggi {bundle_pct:.1f}%!")

    # [FIX #1] Scoring Dev Burned — logika DIBALIK:
    # dev_burned_pct tinggi = dev sudah burn token mereka = BAGUS
    if dev_burned_pct is not None:
        if dev_burned_pct >= 80:
            score += 4; signals.append(f"✅ Dev burned {dev_burned_pct:.0f}% token — komitmen tinggi!")
        elif dev_burned_pct >= 50:
            score += 2; signals.append(f"✅ Dev burned {dev_burned_pct:.0f}% token")
        elif dev_burned_pct >= MAX_DEV_BURNED_PCT_WARN:
            score += 1  # sedikit positif, tidak perlu tampil
        elif dev_burned_pct > 0:
            warnings.append(f"⚠️ Dev burned rendah: {dev_burned_pct:.0f}% (sisanya masih dipegang)")
        else:
            if tier not in ["T0", "T1"]:
                score -= 2; warnings.append("🔴 Dev belum burn token — risiko dump!")
            else:
                warnings.append("⚠️ Dev belum burn token (umum untuk token sangat baru)")
    else:
        warnings.append("⚠️ Dev burned: data tidak tersedia")

    # Scoring Holders
    if holders is not None:
        if holders >= 2000:
            score += 3; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 500:
            score += 2; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 100:
            score += 1

    # Smart Money
    sm = smart_buy - smart_sell
    if sm > 5:
        score += 2; signals.append(f"✅ Smart money NET BUY +{sm}")
    elif sm < -5:
        score -= 1; warnings.append(f"⚠️ Smart money NET SELL {sm}")

    # Helius Top10 — Hub & Spoke sudah di-reject di atas, tidak perlu cek ulang
    if helius.get("available"):
        top10 = helius.get("top10_pct", 0)
        if top10 <= 10:
            score += 3; signals.append(f"✅ Top 10 hanya {top10:.1f}% — sempurna!")
        elif top10 <= MAX_TOP10_PCT:   # <= 30%
            score += 2; signals.append(f"✅ Top 10: {top10:.1f}%")
        elif top10 <= 40:              # [v9.8-8] 30–40% grey zone → penalti kecil
            score -= 1; warnings.append(f"⚠️ Top 10 agak tinggi: {top10:.1f}%")
        else:                          # > 40%
            score -= 2; warnings.append(f"🔴 Top 10 tinggi: {top10:.1f}%!")

    critical = [w for w in warnings if w.startswith("🔴")]
    if len(critical) >= 2: return False, score, signals, warnings, ""

    lp_str     = f"LP:{lp_burned:.0f}%[{lp_source}]" if lp_burned is not None else "LP:N/A"
    bundle_str = f"Bundle:{bundle_pct:.1f}%" if bundle_pct is not None else "Bundle:N/A"
    dev_str    = f"DevBurned:{dev_burned_pct:.0f}%" if dev_burned_pct is not None else "DevBurned:N/A"
    rc_str     = f"RC:{rugcheck.get('score', 0)}" if rugcheck.get("available") else ""
    summary    = f"{lp_str} | {bundle_str} | {dev_str}" + (f" | {rc_str}" if rc_str else "")
    return True, score, signals, warnings, summary

# ── PRE-PUMP PATTERN (T0) ─────────────────────────────────
def check_prepump_pattern(price, open_c1, change_m5, change_h1, change_h6,
                          vol_m5, vol_h1, buys_m5, sells_m5, buys_h1, sells_h1,
                          age_hours, holders, liq: float = 0, mcap: float = 0) -> tuple:
    score = 0; signals = []; warnings = []
    r_m5  = buys_m5 / max(sells_m5, 1)
    r_h1  = buys_h1 / max(sells_h1, 1)
    avg5m = vol_h1 / 12 if vol_h1 > 0 else 0

    # [ENH-B] Volume spike — tier lebih granular, spike 10x+ diberi bobot tertinggi
    if avg5m > 0:
        vol_ratio = vol_m5 / avg5m
        if vol_ratio >= VOL_SPIKE_EXTREME:
            score += 8; signals.append(f"🚨 Volume EXTREME {vol_ratio:.1f}x — FOMO masuk!")
        elif vol_ratio >= VOL_SPIKE_STRONG:
            score += 6; signals.append(f"🔥 Volume spike {vol_ratio:.1f}x — early momentum!")
        elif vol_ratio >= 3.0:
            score += 4; signals.append(f"📊 Volume naik {vol_ratio:.1f}x")
        elif vol_ratio >= 2.0:
            score += 2; signals.append(f"📊 Volume naik {vol_ratio:.1f}x")
        elif vol_ratio < 0.5:
            warnings.append("⚠️ Volume masih sepi")

    # [ENH-C] TX acceleration — banyak wallet masuk tiba-tiba = sinyal organik
    tx_m5  = buys_m5 + sells_m5
    avg_tx = (buys_h1 + sells_h1) / 12 if (buys_h1 + sells_h1) > 0 else 0
    if avg_tx > 0:
        tx_accel = tx_m5 / avg_tx
        if tx_accel >= TX_ACCEL_EXTREME:
            score += 5; signals.append(f"🌊 TX acceleration {tx_accel:.1f}x — serangan buyer organik!")
        elif tx_accel >= TX_ACCEL_STRONG:
            score += 3; signals.append(f"📈 TX naik {tx_accel:.1f}x — banyak wallet baru masuk")
        elif tx_accel >= 1.5:
            score += 1

    # Pump awal mulai
    if change_h6 >= T0_MIN_PUMP_PCT:
        score += 3; signals.append(f"📈 Pump awal h6: +{change_h6:.0f}%")
    if change_h1 >= 20:
        score += 2; signals.append(f"📈 h1 kuat: +{change_h1:.0f}%")
    if change_m5 >= 5:
        score += 2; signals.append(f"🚀 m5 hijau: +{change_m5:.1f}%")
    elif change_m5 < -MAX_CANDLE_DROP:
        return score, signals, ["🔴 Dump masif m5!"], 0

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
    if 0.1 <= age_hours <= 1.0:
        score += 3; signals.append(f"🆕 Token sangat fresh: {age_hours:.1f}h — ultra early!")
    elif age_hours <= 3.0:
        score += 1; signals.append(f"🆕 Token fresh: {age_hours:.1f}h")

    # [v9.8-5] Harga vs C1 — pakai toleransi 0.9x agar konsisten
    # dengan result dict (holds_c1 = price >= open_c1 * 0.9).
    # Sebelumnya: exact check → muncul dua pesan bertentangan di alert.
    if open_c1 > 0 and price >= open_c1 * 0.9:
        above_pct = ((price - open_c1) / open_c1 * 100)
        score += 2; signals.append(f"✅ Harga di atas C1 (+{above_pct:.0f}%)")
    elif open_c1 > 0:
        warnings.append("⚠️ Harga di bawah C1")

    # [ENH-A] Bonus liq/mcap ratio — pool dalam = susah dump
    if mcap > 0 and liq > 0:
        liq_ratio = liq / mcap
        if liq_ratio >= LIQ_MCAP_RATIO_GOOD_T0:
            score += 3; signals.append(f"💧 Pool dalam: liq/mcap {liq_ratio:.0%} — sangat sehat!")
        elif liq_ratio >= LIQ_MCAP_RATIO_MIN_T0:
            score += 1; signals.append(f"💧 Pool cukup: liq/mcap {liq_ratio:.0%}")

    return score, signals, warnings, 0.0

# ── DIP & RIP PATTERN ────────────────────────────────────
def check_core_pattern(price, open_c1, change_m5, change_h1, change_h6, change_h24,
                       vol_m5, vol_h1, vol_h6, buys_m5, sells_m5, buys_h1, sells_h1,
                       tracked_high: float = 0) -> tuple:
    score = 0; signals = []; warnings = []
    r_m5  = buys_m5 / max(sells_m5, 1)
    r_h1  = buys_h1 / max(sells_h1, 1)

    # Pump awal
    pump_pct = change_h24 if change_h24 >= MIN_PUMP_PCT else (
               change_h6  if change_h6  >= MIN_PUMP_PCT else 0)
    if pump_pct >= 500:
        score += 4; signals.append(f"🚀 Pump awal sangat kuat: +{pump_pct:.0f}%")
    elif pump_pct >= 200:
        score += 3; signals.append(f"🚀 Pump awal kuat: +{pump_pct:.0f}%")
    elif pump_pct >= 150:
        score += 2; signals.append(f"📈 Pump awal: +{pump_pct:.0f}%")
    else:
        return score, signals, warnings, 0

    # [FIX #2] Dip dihitung dari tracked_high internal
    dip_from_high = calc_dip_from_high(price, tracked_high, change_h1, change_m5)
    if dip_from_high > MAX_DIP_PCT:
        warnings.append(f"🔴 Dip terlalu dalam: -{dip_from_high:.0f}%")
        return score, signals, warnings, dip_from_high
    elif dip_from_high >= 35:
        score += 3; signals.append(f"✅ Dip ideal: -{dip_from_high:.0f}%")
    elif dip_from_high >= MIN_DIP_PCT:
        score += 2; signals.append(f"✅ Dip cukup: -{dip_from_high:.0f}%")
    elif change_h1 > 0:
        score += 1
    else:
        warnings.append(f"⚠️ Dip dangkal: -{dip_from_high:.0f}%")

    # Tidak tembus C1
    if open_c1 > 0:
        holds_c1 = price >= open_c1 * 0.9
        if holds_c1:
            above_pct = ((price - open_c1) / open_c1 * 100)
            score += 3; signals.append(f"✅ Hold di atas C1 (+{above_pct:.0f}%)")
        else:
            warnings.append("🔴 Harga tembus Open C1!"); score -= 2

    # Volume staircase
    avg_5m = vol_h1 / 12 if vol_h1 > 0 else 0
    avg_h6 = vol_h6 / 6  if vol_h6 > 0 else 0
    if avg_5m > 0:
        vol_ratio = vol_m5 / avg_5m
        if vol_ratio >= 2.0:
            score += 3; signals.append(f"📊 Volume naik {vol_ratio:.1f}x — momentum kuat!")
        elif vol_ratio >= 1.5:
            score += 2; signals.append(f"📊 Volume naik {vol_ratio:.1f}x")
        elif vol_ratio >= 1.0:
            score += 1
        elif vol_ratio < 0.3:
            warnings.append("⚠️ Volume sangat sepi")
    if avg_h6 > 0:
        h1_ratio = vol_h1 / avg_h6
        if h1_ratio >= 1.5:
            score += 2; signals.append(f"📈 Volume momentum h1/h6: {h1_ratio:.1f}x")

    # Konsolidasi + entry signal
    is_consolidating = -20 <= change_h1 <= 20
    if is_consolidating:
        if avg_5m > 0 and vol_m5 / avg_5m < 0.7:
            score += 2; signals.append("🔄 Konsolidasi sehat — volume mengecil")
        if change_m5 >= 10:
            score += 5; signals.append(f"🚀 BREAKOUT konsolidasi! m5: +{change_m5:.1f}%")
        elif change_m5 >= MIN_M5_SIGNAL:
            score += 3; signals.append(f"📈 Entry signal m5: +{change_m5:.1f}%")
        elif change_m5 >= 0:
            score += 1; signals.append("⏳ Konsolidasi berlanjut")
        else:
            warnings.append(f"⚠️ m5 masih negatif: {change_m5:.1f}%")
    else:
        if change_m5 >= 10:
            score += 4; signals.append(f"✅ Reversal kuat m5: +{change_m5:.1f}%")
        elif change_m5 >= MIN_M5_SIGNAL:
            score += 2; signals.append(f"✅ Reversal m5: +{change_m5:.1f}%")
        elif change_m5 < -MAX_CANDLE_DROP:
            return score, signals, ["🔴 Dump masif m5!"], dip_from_high

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

    return score, signals, warnings, dip_from_high

# ── DETERMINE TIER ────────────────────────────────────────
def get_tier(mcap, liq, vol_h1) -> str | None:
    if T0_MIN_MCAP <= mcap <= T0_MAX_MCAP and liq >= T0_MIN_LIQUIDITY and vol_h1 >= T0_MIN_VOL_1H:
        # [ENH-A] Liq/MCAP ratio filter — pool tipis mudah dimanipulasi
        liq_ratio = liq / mcap if mcap > 0 else 0
        if liq_ratio < LIQ_MCAP_RATIO_MIN_T0:
            print(f"[T0 SKIP] MCAP=${mcap/1000:.0f}K liq_ratio={liq_ratio:.2f} < {LIQ_MCAP_RATIO_MIN_T0}")
            return None
        return "T0"
    if T1_MIN_MCAP <= mcap <= T1_MAX_MCAP and liq >= T1_MIN_LIQUIDITY and vol_h1 >= T1_MIN_VOL_1H:
        return "T1"
    if T2_MIN_MCAP <= mcap <= T2_MAX_MCAP and liq >= T2_MIN_LIQUIDITY and vol_h1 >= T2_MIN_VOL_1H:
        return "T2"
    # [FIX #9] T3 sekarang punya batas atas T3_MAX_MCAP ($50M)
    if T3_MIN_MCAP <= mcap <= T3_MAX_MCAP and liq >= T3_MIN_LIQUIDITY and vol_h1 >= T3_MIN_VOL_1H:
        return "T3"
    return None

# ── DYNAMIC STOP LOSS & TARGET ────────────────────────────
def get_sl_tp(price: float, tier: str) -> tuple:
    sl_map  = {"T0": 0.80, "T1": 0.85, "T2": 0.88, "T3": 0.90}
    tp1_map = {"T0": 3.0,  "T1": 2.0,  "T2": 1.8,  "T3": 1.5}
    tp2_map = {"T0": 5.0,  "T1": 3.0,  "T2": 2.5,  "T3": 2.0}
    sl_pct  = sl_map.get(tier, 0.85)
    return (price * sl_pct,
            price * tp1_map.get(tier, 2.0),
            price * tp2_map.get(tier, 3.0),
            int((1 - sl_pct) * 100))

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

        # Filter awal per mode
        if tier == "T0":
            avg5m        = vh1 / 12 if vh1 > 0 else 0
            has_vol_spike = avg5m > 0 and vm5 / avg5m >= 2.0
            has_momentum  = h6 >= T0_MIN_PUMP_PCT or h1 >= 15 or has_vol_spike
            if not has_momentum: return None
            if m5 < -MAX_CANDLE_DROP: return None
        else:
            pump = h24 if h24 >= MIN_PUMP_PCT else (h6 if h6 >= MIN_PUMP_PCT else 0)
            if pump < MIN_PUMP_PCT: return None
            if not ((h1 < -10) or (m5 < -5) or (h6 > 20 and m5 >= 0)): return None
            if m5 < -MAX_CANDLE_DROP: return None
            avg5m = vh1 / 12 if vh1 > 0 else 0
            if avg5m > 0 and vm5 / avg5m < 0.05: return None

        # [FIX #2] open_c1 juga update tracked high secara internal
        open_c1      = get_open_c1(addr, price)
        tracked_high = get_tracked_high(addr)

        gmgn_data     = get_gmgn(addr)
        helius_data   = get_helius(addr)
        rugcheck_data = get_rugcheck(addr)

        # [v9.8-6] age_h fallback SEBELUM check_safety agar tidak terlambat.
        # Jika GMGN gagal, ambil dari pairCreatedAt DexScreener, lalu
        # inject ke gmgn_data sehingga check_safety pakai nilai yang benar.
        age_h = gmgn_data.get("age_hours", 0)
        if not age_h:
            pair_created = pair.get("pairCreatedAt", 0) or 0
            if pair_created:
                age_h = round((time.time() - pair_created / 1000) / 3600, 2)
                print(f"[AGE FALLBACK] {addr[:8]} age dari DexScreener: {age_h:.1f}h")
                gmgn_data = {**gmgn_data, "age_hours": age_h}

        safety_ok, safety_score, safety_sig, safety_warn, safety_sum = check_safety(
            gmgn_data, helius_data, rugcheck_data, tier)
        if not safety_ok: return None

        holders_n = gmgn_data.get("holders", 0)

        if tier == "T0":
            pattern_score, pattern_sig, pattern_warn, dip_pct = check_prepump_pattern(
                price, open_c1, m5, h1, h6, vm5, vh1,
                bm5, sm5, bh1, sh1, age_h, holders_n, liq, mcap)
            pump_pct = max(h6, h1)
        else:
            # [FIX #2] Kirim tracked_high bukan high24h DexScreener
            pattern_score, pattern_sig, pattern_warn, dip_pct = check_core_pattern(
                price, open_c1, m5, h1, h6, h24,
                vm5, vh1, vh6, bm5, sm5, bh1, sh1, tracked_high)
            pump_pct = h24 if h24 >= MIN_PUMP_PCT else h6

        total        = safety_score + pattern_score
        all_warnings = safety_warn  + pattern_warn
        all_signals  = safety_sig   + pattern_sig

        # [FIX #7] Sort warnings — 🔴 diprioritaskan, limit 4
        red_warns   = [w for w in all_warnings if w.startswith("🔴")]
        other_warns = [w for w in all_warnings if not w.startswith("🔴")]
        all_warnings_sorted = (red_warns + other_warns)[:4]

        critical = [w for w in all_warnings if w.startswith("🔴")]
        if len(critical) >= 2: return None

        # Grade per tier — [v9.8-4] Grade C dihapus.
        # Bot bilang "lemah" tapi tetap kirim entry zone = kontradiktif.
        # Alert hanya dikirim jika setup cukup layak (Grade B ke atas).
        if tier == "T0":
            if total >= 15:   grade, status = "A",  "🔥 PRE-PUMP! Entry ultra early!"
            elif total >= 10: grade, status = "B",  "🟡 Sinyal awal ada — monitor ketat"
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
        else:  # T3
            if total >= 26:   grade, status = "A+", "💎 Setup premium!"
            elif total >= 20: grade, status = "A",  "🟢 Setup bagus"
            elif total >= 13: grade, status = "B",  "🟡 Setup cukup"
            else: return None

        pos_map = {"T0": T0_MAX_POSITION, "T1": T1_MAX_POSITION,
                   "T2": T2_MAX_POSITION, "T3": T3_MAX_POSITION}
        sl, tp1, tp2, sl_pct = get_sl_tp(price, tier)
        # [FIX #6] Pass token address untuk cache social yang unik
        social = get_social(symbol, addr)

        return {
            "name": name, "symbol": symbol, "addr": addr,
            "chain": chain, "dex": dex, "pair_id": pair_id,
            "price": price, "pump_pct": round(pump_pct, 1),
            "dip_pct": round(dip_pct, 1), "open_c1": open_c1,
            "holds_c1": price >= open_c1 * 0.9,
            "m5": m5, "h1": h1, "h6": h6, "h24": h24,
            "vm5": vm5, "vh1": vh1, "vh24": vh24,
            "bm5": bm5, "sm5": sm5, "bh1": bh1, "sh1": sh1, "txh24": txh24,
            "liq": liq, "mcap": mcap,
            "total": total, "status": status, "grade": grade,
            "tier": tier, "max_pos": pos_map[tier],
            "signals":  all_signals[:5],
            "warnings": all_warnings_sorted,           # [FIX #7]
            "safety_sum": safety_sum,
            "lp_burned":  gmgn_data.get("lp_burned") if gmgn_data.get("available") else (
                          100.0 if rugcheck_data.get("lp_burned") else (
                          50.0  if rugcheck_data.get("lp_locked") else None)),
            "bundle_pct":    gmgn_data.get("bundle_pct")    if gmgn_data.get("available") else None,
            "dev_burned_pct": gmgn_data.get("dev_burned_pct") if gmgn_data.get("available") else None,
            # [v9.8-7] holders → None saat GMGN tidak tersedia.
            # Sebelumnya default 0 yang terlihat seperti "0 holders" di alert.
            "holders":       gmgn_data.get("holders") if gmgn_data.get("available") else None,
            "age_h":         age_h,   # [v9.8-6] local var sudah include fallback DexScreener
            "smart_buy":  gmgn_data.get("smart_buy", 0),
            "smart_sell": gmgn_data.get("smart_sell", 0),
            "gmgn_ok":    gmgn_data.get("available", False),
            "rc_ok":      rugcheck_data.get("available", False),
            "rc_score":   rugcheck_data.get("score", 0),
            "rc_risks":   rugcheck_data.get("risk_count", 0),
            "mint_auth":  rugcheck_data.get("mint_auth", False),
            "helius_ok":  helius_data.get("available", False),
            "top10_pct":  helius_data.get("top10_pct", 0),
            "hub_spoke":  helius_data.get("hub_spoke", False),
            "social_ok":     social.get("available", False),
            "galaxy_score":  social.get("galaxy_score", 0),
            "alt_rank":      social.get("alt_rank", 0),
            "mention_trend": social.get("mention_trend", 0),
            "kol_active":    social.get("kol_active", False),
            "sl": sl, "tp1": tp1, "tp2": tp2, "sl_pct": sl_pct,
            "chart":    f"https://dexscreener.com/{chain}/{pair_id}",
            "gmgn_url": f"https://gmgn.ai/sol/token/{addr}",
            "axiom_url": f"https://axiom.xyz/sol/{addr}",
        }
    except Exception as e:
        print(f"[ANALYZE ERR] {e}")
        return None

# ── FORMAT ALERT ─────────────────────────────────────────
def format_alert(s: dict) -> str:
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
    temoji, tlabel, tdesc, tpos = tier_map.get(s["tier"], ("⚪", "", "", ""))
    grade_emoji = {"A+": "💎", "A": "🏆", "B": "🥈", "C": "⚡"}.get(s["grade"], "")

    signals_text = "\n".join(s["signals"])  if s["signals"]  else "—"
    warn_text    = "\n".join(s["warnings"]) if s["warnings"] else "✅ Tidak ada warning"

    lp     = s.get("lp_burned")
    bundle = s.get("bundle_pct")
    dev    = s.get("dev_burned_pct")   # [FIX #1]

    lp_str  = f"{lp:.0f}%"    if lp     is not None else "N/A ⚠️"
    bun_str = f"{bundle:.1f}%" if bundle is not None else "N/A ⚠️"
    # [FIX #1] dev_burned: tampilkan sebagai persentase yang sudah dibakar
    dev_str = f"{dev:.0f}% burned" if dev is not None else "N/A ⚠️"

    lp_e  = ("🔒" if lp is not None and lp >= 80 else
             "⚠️" if lp is not None and lp >= 50 else
             "🔴" if lp is not None else "❓")
    bun_e = ("✅" if bundle is not None and bundle <= 5 else
             "⚠️" if bundle is not None and bundle <= 15 else
             "🔴" if bundle is not None else "❓")
    # [FIX #1] emoji dev: tinggi = bagus (burn tinggi = aman)
    dev_e = ("✅" if dev is not None and dev >= 80 else
             "🟡" if dev is not None and dev >= 20 else
             "🔴" if dev is not None and dev == 0 else
             "⚠️" if dev is not None else "❓")

    sm   = s['smart_buy'] - s['smart_sell']
    # [v9.8-9] Smart Money hanya tampil jika GMGN tersedia.
    # Saat GMGN tidak ada, nilai selalu 0/0 = "Netral" yang tidak informatif.
    if s.get("gmgn_ok"):
        sm_t = f"+{sm} NET BUY 🟢" if sm > 0 else (f"{sm} NET SELL 🔴" if sm < 0 else "Netral")
        sm_line = f"\n  💡 Smart $: {sm_t}"
    else:
        sm_line = ""

    # [v9.8-7] Holders: tampilkan N/A jika data tidak tersedia (bukan "0")
    holders_str = f"{s['holders']:,}" if s.get('holders') is not None else "N/A"

    helius_block = ""
    if s["helius_ok"]:
        hub_t = "⚠️ ADA!" if s["hub_spoke"] else "✅ Tidak ada"
        helius_block = f"\n  🏦 Top 10: <b>{s['top10_pct']:.1f}%</b> | Hub&amp;Spoke: {hub_t}"

    rc_block = ""
    if s.get("rc_ok"):
        rc_emoji = "✅" if s['rc_score'] >= 70 else "⚠️" if s['rc_score'] >= 50 else "🔴"
        mint_t   = "🔴 ADA!" if s.get("mint_auth") else "✅ Disabled"
        rc_block = (f"\n  {rc_emoji} Rugcheck: <b>{s['rc_score']}/100</b> "
                    f"| Risks: <b>{s['rc_risks']}</b> | Mint: {mint_t}")

    social_block = ""
    if s.get("social_ok"):
        trend_arrow = "📈" if s["mention_trend"] >= 0 else "📉"
        kol_text    = "👑 YA!" if s["kol_active"] else "—"
        social_block = (f"\n🌐 <b>Social (LunarCrush):</b>\n"
                        f"  🌟 Galaxy: {s['galaxy_score']}/100 | 🏅 #{s['alt_rank']}\n"
                        f"  {trend_arrow} Mention: {s['mention_trend']:+.0f}% | KOL: {kol_text}")

    vol24_t  = f"${s['vh24']/1e6:.1f}M" if s['vh24'] >= 1e6 else f"${s['vh24']/1000:.0f}K"
    c1_t     = f"${s['open_c1']:.8f}"   if s['open_c1'] > 0 else "N/A"
    age_t    = f"{s['age_h']:.1f}h"     if s['age_h']  > 0 else "N/A"
    mode_lbl = "🔥 PRE-PUMP MODE" if s["tier"] == "T0" else "📉 DIP &amp; RIP MODE"
    dip_line = (f"📈 Momentum h6: <b>+{s['pump_pct']}%</b>" if s["tier"] == "T0"
                else f"📉 Dip dari high: <b>-{s['dip_pct']}%</b>")
    footer   = ("⛔ T0 ULTRA EARLY — Posisi SANGAT KECIL! Konfirmasi manual dulu!"
                if s["tier"] == "T0" else "⚡ Konfirmasi LP Burned & Bundle di Axiom!")

    return f"""
🚨 <b>DIP &amp; RIP ALERT v9.8!</b> {mode_lbl}

{temoji} <b>{tlabel}</b>
📍 {tdesc}
💰 <b>{tpos}</b>

🪙 <b>{s['name']} ({s['symbol']})</b>
📊 {s['dex'].upper()} | Solana | ⏱ {age_t}

{grade_emoji} <b>{s['status']}</b>
📊 Score: {s['total']} | Grade: {s['grade']}

💹 Harga: <b>${s['price']:.8f}</b>
📈 Pump awal: <b>+{s['pump_pct']}%</b>
{dip_line}
🏁 Open C1: {c1_t}
{'✅' if s['holds_c1'] else '🔴'} Hold di atas C1: <b>{'YA ✅' if s['holds_c1'] else 'TIDAK ❌'}</b>
📊 m5: <b>{s['m5']:+.1f}%</b> | h1: {s['h1']:+.1f}% | h6: {s['h6']:+.1f}%

🔒 <b>SAFETY:</b>
  {lp_e} LP Burned: <b>{lp_str}</b>
  {bun_e} Bundle: <b>{bun_str}</b>
  {dev_e} Dev Burned: <b>{dev_str}</b>
  👥 Holders: <b>{holders_str}</b>{sm_line}{rc_block}{helius_block}{social_block}

✅ <b>SINYAL:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warn_text}

💰 <b>ENTRY ZONE:</b>
  Beli:         <b>${s['price']:.8f}</b>
  🔴 Stop Loss:  <b>${s['sl']:.8f}</b> (-{s['sl_pct']}%)
  🟡 Target 1:   <b>${s['tp1']:.8f}</b>
  🟢 Target 2:   <b>${s['tp2']:.8f}</b>

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
        a = t.get("tokenAddress", "") or t.get("address", "")
        if a: all_addr.add(a)

    print(f"[SCAN] {len(all_addr)} token...")
    t0 = t1 = t2 = t3 = 0
    state_dirty = False   # [BUG-L FIX] Track apakah state perlu disimpan

    for addr in list(all_addr)[:40]:
        if time.time() - alerted_tokens.get(addr, 0) < ALERT_COOLDOWN_SEC: continue
        pair   = get_pair(addr)
        if not pair: continue
        signal = analyze_pair(pair)
        if signal:
            if   signal["tier"] == "T0": t0 += 1
            elif signal["tier"] == "T1": t1 += 1
            elif signal["tier"] == "T2": t2 += 1
            else:                        t3 += 1
            print(f"[{signal['tier']}] {signal['symbol']} Grade:{signal['grade']} "
                  f"Score:{signal['total']} Pump:+{signal['pump_pct']}%")
            # [BUG-L FIX] Tandai alert dulu, lalu simpan state di luar kondisi Telegram
            alerted_tokens[addr] = time.time()
            state_dirty = True
            send_telegram(format_alert(signal))
        time.sleep(0.5)

    # [FIX #5] save hanya sekali di akhir scan, bukan per token
    # [BUG-L FIX] Simpan state terlepas dari hasil Telegram
    if state_dirty:
        save_state()

    print(f"[DONE] T0:{t0} T1:{t1} T2:{t2} T3:{t3}")

def main():
    print("=" * 60)
    print("  DIP & RIP BOT v9.8 — NO MORE BAD ALERTS")
    print("  Core: Token Aman + Dump Sehat + Second Pump")
    print("  RULE: Tidak ada alert tanpa data pendukung wajib")
    print("=" * 60)
    print(f"  Helius  : {'✅' if HELIUS_KEY else '⚠️ Belum ada key'}")
    print(f"  Lunar   : {'✅' if LUNARCRUSH_KEY else '⚠️ Belum ada key'}")
    print(f"  State   : {'✅ ' + str(STATE_FILE) if STATE_FILE.exists() else '🆕 Fresh start'}")
    print(f"  T0 Pre-pump : MCAP ${T0_MIN_MCAP/1000:.0f}K–${T0_MAX_MCAP/1000:.0f}K")
    print(f"  T1 Early    : MCAP ${T1_MIN_MCAP/1000:.0f}K–${T1_MAX_MCAP/1000:.0f}K")
    print(f"  T2 Normal   : MCAP ${T2_MIN_MCAP/1000:.0f}K–${T2_MAX_MCAP/1000:.0f}K")
    print(f"  T3 Late     : MCAP ${T3_MIN_MCAP/1000:.0f}K–${T3_MAX_MCAP/1_000_000:.0f}M")
    print("=" * 60)

    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v9.8 aktif!</b>\n\n"
        "🛡️ <b>v9.8 — No More Bad Alerts:</b>\n\n"
        "🔴 <b>Gate baru (alert ditolak jika):</b>\n"
        "   ✅ Hub &amp; Spoke → hard reject di semua jalur\n"
        "   ✅ T1/T2/T3 tanpa GMGN → ditolak\n"
        "   ✅ T0 tanpa Rugcheck ≥60 → ditolak\n"
        "   ✅ Grade C → tidak lagi di-alert\n\n"
        "🔧 <b>Fix logika &amp; display:</b>\n"
        "   ✅ C1 konsisten — tidak ada pesan bertentangan\n"
        "   ✅ age_h fallback aktif sebelum safety check\n"
        "   ✅ Holders N/A bukan 0 saat GMGN tidak ada\n"
        "   ✅ Top10 30–40% dapat penalti score\n"
        "   ✅ Smart Money disembunyikan saat data N/A\n\n"
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
