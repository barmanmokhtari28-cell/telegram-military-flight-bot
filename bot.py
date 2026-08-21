import os
import json
import traceback
from datetime import datetime, timezone, timedelta

import requests
from playwright.sync_api import sync_playwright

# ==================================================================
# CONFIG
# ==================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Regional bounding box -- narrowed to Iran + the Gulf Arab states
# (Saudi Arabia, UAE, Qatar, Bahrain, Kuwait, Oman)
LAT_MIN, LAT_MAX = 16.0, 39.0
LON_MIN, LON_MAX = 44.0, 63.0

# Grid of search points tiling Iran + the Gulf Arab countries (each
# point-search call only covers a ~250nm radius circle)
REGION_POINTS = [
    ("Tehran / Central Iran", 35.7, 51.4),
    ("Strait of Hormuz / S. Iran", 26.5, 56.0),
    ("SW Iran / N. Persian Gulf", 30.0, 49.0),
    ("E. Iran", 32.0, 60.5),
    ("Saudi Arabia / Eastern Province", 26.0, 50.0),
    ("UAE / Qatar / Bahrain", 25.0, 54.0),
    ("Kuwait", 29.3, 47.9),
    ("Oman / S. Hormuz approach", 23.5, 58.5),
]
POINT_RADIUS_NM = 250

# Sub-box used for the civilian "mass diversion" anomaly baseline --
# the Hormuz / Iranian Gulf corridor, the highest-signal area for
# airlines quietly rerouting away from Iranian airspace.
HORMUZ_LAT_MIN, HORMUZ_LAT_MAX = 24.0, 30.0
HORMUZ_LON_MIN, HORMUZ_LON_MAX = 53.0, 60.0

# Center point / zoom used for the regional overview screenshot
OVERVIEW_LAT, OVERVIEW_LON, OVERVIEW_ZOOM = 26.0, 54.0, 6

STATE_FILE = "state.json"
LEGACY_STATE_FILE = "seen_flights.json"  # from the old version of this bot

# How long a given aircraft can go "unseen" before it's eligible to be
# re-announced (prevents a loitering aircraft from re-alerting every
# 10 minutes, while still allowing a repeat appearance days later).
REANNOUNCE_AFTER_HOURS = 8

# ==================================================================
# AIRCRAFT TYPE / CATEGORY DATA
# ==================================================================

AIRCRAFT_NAMES = {
    "C17": "C-17A Globemaster III", "C130": "C-130 Hercules", "C30J": "C-130J Super Hercules",
    "C5": "C-5M Super Galaxy", "K35R": "KC-135 Stratotanker", "KC135": "KC-135 Stratotanker",
    "KC10": "KC-10 Extender", "KC46": "KC-46 Pegasus", "A332": "A330 MRTT Tanker",
    "A400": "A400M Atlas", "P8": "P-8A Poseidon ISR", "RC135": "RC-135 Rivet Joint",
    "E3TF": "E-3 Sentry AWACS", "E3": "E-3 Sentry AWACS", "E8": "E-8 Joint STARS",
    "E2": "E-2 Hawkeye", "IL76": "Ilyushin Il-76 Cargo", "AN124": "Antonov An-124 Heavy Cargo",
    "RQ4": "RQ-4 Global Hawk Drone", "MQ9": "MQ-9 Reaper Drone", "VC25": "Air Force One (VC-25)",
    "C32": "Boeing C-32 VIP", "C40": "Boeing C-40 Clipper",
    "B52": "B-52H Stratofortress", "B1": "B-1B Lancer", "B2": "B-2 Spirit",
    "F15": "F-15 Eagle/Strike Eagle", "F16": "F-16 Fighting Falcon", "F22": "F-22 Raptor",
    "F35": "F-35 Lightning II", "F18": "F/A-18 Hornet", "A10": "A-10 Thunderbolt II",
}
TARGET_TYPES = list(AIRCRAFT_NAMES.keys())

CATEGORY_BY_TYPE = {}
for t in ["K35R", "KC135", "KC10", "KC46", "A332", "A400"]:
    CATEGORY_BY_TYPE[t] = "TANKER"
for t in ["C17", "C130", "C30J", "C5", "IL76", "AN124"]:
    CATEGORY_BY_TYPE[t] = "CARGO"
for t in ["P8", "RC135", "E3TF", "E3", "E8", "E2", "RQ4", "MQ9"]:
    CATEGORY_BY_TYPE[t] = "ISR"
for t in ["VC25", "C32", "C40"]:
    CATEGORY_BY_TYPE[t] = "VIP"
for t in ["B52", "B1", "B2", "F15", "F16", "F22", "F35", "F18", "A10"]:
    CATEGORY_BY_TYPE[t] = "COMBAT"

MIL_CALLSIGNS = [
    "RCH", "REACH", "DUKE", "PAT", "EVAC", "CNV", "TOPCAT", "SNOOP",
    "JAKE", "SAM", "SPAR", "TEAL", "GOLD", "CLEAN", "NAVY", "EXEC",
    "DOOM", "HOSER", "TUF", "BOEING", "CMB", "CAMBER", "GTI", "CKS",
    "NATO", "RRR", "CTM", "IAF", "LAGR", "NCHO", "FORTE", "HOMER",
]

now_utc = lambda: datetime.now(timezone.utc)


# ==================================================================
# STATE
# ==================================================================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # migrate the old seen_flights.json format if present, so a prior
    # deployment doesn't re-announce everything on first run
    seen = []
    if os.path.exists(LEGACY_STATE_FILE):
        try:
            with open(LEGACY_STATE_FILE, "r") as f:
                legacy = json.load(f)
            seen = [{"key": k, "ts": now_utc().isoformat()} for k in legacy]
        except Exception:
            pass

    return {
        "seen_flights": seen,
        "mil_snapshot_log": [],
        "civil_corridor_log": [],
        "last_escalation_level": "LOW",
        "last_fallback_post_ts": None,
    }


def save_state(state):
    cutoff = now_utc() - timedelta(hours=48)

    state["seen_flights"] = [
        s for s in state["seen_flights"]
        if datetime.fromisoformat(s["ts"]) > now_utc() - timedelta(hours=REANNOUNCE_AFTER_HOURS)
    ][-1000:]

    state["mil_snapshot_log"] = [
        s for s in state["mil_snapshot_log"] if datetime.fromisoformat(s["ts"]) > cutoff
    ][-500:]

    state["civil_corridor_log"] = [
        s for s in state["civil_corridor_log"] if datetime.fromisoformat(s["ts"]) > (now_utc() - timedelta(days=14))
    ][-2000:]

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_recently_seen(state, flight_key):
    return any(s["key"] == flight_key for s in state["seen_flights"])


def mark_seen(state, flight_key):
    state["seen_flights"].append({"key": flight_key, "ts": now_utc().isoformat()})


# ==================================================================
# DATA FETCH
# ==================================================================

def fetch_adsb_data():
    aircraft = []
    headers = {"User-Agent": "OSINT-Flight-Tracker/1.0"}

    try:
        res = requests.get("https://api.airplanes.live/v2/mil", headers=headers, timeout=10)
        if res.status_code == 200:
            aircraft.extend(res.json().get("ac", []))
    except Exception as e:
        print(f"[ Warn ] Global mil endpoint failed: {e}")

    for label, lat, lon in REGION_POINTS:
        try:
            res = requests.get(
                f"https://api.adsb.one/v2/point/{lat}/{lon}/{POINT_RADIUS_NM}",
                headers=headers, timeout=10,
            )
            if res.status_code == 200:
                aircraft.extend(res.json().get("ac", []))
        except Exception as e:
            print(f"[ Warn ] Point search failed for {label}: {e}")

    unique_ac = {}
    for ac in aircraft:
        hex_code = ac.get("hex")
        if hex_code and hex_code not in unique_ac:
            unique_ac[hex_code] = ac

    return list(unique_ac.values())


def get_plane_photo(icao: str):
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{icao}"
        headers = {"User-Agent": "OSINT-Flight-Bot/1.0"}
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("photos"):
            return res["photos"][0]["thumbnail_large"]["src"]
    except Exception:
        pass
    return None


# ==================================================================
# SCREENSHOTS (Playwright)
#
# NOTE: the original version of this bot used wait_until="networkidle",
# which never fires on a live radar page (it's constantly polling in
# the background), so every screenshot call silently timed out and
# returned nothing. Fixed here by waiting for DOM content + a fixed
# render delay instead of waiting for network silence that never comes.
# ==================================================================

def _screenshot(map_url: str, filename: str, render_wait_ms: int = 7000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(map_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(render_wait_ms)  # let tiles + markers render
            page.screenshot(path=filename)
            browser.close()
            return filename
    except Exception as e:
        print(f"[ Error ] Screenshot failed for {map_url}: {e}")
        return None


def capture_map_screenshot(icao: str) -> str:
    map_url = f"https://globe.airplanes.live/?icao={icao.lower()}"
    print(f"[ Playwright ] Capturing flight track map for {icao}...")
    return _screenshot(map_url, f"map_flight_{icao}.png", render_wait_ms=6000)


def capture_regional_overview_map() -> str:
    map_url = f"https://globe.airplanes.live/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}"
    print("[ Playwright ] Capturing Iran + Gulf regional overview map...")
    return _screenshot(map_url, "regional_overview_iran_gulf.png", render_wait_ms=8000)


def cleanup_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


# ==================================================================
# CLASSIFICATION
# ==================================================================

def in_region(lat, lon) -> bool:
    return lat is not None and lon is not None and LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def in_hormuz_corridor(lat, lon) -> bool:
    return (lat is not None and lon is not None
            and HORMUZ_LAT_MIN <= lat <= HORMUZ_LAT_MAX and HORMUZ_LON_MIN <= lon <= HORMUZ_LON_MAX)


def is_target_military(aircraft: dict) -> bool:
    icao = str(aircraft.get("hex", "")).lower().strip()
    callsign = str(aircraft.get("flight", "")).upper().strip()
    typecode = str(aircraft.get("t", "")).upper().strip()
    db_flags = aircraft.get("dbFlags", 0)

    if icao.startswith("ae") or icao.startswith("af") or db_flags == 1:
        return True
    if typecode in TARGET_TYPES:
        return True
    if any(callsign.startswith(prefix) for prefix in MIL_CALLSIGNS):
        return True
    return False


def classify_type(typecode: str):
    return CATEGORY_BY_TYPE.get(typecode.upper().strip())


def determine_airspace_sector(lat, lon) -> str:
    if lat is None or lon is None:
        return "🌐 Iran / Gulf Strategic Airspace"
    if HORMUZ_LAT_MIN <= lat <= HORMUZ_LAT_MAX and HORMUZ_LON_MIN <= lon <= HORMUZ_LON_MAX:
        return "🌊 Strait of Hormuz & Persian Gulf Maritime Corridor"
    elif 25.0 <= lat <= 39.0 and 45.0 <= lon <= 63.0:
        return "🇮🇷 Iranian Airspace & Central Sector"
    elif 22.0 <= lat <= 27.0 and 46.0 <= lon <= 52.0:
        return "🇸🇦 Saudi Arabia / Eastern Gulf Sector"
    elif 22.5 <= lat <= 26.5 and 50.5 <= lon <= 56.5:
        return "🇦🇪🇶🇦🇧🇭 UAE / Qatar / Bahrain Sector"
    elif 28.5 <= lat <= 30.5 and 46.5 <= lon <= 49.0:
        return "🇰🇼 Kuwait Sector"
    elif 16.0 <= lat <= 26.5 and 51.5 <= lon <= 60.0:
        return "🇴🇲 Oman / S. Hormuz Approach Sector"
    else:
        return "🌐 Iran / Gulf Regional Airspace Sector"


def get_exact_aircraft_title(typecode: str, callsign: str) -> str:
    typecode = typecode.upper().strip()
    callsign = callsign.upper().strip()
    exact_name = AIRCRAFT_NAMES.get(typecode, typecode if typecode else "Military Aircraft")
    if callsign and callsign != "N/A":
        return f"🚨 {exact_name} ({callsign}) DETECTED 🚨"
    return f"🚨 {exact_name} DETECTED 🚨"


# ==================================================================
# ESCALATION SCORING
# ==================================================================

ESCALATION_BANDS = [
    (85, "CRITICAL", "🔴"), (65, "HIGH", "🟠"), (40, "ELEVATED", "🟡"),
    (20, "GUARDED", "🔵"), (0, "LOW", "🟢"),
]


def escalation_label(score):
    for threshold, label, emoji in ESCALATION_BANDS:
        if score >= threshold:
            return label, emoji
    return "LOW", "🟢"


def compute_escalation(state, current_mil_snapshot, current_civil_corridor_count):
    score = 0
    factors = []

    one_hour_ago = now_utc() - timedelta(hours=1)
    day_ago = now_utc() - timedelta(hours=24)
    recent_log = [s for s in state["mil_snapshot_log"] if datetime.fromisoformat(s["ts"]) > day_ago]
    baseline_counts = [s["count"] for s in recent_log if datetime.fromisoformat(s["ts"]) <= one_hour_ago]
    baseline = (sum(baseline_counts) / len(baseline_counts)) if baseline_counts else 0
    current_count = len(current_mil_snapshot)

    if current_count >= 5 and (baseline < 1 or current_count >= baseline * 2.5):
        score += 30
        factors.append(f"Military traffic spike: {current_count} active vs baseline ~{baseline:.1f}")
    elif current_count >= 8:
        score += 20
        factors.append(f"High absolute military volume: {current_count} active aircraft in region")

    categories_present = set()
    for ac in current_mil_snapshot:
        cat = classify_type(str(ac.get("t", "")).upper().strip())
        if cat:
            categories_present.add(cat)

    if "VIP" in categories_present:
        score += 40
        factors.append("VIP / high-value government aircraft airborne in region")

    if "TANKER" in categories_present and "COMBAT" in categories_present:
        score += 30
        factors.append("Tanker + combat aircraft together (possible strike package / escort posture)")

    isr_count = sum(1 for ac in current_mil_snapshot if classify_type(str(ac.get("t", "")).upper().strip()) == "ISR")
    if isr_count >= 2:
        score += 15
        factors.append(f"ISR clustering: {isr_count} reconnaissance/surveillance aircraft active simultaneously")

    cargo_count = sum(1 for ac in current_mil_snapshot if classify_type(str(ac.get("t", "")).upper().strip()) == "CARGO")
    if cargo_count >= 3:
        score += 10
        factors.append(f"Airlift surge: {cargo_count} military cargo aircraft active simultaneously")

    civil_baseline_counts = [s["count"] for s in state["civil_corridor_log"]
                              if datetime.fromisoformat(s["ts"]) <= one_hour_ago]
    civil_baseline = (sum(civil_baseline_counts) / len(civil_baseline_counts)) if civil_baseline_counts else 0

    if civil_baseline >= 8 and current_civil_corridor_count <= civil_baseline * 0.4:
        score += 25
        factors.append(
            f"Possible airline avoidance of Hormuz/Gulf corridor: {current_civil_corridor_count} civil "
            f"flights vs baseline ~{civil_baseline:.1f} (airlines may be rerouting due to tension)"
        )

    score = min(score, 100)
    label, emoji = escalation_label(score)
    return score, label, emoji, factors, baseline, current_count


# ==================================================================
# TELEGRAM
# ==================================================================

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }
    try:
        return requests.post(url, data=payload, timeout=15).json()
    except Exception as e:
        print(f"[ Error ] sendMessage failed: {e}")
        return None


def send_telegram_photo_file(photo_path, caption):
    """Sends a single locally-saved screenshot as a photo message."""
    if not photo_path or not os.path.exists(photo_path):
        return send_telegram_message(caption)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
            res = requests.post(url, data=data, files=files, timeout=30).json()
        if not res.get("ok"):
            print(f"[ Warn ] sendPhoto (file) failed ({res}), falling back to text")
            return send_telegram_message(caption)
        return res
    except Exception as e:
        print(f"[ Error ] sendPhoto (file) failed: {e}, falling back to text")
        return send_telegram_message(caption)


def send_telegram_media_group(caption, photo_paths):
    """Sends a gallery of local screenshots (regional overview + flight
    track) as a single Telegram media group."""
    valid_paths = [p for p in photo_paths if p and os.path.exists(p)]
    if not valid_paths:
        return send_telegram_message(caption)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media = []
    files = {}
    opened = []
    try:
        for i, path in enumerate(valid_paths):
            file_key = f"photo_{i}"
            fh = open(path, "rb")
            opened.append(fh)
            files[file_key] = fh
            item = {"type": "photo", "media": f"attach://{file_key}"}
            if i == 0:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media.append(item)

        payload = {"chat_id": TELEGRAM_CHANNEL_ID, "media": json.dumps(media)}
        res = requests.post(url, data=payload, files=files, timeout=30).json()
        if not res.get("ok"):
            print(f"[ Warn ] sendMediaGroup failed ({res}), falling back to text")
            return send_telegram_message(caption)
        return res
    except Exception as e:
        print(f"[ Error ] sendMediaGroup failed: {e}, falling back to text")
        return send_telegram_message(caption)
    finally:
        for fh in opened:
            fh.close()


def emoji_line(emoji, score, label):
    return f"{emoji} <b>Regional escalation index:</b> <code>{score}/100 ({label})</code>\n\n"


def post_detection(ac, sector, escalation_score, escalation_label_str, escalation_emoji, overview_path):
    icao = str(ac.get("hex", "")).strip()
    callsign = str(ac.get("flight", "N/A")).strip() or "N/A"
    typecode = str(ac.get("t", "Unknown Type")).strip()
    alt = ac.get("alt_baro", "N/A")
    speed = ac.get("gs", "N/A")
    lat, lon = ac.get("lat"), ac.get("lon")
    title_header = get_exact_aircraft_title(typecode, callsign)

    photo_url = get_plane_photo(icao)
    photo_link = f"📸 <a href='{photo_url}'>مشاهده عکس هواپیما</a>\n" if photo_url else ""
    flight_map = capture_map_screenshot(icao)

    caption = (
        f"<b>{title_header}</b>\n"
        f"📍 <b>Sector:</b> <i>{sector}</i>\n\n"
        f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
        f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
        f"🛩️ <b>Type:</b> <code>{typecode}</code>\n"
        f"📈 <b>Altitude:</b> <code>{alt} ft</code> | 💨 <b>Speed:</b> <code>{speed} kts</code>\n"
        f"🗺️ <b>Coordinates:</b> <code>{lat}, {lon}</code>\n\n"
        f"{photo_link}"
        f"{emoji_line(escalation_emoji, escalation_score, escalation_label_str)}"
        f"🔗 <a href='https://globe.airplanes.live/?icao={icao}'>ردیابی زنده رادار</a>\n"
        f"✈️ @secretollah"
    )

    photo_list = [p for p in [overview_path, flight_map] if p]
    res = send_telegram_media_group(caption, photo_list) if photo_list else send_telegram_message(caption)
    cleanup_file(flight_map)
    return res


def post_escalation_change(old_label, new_label, score, emoji, factors, overview_path):
    direction = "🔺 ESCALATION" if _band_rank(new_label) > _band_rank(old_label) else "🔻 DE-ESCALATION"
    factor_text = "\n".join(f"• {f}" for f in factors) if factors else "• No specific contributing factors logged"
    text = (
        f"{emoji} <b>{direction} LEVEL CHANGE</b>\n\n"
        f"Regional posture shifted: <b>{old_label} → {new_label}</b>\n"
        f"<b>Escalation index:</b> <code>{score}/100</code>\n\n"
        f"<b>Contributing factors:</b>\n{factor_text}\n\n"
        f"✈️ @secretollah"
    )
    return send_telegram_photo_file(overview_path, text)


def post_civil_anomaly(current_count, baseline, overview_path):
    text = (
        f"🟡 <b>CIVIL TRAFFIC ANOMALY — Hormuz/Gulf Corridor</b>\n\n"
        f"Civilian flight volume in the Strait of Hormuz / Persian Gulf corridor has dropped to "
        f"<code>{current_count}</code> aircraft, versus a recent baseline of ~<code>{baseline:.1f}</code>.\n"
        f"This can indicate airlines proactively rerouting away from the area due to perceived risk.\n\n"
        f"🔗 <a href='https://globe.airplanes.live/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}'>ردیابی زنده رادار</a>\n"
        f"✈️ @secretollah"
    )
    return send_telegram_photo_file(overview_path, text)


def post_heartbeat(total_scanned, mil_count, civil_corridor_count, escalation_score, label, emoji, overview_path):
    text = (
        f"🌐 <b>IRAN / GULF AIRSPACE STATUS</b>\n\n"
        f"ℹ️ <i>Routine interval check — no new military detections this cycle.</i>\n"
        f"📊 <b>Total regional aircraft monitored:</b> <code>{total_scanned}</code>\n"
        f"🪖 <b>Active military aircraft:</b> <code>{mil_count}</code>\n"
        f"🌊 <b>Civil traffic — Hormuz/Gulf corridor:</b> <code>{civil_corridor_count}</code>\n\n"
        f"{emoji_line(emoji, escalation_score, label)}"
        f"🔗 <a href='https://globe.airplanes.live/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}'>ردیابی زنده رادار</a>\n"
        f"✈️ @secretollah"
    )
    return send_telegram_photo_file(overview_path, text)


def _band_rank(label):
    order = ["LOW", "GUARDED", "ELEVATED", "HIGH", "CRITICAL"]
    return order.index(label) if label in order else 0


# ==================================================================
# MAIN
# ==================================================================

def run_tracker():
    print("==================================================")
    print("   OSINT Sky Radar — Iran & Gulf Arab States       ")
    print("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[ Error ] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID missing!")
        return

    state = load_state()
    aircraft_list = fetch_adsb_data()

    valid_flights = [ac for ac in aircraft_list if in_region(ac.get("lat"), ac.get("lon"))]
    total_scanned = len(valid_flights)
    print(f"[ Scan ] Evaluated {total_scanned} active regional aircraft.")

    current_mil_snapshot = [ac for ac in valid_flights if is_target_military(ac)]
    civil_corridor_flights = [
        ac for ac in valid_flights
        if not is_target_military(ac) and in_hormuz_corridor(ac.get("lat"), ac.get("lon"))
    ]

    state["mil_snapshot_log"].append({"ts": now_utc().isoformat(), "count": len(current_mil_snapshot)})
    state["civil_corridor_log"].append({"ts": now_utc().isoformat(), "count": len(civil_corridor_flights)})

    escalation_score, escalation_lvl, escalation_emoji, factors, mil_baseline, mil_count = compute_escalation(
        state, current_mil_snapshot, len(civil_corridor_flights)
    )
    print(f"[ Escalation ] {escalation_score}/100 ({escalation_lvl}) — factors: {factors}")

    # Always capture the regional overview screenshot once per run,
    # regardless of escalation level -- reused across every message
    # this run sends, then cleaned up at the end.
    overview_path = capture_regional_overview_map()

    try:
        # --- Post individual alerts for newly-seen military aircraft ---
        new_detections = 0
        for ac in current_mil_snapshot:
            icao = str(ac.get("hex", "")).strip()
            callsign = str(ac.get("flight", "N/A")).strip()
            if not icao:
                continue
            flight_key = f"{icao}_{callsign}"
            if is_recently_seen(state, flight_key):
                continue

            mark_seen(state, flight_key)
            new_detections += 1
            sector = determine_airspace_sector(ac.get("lat"), ac.get("lon"))
            res = post_detection(ac, sector, escalation_score, escalation_lvl, escalation_emoji, overview_path)
            print(f"[ Posted ] {icao} {callsign} -> {res}")

        print(f"[ Summary ] {new_detections} new military detections this cycle "
              f"({len(current_mil_snapshot)} currently active in region).")

        # --- Escalation level change notification (always includes screenshot) ---
        if escalation_lvl != state.get("last_escalation_level", "LOW"):
            post_escalation_change(state.get("last_escalation_level", "LOW"), escalation_lvl,
                                    escalation_score, escalation_emoji, factors, overview_path)
            state["last_escalation_level"] = escalation_lvl

        # --- Civil anomaly alert (independent of escalation band change) ---
        civil_anomaly_factor = next((f for f in factors if "Hormuz/Gulf corridor" in f), None)
        if civil_anomaly_factor:
            one_hour_ago = now_utc() - timedelta(hours=1)
            baseline_counts = [s["count"] for s in state["civil_corridor_log"]
                                if datetime.fromisoformat(s["ts"]) <= one_hour_ago]
            baseline = (sum(baseline_counts) / len(baseline_counts)) if baseline_counts else 0
            post_civil_anomaly(len(civil_corridor_flights), baseline, overview_path)

        # --- Heartbeat: only if nothing new happened AND it's been a while ---
        # (still includes the regional screenshot, even at LOW escalation)
        if new_detections == 0:
            last_fb = state.get("last_fallback_post_ts")
            should_post_heartbeat = (
                last_fb is None
                or datetime.fromisoformat(last_fb) < now_utc() - timedelta(hours=1)
            )
            if should_post_heartbeat:
                post_heartbeat(total_scanned, len(current_mil_snapshot), len(civil_corridor_flights),
                               escalation_score, escalation_lvl, escalation_emoji, overview_path)
                state["last_fallback_post_ts"] = now_utc().isoformat()
    finally:
        cleanup_file(overview_path)

    save_state(state)


if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        print(f"[ Fatal Error ] {e}")
        traceback.print_exc()
