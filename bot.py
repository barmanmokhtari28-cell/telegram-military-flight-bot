import os
import sys
import time
import json
import hashlib
import traceback
from datetime import datetime, timezone, timedelta

import requests
from playwright.sync_api import sync_playwright

# ==================================================================
# CONFIG & SECRETS (Safe for public GitHub repositories)
# ==================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Optional OpenSky credentials (can be stored in GitHub Secrets)
OPENSKY_USER = os.getenv("OPENSKY_USER")
OPENSKY_PASS = os.getenv("OPENSKY_PASS")

# Geographic Boundaries
# 1. Middle East Theater
MIDEAST_LAT_MIN, MIDEAST_LAT_MAX = 12.0, 40.0
MIDEAST_LON_MIN, MIDEAST_LON_MAX = 32.0, 65.0

# 2. Iranian Airspace Boundary (for 12-hour report)
IRAN_LAT_MIN, IRAN_LAT_MAX = 25.0, 39.0
IRAN_LON_MIN, IRAN_LON_MAX = 44.0, 63.5

# 3. Persian Gulf / Hormuz Corridor
HORMUZ_LAT_MIN, HORMUZ_LAT_MAX = 24.0, 30.0
HORMUZ_LON_MIN, HORMUZ_LON_MAX = 53.0, 60.0

# Strategic points covering hubs across the US-Europe-Mideast pipeline
REGION_POINTS = [
    ("Al Udeid AB / Qatar / Central Gulf", 25.1, 51.3),
    ("Al Dhafra AB / UAE / S. Gulf", 24.2, 54.5),
    ("Ali Al Salem AB / Kuwait / N. Gulf", 29.3, 47.5),
    ("Strait of Hormuz / Persian Gulf", 26.5, 56.0),
    ("Prince Sultan AB / Saudi Arabia", 24.1, 47.6),
    ("Muwaffaq Salti AB / Jordan / Levant", 31.8, 36.8),
    ("Israel / Eastern Mediterranean", 32.0, 34.8),
    ("Souda Bay / Crete / E. Med Gateway", 35.5, 24.1),
    ("Ramstein Air Base / Germany Hub", 49.4, 7.6),
    ("RAF Mildenhall / Lakenheath / UK Hub", 52.3, 0.5),
    ("Naval Station Rota / Spain Gateway", 36.6, -6.3),
    ("NAS Sigonella / Sicily / Med Hub", 37.4, 14.9),
    ("Lajes Field / Azores / Atlantic Bridge", 38.7, -27.1),
    ("Bab el-Mandeb / S. Red Sea Corridor", 13.0, 43.5),
]
POINT_RADIUS_NM = 250

CUMULATIVE_MAP_LAT, CUMULATIVE_MAP_LON, CUMULATIVE_MAP_ZOOM = 36.0, 20.0, 4
OVERVIEW_LAT, OVERVIEW_LON, OVERVIEW_ZOOM = 27.5, 47.0, 5

STATE_FILE = "state.json"
REANNOUNCE_AFTER_HOURS = 8
ANOMALY_COOLDOWN_HOURS = 3

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def log(msg):
    print(msg, flush=True)

now_utc = lambda: datetime.now(timezone.utc)

# ==================================================================
# AIRCRAFT TYPES & CALLSIGNS
# ==================================================================

AIRCRAFT_NAMES = {
    "C17": "C-17A Globemaster III",
    "C5": "C-5M Super Galaxy",
    "C130": "C-130 Hercules",
    "C30J": "C-130J Super Hercules",
    "A400": "A400M Atlas",
    "IL76": "Il-76 Strategic Transport",
    "AN124": "An-124 Ruslan",
    "KC135": "KC-135R Stratotanker",
    "K35R": "KC-135R Stratotanker",
    "KC46": "KC-46A Pegasus",
    "KC10": "KC-10A Extender",
    "A332": "A330 MRTT Tanker",
    "B707": "Boeing 707 Re'em Tanker",
    "P8": "P-8A Poseidon",
    "RC135": "RC-135 Rivet Joint",
    "E3TF": "E-3 Sentry AWACS",
    "E3": "E-3 Sentry AWACS",
    "E8": "E-8C Joint STARS",
    "E2": "E-2D Hawkeye",
    "G550": "G550 Nachshon / Eitam / Oron",
    "GLF5": "G550 Nachshon / Eitam / Oron",
    "RQ4": "RQ-4 Global Hawk",
    "MQ9": "MQ-9 Reaper",
    "U2": "U-2S Dragon Lady",
    "B52": "B-52H Stratofortress",
    "B1": "B-1B Lancer",
    "B2": "B-2A Spirit",
    "F15": "F-15 Eagle / Strike Eagle",
    "F16": "F-16 Fighting Falcon",
    "F22": "F-22A Raptor",
    "F35": "F-35 Lightning II / Adir",
    "F18": "F/A-18 Super Hornet",
    "A10": "A-10 Thunderbolt II",
    "VC25": "Air Force One (VC-25)",
    "C32": "Boeing C-32A (AF Two)",
    "C40": "Boeing C-40 Clipper",
    "E4B": "E-4B Nightwatch (Doomsday)",
    "B767": "Boeing 767 / Wing of Zion",
}

STRATEGIC_LOGISTICS_TYPES = [
    "C17", "C5", "C130", "C30J", "A400", "IL76", "AN124",
    "KC135", "K35R", "KC46", "KC10", "A332", "B707",
    "B52", "B1", "B2", "RC135", "P8", "E3", "E3TF", "E8", "RQ4", "U2",
    "G550", "GLF5", "VC25", "C32", "C40", "E4B", "B767"
]

US_LOGISTICS_CALLSIGNS = [
    "RCH", "REACH", "MOOSE", "SLAM", "ORDER",
    "LAGR", "NCHO", "GOLD", "CLEAN", "BOBBY", "PEARL", "SHELL", "ESSO", "QUID",
    "CMB", "CAMBER",
    "FORTE", "HOMER", "OLIVE", "COBRA", "PYTHON", "SNOOP", "JAKE",
    "DOOM", "DEATH", "MYTEE", "BONE", "DARK", "SKULL",
    "NAVY", "TOPCAT", "VNDL", "GOTO", "PAT", "EVAC", "SAM", "EXEC", "SPAR", "VENUS",
]

ISRAEL_CALLSIGNS = ["IAF", "ISF", "ISR", "KNAF", "RAM", "ORON", "EITAM", "SHAVIT"]
ALLIED_MIL_PREFIXES = ["RRR", "ASCOT", "TARTAN", "CTM", "COTAM", "GAF", "GAM", "NATO", "NAF"]

# ==================================================================
# STATE MANAGEMENT
# ==================================================================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "seen_flights": [],
        "mil_snapshot_log": [],
        "civil_corridor_log": [],
        "last_12h_report_ts": None,
        "last_anomaly_sig": None,
        "last_anomaly_ts": None,
    }

def save_state(state):
    cutoff = now_utc() - timedelta(hours=48)
    state["seen_flights"] = [
        s for s in state.get("seen_flights", [])
        if datetime.fromisoformat(s["ts"]) > now_utc() - timedelta(hours=REANNOUNCE_AFTER_HOURS)
    ][-1000:]
    state["mil_snapshot_log"] = [
        s for s in state.get("mil_snapshot_log", []) if datetime.fromisoformat(s["ts"]) > cutoff
    ][-500:]
    state["civil_corridor_log"] = [
        s for s in state.get("civil_corridor_log", []) if datetime.fromisoformat(s["ts"]) > (now_utc() - timedelta(days=14))
    ][-2000:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def is_recently_seen(state, flight_key):
    return any(s["key"] == flight_key for s in state.get("seen_flights", []))

def mark_seen(state, flight_key):
    state.setdefault("seen_flights", []).append({"key": flight_key, "ts": now_utc().isoformat()})

# ==================================================================
# DATA FETCHING
# ==================================================================

def fetch_from_adsb_lol():
    results = []
    try:
        url = "https://api.adsb.lol/v2/mil"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=14)
        if res.status_code == 200:
            ac = res.json().get("ac", [])
            log(f"[ adsb.lol ] Global military endpoint returned {len(ac)} aircraft.")
            results.extend(ac)
    except Exception as e:
        log(f"[ Warn ] adsb.lol global mil request failed: {e}")

    for label, lat, lon in REGION_POINTS:
        try:
            url = f"https://api.adsb.lol/v2/point/{lat}/{lon}/{POINT_RADIUS_NM}"
            res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            if res.status_code == 200:
                results.extend(res.json().get("ac", []))
        except Exception:
            pass
    return results

def fetch_from_adsb_fi():
    results = []
    try:
        url = "https://opendata.adsb.fi/api/v2/mil"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=14)
        if res.status_code == 200:
            ac = res.json().get("ac", [])
            log(f"[ adsb.fi ] Military feed returned {len(ac)} aircraft.")
            results.extend(ac)
    except Exception as e:
        log(f"[ Warn ] adsb.fi failed: {e}")
    return results

def fetch_from_opensky():
    results = []
    auth = (OPENSKY_USER, OPENSKY_PASS) if (OPENSKY_USER and OPENSKY_PASS) else None
    url = f"https://opensky-network.org/api/states/all?lamin={MIDEAST_LAT_MIN}&lomin={MIDEAST_LON_MIN}&lamax={MIDEAST_LAT_MAX}&lomax={MIDEAST_LON_MAX}"
    try:
        res = requests.get(url, headers={"User-Agent": "MilitaryTracker/4.0"}, auth=auth, timeout=15)
        if res.status_code == 200:
            states = res.json().get("states", []) or []
            log(f"[ OpenSky ] Fallback returned {len(states)} regional aircraft.")
            for s in states:
                results.append({
                    "hex": str(s[0]).strip().lower(),
                    "flight": str(s[1]).strip() if s[1] else "",
                    "lat": s[6],
                    "lon": s[5],
                    "alt_baro": int(s[7] * 3.28084) if s[7] is not None else None,
                    "gs": int(s[9] * 1.94384) if s[9] is not None else None,
                    "track": s[10],
                    "t": "",
                    "dbFlags": 1 if s[2] == "United States" and is_us_military_hex(str(s[0])) else 0,
                })
    except Exception as e:
        log(f"[ Warn ] OpenSky fetch failed: {e}")
    return results

def fetch_all_aircraft():
    raw = []
    raw.extend(fetch_from_adsb_lol())
    raw.extend(fetch_from_adsb_fi())

    if len(raw) == 0:
        log("[ Fallback ] Primary ADS-B feeds returned 0. Querying OpenSky...")
        raw.extend(fetch_from_opensky())

    dedup = {}
    for ac in raw:
        hex_code = str(ac.get("hex", "")).strip().lower()
        if hex_code and hex_code not in dedup:
            dedup[hex_code] = ac

    log(f"[ Fetch ] Total unique aircraft collected: {len(dedup)}")
    return list(dedup.values())

# ==================================================================
# CLASSIFICATION & PIPELINE GEOGRAPHY
# ==================================================================

def is_us_military_hex(icao_hex: str) -> bool:
    try:
        val = int(icao_hex, 16)
        return 0xAE0000 <= val <= 0xAFFFFF
    except Exception:
        return False

def is_israeli_military(icao_hex: str, callsign: str) -> bool:
    try:
        val = int(icao_hex, 16)
        if 0x738000 <= val <= 0x738FFF:
            return True
    except Exception:
        pass
    return any(callsign.startswith(p) for p in ISRAEL_CALLSIGNS)

def in_mideast_theater(lat, lon) -> bool:
    return lat is not None and lon is not None and MIDEAST_LAT_MIN <= lat <= MIDEAST_LAT_MAX and MIDEAST_LON_MIN <= lon <= MIDEAST_LON_MAX

def in_iran_airspace(lat, lon) -> bool:
    return lat is not None and lon is not None and IRAN_LAT_MIN <= lat <= IRAN_LAT_MAX and IRAN_LON_MIN <= lon <= IRAN_LON_MAX

def in_hormuz_corridor(lat, lon) -> bool:
    return lat is not None and lon is not None and HORMUZ_LAT_MIN <= lat <= HORMUZ_LAT_MAX and HORMUZ_LON_MIN <= lon <= HORMUZ_LON_MAX

def in_transatlantic_or_europe(lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    return 25.0 <= lat <= 65.0 and -85.0 <= lon <= 34.0

def is_target_flight(ac: dict) -> bool:
    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        return False

    icao = str(ac.get("hex", "")).strip().lower()
    callsign = str(ac.get("flight", "")).strip().upper()
    typecode = str(ac.get("t", "")).strip().upper()
    db_flags = ac.get("dbFlags", 0)

    is_mil = (
        is_us_military_hex(icao)
        or is_israeli_military(icao, callsign)
        or db_flags == 1
        or (typecode in AIRCRAFT_NAMES)
        or any(callsign.startswith(p) for p in US_LOGISTICS_CALLSIGNS + ISRAEL_CALLSIGNS + ALLIED_MIL_PREFIXES)
    )
    if not is_mil:
        return False

    if in_mideast_theater(lat, lon):
        return True

    if in_transatlantic_or_europe(lat, lon):
        if typecode in STRATEGIC_LOGISTICS_TYPES:
            return True
        if any(callsign.startswith(p) for p in US_LOGISTICS_CALLSIGNS + ISRAEL_CALLSIGNS):
            return True

    return False

def get_region_group(lat, lon):
    if lon is not None and lon < -15.0:
        return "transatlantic"
    elif lon is not None and -15.0 <= lon <= 34.0:
        return "europe"
    else:
        return "mideast"

def format_flight_posture(track, lat, lon):
    is_eastbound = (track is not None and 45 <= track <= 135)
    is_westbound = (track is not None and 225 <= track <= 315)

    if lon is not None and lon < -15.0:
        return "➡️ Eastbound Transatlantic" if is_eastbound else "⬅️ Westbound to US"
    elif lon is not None and -15.0 <= lon <= 34.0:
        if 20.0 <= lon <= 34.0:
            return "➡️ E. Med Inbound Mideast" if is_eastbound else "⬅️ E. Med Egress"
        return "➡️ Europe Inbound Mideast" if is_eastbound else "⬅️ Europe Westbound Egress"
    else:
        return "➡️ Theater Inbound" if is_eastbound else ("⬅️ Theater Outbound" if is_westbound else "🔄 Patrol / Orbit")

# ==================================================================
# ABNORMAL BUILD-UP & EGRESS ANOMALY ENGINE
# ==================================================================

def evaluate_abnormal_patterns(state, target_flights, civil_hormuz_count):
    anomalies = []
    evidence_aircraft = []

    inbound_airlift = []
    outbound_airlift = []
    tankers = []
    bombers = []
    israeli_strategic = []
    doomsday_vip = []

    for ac in target_flights:
        icao = str(ac.get("hex", "")).strip().lower()
        callsign = str(ac.get("flight", "")).strip().upper()
        typecode = str(ac.get("t", "")).strip().upper()
        track = ac.get("track")
        lat, lon = ac.get("lat"), ac.get("lon")

        is_east = (track is not None and 45 <= track <= 135)
        is_west = (track is not None and 225 <= track <= 315)

        if typecode in ["B52", "B1", "B2"] or any(callsign.startswith(p) for p in ["DOOM", "DEATH", "MYTEE", "BONE", "DARK"]):
            bombers.append(ac)

        if typecode in ["KC135", "K35R", "KC46", "KC10", "A332", "B707"] or any(callsign.startswith(p) for p in ["LAGR", "NCHO", "GOLD", "CLEAN", "QUID", "SHELL"]):
            tankers.append(ac)

        if typecode in ["C17", "C5", "C130", "C30J", "A400", "AN124", "IL76"] or any(callsign.startswith(p) for p in ["RCH", "REACH", "MOOSE", "SLAM", "CMB"]):
            if is_east or (lon and lon > 20.0 and is_east):
                inbound_airlift.append(ac)
            elif is_west and (lon and lon > 15.0):
                outbound_airlift.append(ac)

        if is_israeli_military(icao, callsign) or typecode in ["G550", "GLF5", "B707", "B767"]:
            israeli_strategic.append(ac)

        if typecode in ["E4B", "VC25", "C32", "E6B"]:
            doomsday_vip.append(ac)

    # Threshold rules
    if bombers:
        anomalies.append(f"💣 <b>STRATEGIC BOMBERS ACTIVE:</b> {len(bombers)} Heavy Bomber(s) airborne.")
        evidence_aircraft.extend(bombers)

    if len(inbound_airlift) >= 3:
        anomalies.append(f"📦 <b>MASS AIRLIFT BUILD-UP:</b> {len(inbound_airlift)} Transports inbound to Mideast.")
        evidence_aircraft.extend(inbound_airlift)

    if len(outbound_airlift) >= 3:
        anomalies.append(f"🛫 <b>THEATER EGRESS SURGE:</b> {len(outbound_airlift)} Military Transports heading Westbound.")
        evidence_aircraft.extend(outbound_airlift)

    if len(tankers) >= 4:
        anomalies.append(f"⛽ <b>AERIAL REFUELING SURGE:</b> {len(tankers)} Tankers active along corridor.")
        evidence_aircraft.extend(tankers)

    if len(israeli_strategic) >= 2:
        anomalies.append(f"🇮🇱 <b>ISRAELI AIR FORCE SURGE:</b> {len(israeli_strategic)} Strategic IAF assets active.")
        evidence_aircraft.extend(israeli_strategic)

    if doomsday_vip:
        anomalies.append(f"🚨 <b>NATIONAL AIRBORNE COMMAND:</b> High-value US command aircraft airborne.")
        evidence_aircraft.extend(doomsday_vip)

    one_hour_ago = now_utc() - timedelta(hours=1)
    civil_baseline_counts = [s["count"] for s in state.get("civil_corridor_log", []) if datetime.fromisoformat(s["ts"]) <= one_hour_ago]
    civil_baseline = (sum(civil_baseline_counts) / len(civil_baseline_counts)) if civil_baseline_counts else 0

    if civil_baseline >= 8 and civil_hormuz_count <= civil_baseline * 0.4:
        anomalies.append(f"⚠️ <b>HORMUZ TRAFFIC DROP:</b> Commercial traffic dropped to {civil_hormuz_count} flights (baseline ~{civil_baseline:.1f}).")

    return anomalies, evidence_aircraft

# ==================================================================
# SCREENSHOT CAPTURE (Isolates ALL Cumulative Flights on the Map)
# ==================================================================

def _screenshot(map_url: str, filename: str, render_wait_ms: int = 8000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-web-security",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto(map_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(render_wait_ms)
            page.screenshot(path=filename, full_page=False)
            browser.close()

            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                log(f"[ Playwright ] Screenshot saved: {filename} ({os.path.getsize(filename)} bytes)")
                return filename
            return None
    except Exception as e:
        log(f"[ Warn ] Screenshot failed: {e}")
        return None

def capture_cumulative_flights_map(ac_list: list) -> str:
    """
    CRITICAL FEATURE:
    Passes comma-separated hex IDs of ALL detected cumulative flights into tar1090.
    - icaoFilter: isolates ONLY these aircraft on the map (all other traffic hidden).
    - icao: selects them to display their flight track lines.
    - enableLabels: prints callsigns and types directly next to each plane icon.
    """
    hexes = [str(ac.get("hex", "")).strip().lower() for ac in ac_list if ac.get("hex")]
    hex_str = ",".join(hexes[:30])

    if hex_str:
        map_url = f"https://globe.adsb.lol/?icaoFilter={hex_str}&icao={hex_str}&enableLabels&hideSidebar"
    else:
        map_url = f"https://globe.adsb.lol/?lat={CUMULATIVE_MAP_LAT}&lon={CUMULATIVE_MAP_LON}&zoom={CUMULATIVE_MAP_ZOOM}&filterMil&hideSidebar"

    log(f"[ Playwright ] Capturing cumulative map for {len(hexes)} isolated flights...")
    return _screenshot(map_url, "cumulative_flights.png", render_wait_ms=8500)

def capture_regional_overview_map() -> str:
    map_url = f"https://globe.adsb.lol/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}&hideSidebar"
    return _screenshot(map_url, "regional_overview.png", render_wait_ms=8000)

def cleanup_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

# ==================================================================
# TELEGRAM DISPATCH (Screenshot with Report in Caption)
# ==================================================================

def send_telegram_alert(caption: str, photo_path: str):
    """
    Ensures the report is ALWAYS attached as the caption of the screenshot.
    Truncates to 1020 chars if needed so Telegram's 1024-character photo caption
    limit is never exceeded.
    """
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    if photo_path and os.path.exists(photo_path) and os.path.getsize(photo_path) > 0:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        try:
            with open(photo_path, "rb") as f:
                res = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=35,
                ).json()
            if res.get("ok"):
                log(f"[ Telegram ] Photo with report caption delivered successfully.")
                return True
            else:
                log(f"[ Telegram Warn ] sendPhoto rejected: {res}. Falling back to text.")
        except Exception as e:
            log(f"[ Telegram Error ] sendPhoto failed: {e}")

    # Fallback to plain text if photo fails
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        return True
    except Exception as e:
        log(f"[ Telegram Error ] sendMessage failed: {e}")
        return False

# ==================================================================
# 12-HOUR REGIONAL & IRAN AIRSPACE BRIEFING
# ==================================================================

def check_and_send_12h_report(state, raw_aircraft):
    last_rep = state.get("last_12h_report_ts")
    should_send = False

    if last_rep is None:
        should_send = True
    else:
        try:
            if datetime.fromisoformat(last_rep) <= now_utc() - timedelta(hours=12):
                should_send = True
        except Exception:
            should_send = True

    if not should_send:
        return

    mideast_flights = [ac for ac in raw_aircraft if in_mideast_theater(ac.get("lat"), ac.get("lon"))]
    iran_flights = [ac for ac in raw_aircraft if in_iran_airspace(ac.get("lat"), ac.get("lon"))]
    hormuz_flights = [ac for ac in raw_aircraft if in_hormuz_corridor(ac.get("lat"), ac.get("lon"))]
    mil_in_mideast = [ac for ac in mideast_flights if is_target_flight(ac)]

    log(f"[ 12h Report ] Dispatching 12-hour report (Mideast: {len(mideast_flights)}, Iran: {len(iran_flights)})...")
    overview_img = capture_regional_overview_map()

    caption = (
        f"🌐 <b>12-HOUR REGIONAL & IRAN AIRSPACE BRIEFING</b>\n\n"
        f"📅 <b>Timestamp:</b> <code>{now_utc().strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
        f"📊 <b>Middle East Total Monitored:</b> <code>{len(mideast_flights)}</code>\n"
        f"🇮🇷 <b>Active in Iranian Airspace:</b> <code>{len(iran_flights)}</code>\n"
        f"🌊 <b>Persian Gulf / Hormuz Corridor:</b> <code>{len(hormuz_flights)}</code>\n"
        f"🪖 <b>Monitored Military Aircraft:</b> <code>{len(mil_in_mideast)}</code>\n\n"
        f"ℹ️ <i>Automated regional surveillance snapshot.</i>\n\n"
        f"🔗 <a href='https://globe.adsb.lol/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}'>Live Regional Radar Map</a>\n"
        f"📡 @secretollah"
    )

    try:
        send_telegram_alert(caption, overview_img)
        state["last_12h_report_ts"] = now_utc().isoformat()
        log("[ 12h Report ] Briefing posted.")
    finally:
        cleanup_file(overview_img)

# ==================================================================
# MAIN ORCHESTRATION
# ==================================================================

def run_tracker():
    log("==================================================")
    log("   OSINT Sky Radar — Strategic Alert & Pipeline    ")
    log("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        log("[ Error ] Telegram credentials missing from environment variables!")
        return

    state = load_state()
    raw_aircraft = fetch_all_aircraft()

    if len(raw_aircraft) == 0:
        log("[ ABORT ] All ADS-B sources returned 0 aircraft. Halting run to protect baseline.")
        return

    # Update baselines
    mideast_flights = [ac for ac in raw_aircraft if in_mideast_theater(ac.get("lat"), ac.get("lon"))]
    civil_hormuz_flights = [ac for ac in raw_aircraft if not is_target_flight(ac) and in_hormuz_corridor(ac.get("lat"), ac.get("lon"))]
    target_flights = [ac for ac in raw_aircraft if is_target_flight(ac)]

    state["mil_snapshot_log"].append({"ts": now_utc().isoformat(), "count": len(target_flights)})
    state["civil_corridor_log"].append({"ts": now_utc().isoformat(), "count": len(civil_hormuz_flights)})

    # 1. Dispatch independent 12-hour Regional & Iran airspace briefing
    check_and_send_12h_report(state, raw_aircraft)

    # 2. REAL-TIME ANOMALY & BUILD-UP / EGRESS DETECTION
    anomalies, evidence_aircraft = evaluate_abnormal_patterns(state, target_flights, len(civil_hormuz_flights))

    if anomalies:
        sig_str = "|".join(sorted(anomalies)) + "|" + str(len(evidence_aircraft))
        current_sig = hashlib.md5(sig_str.encode()).hexdigest()
        last_sig = state.get("last_anomaly_sig")
        last_anomaly_ts = state.get("last_anomaly_ts")

        should_post_anomaly = (
            last_sig != current_sig
            or last_anomaly_ts is None
            or datetime.fromisoformat(last_anomaly_ts) < now_utc() - timedelta(hours=ANOMALY_COOLDOWN_HOURS)
        )

        if should_post_anomaly:
            log(f"[ FLASH ALERT ] Anomaly detected: {anomalies}")
            # Screenshot isolates the exact evidence aircraft
            anomaly_map = capture_cumulative_flights_map(evidence_aircraft)

            anomaly_lines = [
                f"🚨 <b>STRATEGIC MILITARY ANOMALY ALERT</b> 🚨",
                f"⚠️ <b>Abnormal Build-Up / Egress Vector</b>",
                f"⏱ <b>Timestamp:</b> <code>{now_utc().strftime('%Y-%m-%d %H:%M UTC')}</code>\n",
                f"<b>Key Indicators:</b>"
            ]
            for factor in anomalies[:4]:
                anomaly_lines.append(f"• {factor}")

            anomaly_lines.append("\n<b>Observed Surge Assets:</b>")
            dedup_ev = {str(ac.get("hex", "")).lower(): ac for ac in evidence_aircraft}
            for ac in list(dedup_ev.values())[:6]:
                callsign = str(ac.get("flight", "N/A")).strip().upper() or "N/A"
                typecode = str(ac.get("t", "MIL")).strip().upper()
                alt = f"{ac.get('alt_baro', 'N/A')} ft" if ac.get('alt_baro') else "N/A"
                posture = format_flight_posture(ac.get("track"), ac.get("lat"), ac.get("lon"))
                anomaly_lines.append(f"• <code>{callsign}</code> ({typecode}) | {alt} | {posture}")

            anomaly_lines.append("\n🔗 <a href='https://globe.adsb.lol/?filterMil'>Live Military Radar Feed</a>")
            anomaly_lines.append("📡 @secretollah")

            anomaly_caption = "\n".join(anomaly_lines)
            send_telegram_alert(anomaly_caption, anomaly_map)
            cleanup_file(anomaly_map)

            state["last_anomaly_sig"] = current_sig
            state["last_anomaly_ts"] = now_utc().isoformat()
            log("[ FLASH ALERT ] Delivered urgent anomaly alert with attached screenshot.")

    # 3. ROUTINE CUMULATIVE DIGEST (Batches all cumulative flights into ONE report + screenshot)
    new_flights = []
    for ac in target_flights:
        icao = str(ac.get("hex", "")).strip().lower()
        callsign = str(ac.get("flight", "N/A")).strip().upper() or "N/A"
        flight_key = f"{icao}_{callsign}"

        if not icao:
            continue
        if not is_recently_seen(state, flight_key):
            new_flights.append(ac)
            mark_seen(state, flight_key)

    log(f"[ Activity ] {len(target_flights)} active military flights ({len(new_flights)} new arrivals).")

    # Only post routine digest if new flights appeared and no anomaly was just posted
    if len(new_flights) > 0 and not anomalies:
        # Screenshot isolates ALL currently active target flights on the map with callsign labels
        cumulative_map = capture_cumulative_flights_map(target_flights)

        groups = {"transatlantic": [], "europe": [], "mideast": []}
        for ac in target_flights:
            lat, lon = ac.get("lat"), ac.get("lon")
            g = get_region_group(lat, lon)
            groups[g].append(ac)

        lines = [
            f"🚨 <b>US & ALLIED MILITARY AIR MOBILITY</b> 🚨",
            f"⏱ <b>Active Fleet Snapshot:</b> <code>{now_utc().strftime('%Y-%m-%d %H:%M UTC')}</code>",
            f"✈️ <b>Total Airborne:</b> <code>{len(target_flights)}</code> (<code>+{len(new_flights)} new</code>)\n"
        ]

        new_hexes = {str(ac.get("hex", "")).lower() for ac in new_flights}
        rendered_count = 0

        def render_group(header, ac_list):
            nonlocal rendered_count
            if not ac_list:
                return
            lines.append(f"<b>{header}</b>")
            for ac in ac_list:
                if rendered_count >= 10:
                    break
                hex_code = str(ac.get("hex", "")).lower()
                callsign = str(ac.get("flight", "N/A")).strip().upper() or "N/A"
                typecode = str(ac.get("t", "MIL")).strip().upper()
                alt = f"{ac.get('alt_baro', 'N/A')} ft" if ac.get('alt_baro') else "N/A"
                posture = format_flight_posture(ac.get("track"), ac.get("lat"), ac.get("lon"))
                new_badge = " 🆕" if hex_code in new_hexes else ""

                lines.append(f"• <code>{callsign}</code> ({typecode}) | {alt} | {posture}{new_badge}")
                rendered_count += 1
            lines.append("")

        render_group("🌊 TRANSATLANTIC & CONUS:", groups["transatlantic"])
        render_group("🇪🇺 EUROPEAN CORRIDORS:", groups["europe"])
        render_group("🌐 MIDDLE EAST FORWARD THEATER:", groups["mideast"])

        remaining = len(target_flights) - rendered_count
        if remaining > 0:
            lines.append(f"<i>...and {remaining} more active aircraft on radar</i>\n")

        lines.append("🔗 <a href='https://globe.adsb.lol/?filterMil'>Live Military Radar Feed</a>")
        lines.append("📡 @secretollah")

        caption = "\n".join(lines)
        # SENDS THE SCREENSHOT OF ALL CUMULATIVE FLIGHTS WITH REPORT IN CAPTION
        send_telegram_alert(caption, cumulative_map)
        cleanup_file(cumulative_map)
        log("[ Digest Delivered ] Cumulative military fleet dispatch posted successfully.")

    save_state(state)
    log("[ Complete ] Run finished.")

if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        log(f"[ Fatal Error ] {e}")
        traceback.print_exc()
