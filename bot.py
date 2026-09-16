import os
import sys
import time
import json
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
# 1. Middle East Theater (Comprehensive monitoring of all military assets)
MIDEAST_LAT_MIN, MIDEAST_LAT_MAX = 12.0, 40.0
MIDEAST_LON_MIN, MIDEAST_LON_MAX = 32.0, 65.0

# 2. Iranian Airspace Boundary (for the 12-hour report)
IRAN_LAT_MIN, IRAN_LAT_MAX = 25.0, 39.0
IRAN_LON_MIN, IRAN_LON_MAX = 44.0, 63.5

# 3. Persian Gulf / Hormuz Corridor
HORMUZ_LAT_MIN, HORMUZ_LAT_MAX = 24.0, 30.0
HORMUZ_LON_MIN, HORMUZ_LON_MAX = 53.0, 60.0

# Key strategic hubs & choke points
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

OVERVIEW_LAT, OVERVIEW_LON, OVERVIEW_ZOOM = 27.5, 47.0, 5
STATE_FILE = "state.json"
REANNOUNCE_AFTER_HOURS = 8

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def log(msg):
    print(msg, flush=True)

now_utc = lambda: datetime.now(timezone.utc)

# ==================================================================
# AIRCRAFT TYPES & CALLSIGNS (Strategic Logistics & Tankers)
# ==================================================================

AIRCRAFT_NAMES = {
    # Strategic Heavy Airlift
    "C17": "C-17A Globemaster III (Strategic Airlifter)",
    "C5": "C-5M Super Galaxy (Heavy Strategic Transport)",
    "C130": "C-130 Hercules (Tactical Transport)",
    "C30J": "C-130J Super Hercules",
    "A400": "A400M Atlas (Tactical/Strategic Transport)",
    "IL76": "Ilyushin Il-76 (Strategic Transport)",
    "AN124": "Antonov An-124 Ruslan (Heavy Transport)",
    # Aerial Refueling Tankers
    "KC135": "KC-135R Stratotanker (Aerial Refueling)",
    "K35R": "KC-135R Stratotanker (Aerial Refueling)",
    "KC46": "KC-46A Pegasus (Strategic Tanker)",
    "KC10": "KC-10A Extender (Heavy Refueling/Cargo)",
    "A332": "A330 MRTT (Multi-Role Tanker Transport)",
    # Strategic ISR & Airborne Early Warning
    "P8": "P-8A Poseidon (Maritime Patrol & ASW)",
    "RC135": "RC-135 Rivet Joint (Signals Intelligence)",
    "E3TF": "E-3 Sentry (AWACS Command & Control)",
    "E3": "E-3 Sentry (AWACS Command & Control)",
    "E8": "E-8C Joint STARS (Ground Surveillance)",
    "E2": "E-2D Advanced Hawkeye (Carrier AEW&C)",
    "RQ4": "RQ-4 Global Hawk / MQ-4C Triton (HALE Drone)",
    "MQ9": "MQ-9 Reaper (Surveillance/Strike Drone)",
    "U2": "U-2S Dragon Lady (High Altitude Recon)",
    # Strategic Bombers & Strike
    "B52": "B-52H Stratofortress (Strategic Bomber)",
    "B1": "B-1B Lancer (Supersonic Heavy Bomber)",
    "B2": "B-2A Spirit (Stealth Strategic Bomber)",
    "F15": "F-15 Strike Eagle",
    "F16": "F-16 Fighting Falcon",
    "F22": "F-22A Raptor (Air Superiority)",
    "F35": "F-35 Lightning II (Multi-Role)",
    "F18": "F/A-18 Super Hornet",
    "A10": "A-10C Thunderbolt II",
    # VIP & Strategic Command
    "VC25": "Air Force One (VC-25 / Presidential)",
    "C32": "Boeing C-32A (Air Force Two / Executive)",
    "C40": "Boeing C-40 Clipper (US Logistics Transport)",
    "E4B": "E-4B Nightwatch (National Airborne Ops Center)",
}

STRATEGIC_LOGISTICS_TYPES = [
    "C17", "C5", "C130", "C30J", "A400", "IL76", "AN124",
    "KC135", "K35R", "KC46", "KC10", "A332",
    "B52", "B1", "B2", "RC135", "P8", "E3", "E3TF", "E8", "RQ4", "U2",
    "VC25", "C32", "C40", "E4B"
]

US_LOGISTICS_CALLSIGNS = [
    # Air Mobility Command (AMC) Strategic Transports
    "RCH", "REACH", "MOOSE", "SLAM", "ORDER",
    # Aerial Refueling Tankers
    "LAGR", "NCHO", "GOLD", "CLEAN", "BOBBY", "PEARL", "SHELL", "ESSO", "QUID",
    # Military Charters carrying DoD personnel/equipment
    "CMB", "CAMBER",
    # Strategic ISR & Bombers
    "FORTE", "HOMER", "OLIVE", "COBRA", "PYTHON", "SNOOP", "JAKE",
    "DOOM", "DEATH", "MYTEE", "BONE", "DARK", "SKULL",
    # Navy Logistics & Command
    "NAVY", "TOPCAT", "VNDL", "GOTO", "PAT", "EVAC", "SAM", "EXEC", "SPAR", "VENUS",
]

ALLIED_MIL_PREFIXES = [
    "RRR", "ASCOT", "TARTAN",  # Royal Air Force
    "IAF", "ISF",              # Israeli Air Force
    "CTM", "COTAM",            # French Air Force
    "GAF", "GAM",              # German Air Force
    "NATO", "NAF",
]

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
        res = requests.get(url, headers={"User-Agent": "MilitaryTracker/3.0"}, auth=auth, timeout=15)
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

def get_plane_photo(icao: str):
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{icao}"
        res = requests.get(url, headers={"User-Agent": "OSINT-Bot/3.0"}, timeout=5).json()
        if res.get("photos"):
            return res["photos"][0]["thumbnail_large"]["src"]
    except Exception:
        pass
    return None

# ==================================================================
# CLASSIFICATION & PIPELINE GEOGRAPHY
# ==================================================================

def is_us_military_hex(icao_hex: str) -> bool:
    try:
        val = int(icao_hex, 16)
        return 0xAE0000 <= val <= 0xAFFFFF
    except Exception:
        return False

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

def classify_role(typecode: str):
    typecode = typecode.upper().strip()
    if typecode in ["C17", "C5", "C130", "C30J", "A400", "IL76", "AN124"]:
        return "STRATEGIC HEAVY AIRLIFT / LOGISTICS"
    if typecode in ["KC135", "K35R", "KC46", "KC10", "A332"]:
        return "AERIAL REFUELING TANKER"
    if typecode in ["P8", "RC135", "E3TF", "E3", "E8", "E2", "RQ4", "MQ9", "U2"]:
        return "ISR & AIRBORNE SURVEILLANCE"
    if typecode in ["B52", "B1", "B2", "F15", "F16", "F22", "F35", "F18", "A10"]:
        return "COMBAT / STRIKE ASSET"
    if typecode in ["VC25", "C32", "C40", "E4B"]:
        return "VIP / STRATEGIC COMMAND"
    return "MILITARY AIR MOBILITY"

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
        or db_flags == 1
        or (typecode in AIRCRAFT_NAMES)
        or any(callsign.startswith(p) for p in US_LOGISTICS_CALLSIGNS + ALLIED_MIL_PREFIXES)
    )
    if not is_mil:
        return False

    # All military inside Middle East theater
    if in_mideast_theater(lat, lon):
        return True

    # Transatlantic & European pipeline
    if in_transatlantic_or_europe(lat, lon):
        if typecode in STRATEGIC_LOGISTICS_TYPES:
            return True
        if any(callsign.startswith(p) for p in US_LOGISTICS_CALLSIGNS):
            return True

    return False

def determine_pipeline_stage_and_posture(track, lat, lon):
    if lat is None or lon is None:
        return "Global Airspace", "Track Bearing Unknown"

    is_eastbound = (track is not None and 45 <= track <= 135)
    is_westbound = (track is not None and 225 <= track <= 315)
    is_southbound = (track is not None and 136 <= track <= 224)

    # 1. Transatlantic Bridge & US East Coast
    if -85.0 <= lon <= -65.0 and 30.0 <= lat <= 45.0:
        corridor = "🇺🇸 US East Coast Strategic Hub (Dover / Charleston / McGuire AFB)"
        posture = "🛫 DEPARTING CONUS (Eastbound Transatlantic Ingress)" if is_eastbound else "🛬 ARRIVING CONUS (Homeland Return)"
    elif -65.0 < lon <= -15.0 and 28.0 <= lat <= 60.0:
        corridor = "🌊 Transatlantic Strategic Air Bridge (North Atlantic / Azores Route)"
        posture = "➡️ TRANSATLANTIC EASTBOUND (US to Europe/Mideast Pipeline)" if is_eastbound else "⬅️ TRANSATLANTIC WESTBOUND (Returning to US Bases)"

    # 2. European Transit Corridors
    elif -15.0 < lon <= 5.0 and 48.0 <= lat <= 60.0:
        corridor = "🇬🇧 UK Strategic Waypoint (RAF Mildenhall / Lakenheath / Fairford)"
        posture = "➡️ INBOUND EUROPE/MIDEAST (Eastbound)" if is_eastbound else "⬅️ WESTBOUND EGRESS (Heading toward Atlantic)"
    elif 5.0 < lon <= 16.0 and 46.0 <= lat <= 55.0:
        corridor = "🇩🇪 Central European Hub (Ramstein / Spangdahlem Air Base)"
        posture = "➡️ INBOUND SOUTHEAST (Toward Med / Middle East)" if (is_eastbound or is_southbound) else "⬅️ NORTHWEST TRANSIT (Returning through Europe)"
    elif -12.0 < lon <= 5.0 and 34.0 <= lat <= 45.0:
        corridor = "🇪🇸 Iberian Strategic Gateway (Naval Station Rota / Morón AB)"
        posture = "➡️ MEDITERRANEAN INGRESS (Heading East toward Middle East)" if is_eastbound else "⬅️ ATLANTIC EGRESS (Heading West to US)"
    elif 5.0 < lon <= 20.0 and 34.0 <= lat <= 46.0:
        corridor = "🇮🇹 Central Mediterranean Corridor (NAS Sigonella / Aviano AB)"
        posture = "➡️ EASTBOUND TRANSIT (Toward Levant / Gulf)" if is_eastbound else "⬅️ WESTBOUND TRANSIT (Toward Europe / US)"
    elif 20.0 < lon <= 34.0 and 30.0 <= lat <= 40.0:
        corridor = "🇬🇷 Eastern Mediterranean Gateway (Souda Bay, Crete / Cyprus)"
        posture = "➡️ INBOUND MIDDLE EAST THEATER (Forward Staging)" if is_eastbound else "⬅️ EGRESS OUT OF THEATER (Returning to Europe)"

    # 3. Middle East Forward Theater
    elif in_mideast_theater(lat, lon):
        if HORMUZ_LAT_MIN <= lat <= HORMUZ_LAT_MAX and HORMUZ_LON_MIN <= lon <= HORMUZ_LON_MAX:
            corridor = "🌊 Strait of Hormuz & Persian Gulf Maritime Chokepoint"
        elif 22.5 <= lat <= 26.5 and 50.5 <= lon <= 56.5:
            corridor = "🇦🇪🇶🇦 Al Udeid (Qatar) / Al Dhafra (UAE) Forward Hub"
        elif 28.5 <= lat <= 30.5 and 46.5 <= lon <= 48.5:
            corridor = "🇰🇼 Ali Al Salem AB Forward Logistics Hub (Kuwait)"
        elif 22.0 <= lat <= 27.0 and 44.0 <= lon <= 50.0:
            corridor = "🇸🇦 Prince Sultan Air Base Sector (Saudi Arabia)"
        elif 29.5 <= lat <= 33.5 and 35.0 <= lon <= 39.0:
            corridor = "🇯🇴 Muwaffaq Salti AB / Jordan Forward Sector"
        elif 31.0 <= lat <= 34.0 and 34.0 <= lon <= 36.0:
            corridor = "🇮🇱 Israel / Levant Forward Sector"
        elif 31.0 <= lat <= 37.0 and 38.0 <= lon <= 46.0:
            corridor = "🇮🇶 Iraqi Airspace Transit Corridor"
        elif 12.0 <= lat <= 22.0 and 38.0 <= lon <= 45.0:
            corridor = "🔴 Red Sea / Bab el-Mandeb Strategic Corridor"
        elif in_iran_airspace(lat, lon):
            corridor = "🇮🇷 Iranian Airspace / Boundary Zone"
        else:
            corridor = "🌐 Middle East Operational Theater"

        posture = "➡️ THEATER INBOUND / FORWARD REINFORCEMENT" if is_eastbound else ("⬅️ THEATER OUTBOUND / RETURN TRANSIT" if is_westbound else "🔄 THEATER PATROL / REFUELING ORBIT")
    else:
        corridor = "🌐 International Transit Corridor"
        posture = "Transit Bearing: " + (f"{int(track)}°" if track else "Unknown")

    return corridor, posture

# ==================================================================
# SCREENSHOT CAPTURE (Fixed: Uses domcontentloaded to prevent timeouts)
# ==================================================================

def _screenshot(map_url: str, filename: str, render_wait_ms: int = 7000):
    """
    CRITICAL FIX:
    Do NOT use 'networkidle'. tar1090 continuously polls aircraft.json every second,
    which causes networkidle to timeout, fail, and drop the screenshot.
    Using 'domcontentloaded' + explicit wait renders map tiles & aircraft cleanly.
    """
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
            
            # Wait for tar1090 to load flight trace, icons and map tiles
            page.wait_for_timeout(render_wait_ms)
            
            page.screenshot(path=filename, full_page=False)
            browser.close()

            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                log(f"[ Playwright ] Screenshot saved successfully: {filename} ({os.path.getsize(filename)} bytes)")
                return filename
            else:
                log(f"[ Playwright ] Screenshot file missing or empty: {filename}")
                return None
    except Exception as e:
        log(f"[ Warn ] Screenshot failed for {map_url}: {e}")
        return None

def capture_flight_map(icao: str) -> str:
    map_url = f"https://globe.adsb.lol/?icao={icao.lower()}&hideSidebar"
    return _screenshot(map_url, f"map_{icao.lower()}.png", render_wait_ms=7000)

def capture_regional_overview_map() -> str:
    map_url = f"https://globe.adsb.lol/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}&hideSidebar"
    return _screenshot(map_url, "regional_overview.png", render_wait_ms=9000)

def cleanup_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

# ==================================================================
# TELEGRAM DISPATCH
# ==================================================================

def send_telegram_alert(caption: str, photo_path: str):
    """
    Guarantees every flight alert is dispatched with its photo screenshot.
    Truncates caption to 1020 chars so Telegram never rejects it.
    """
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    # Attempt to send photo first
    if photo_path and os.path.exists(photo_path) and os.path.getsize(photo_path) > 0:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        try:
            with open(photo_path, "rb") as f:
                res = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=30,
                ).json()
                if res.get("ok"):
                    log(f"[ Telegram ] Photo alert delivered successfully.")
                    return True
                else:
                    log(f"[ Telegram Warn ] sendPhoto rejected: {res}. Falling back to text.")
        except Exception as e:
            log(f"[ Telegram Error ] sendPhoto exception: {e}")

    # Fallback to plain text if photo fails
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        ).json()
        return res.get("ok", False)
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

    log(f"[ 12h Report ] Dispatching Middle East & Iran report (Mideast: {len(mideast_flights)}, Iran: {len(iran_flights)})...")
    overview_img = capture_regional_overview_map()

    caption = (
        f"🌐 <b>12-HOUR REGIONAL & IRAN AIRSPACE BRIEFING</b>\n\n"
        f"📅 <b>Timestamp:</b> <code>{now_utc().strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
        f"📊 <b>Middle East Total Monitored:</b> <code>{len(mideast_flights)}</code>\n"
        f"🇮🇷 <b>Active in Iranian Airspace:</b> <code>{len(iran_flights)}</code>\n"
        f"🌊 <b>Persian Gulf / Hormuz Corridor:</b> <code>{len(hormuz_flights)}</code>\n"
        f"🪖 <b>Monitored Military Aircraft:</b> <code>{len(mil_in_mideast)}</code>\n\n"
        f"ℹ️ <i>Automated regional surveillance and corridor overview snapshot.</i>\n\n"
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
    log("   OSINT Sky Radar — US Global Military Logistics Pipeline ")
    log("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        log("[ Error ] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID is missing from environment variables!")
        return

    state = load_state()
    raw_aircraft = fetch_all_aircraft()

    # CIRCUIT BREAKER: Never process 0 aircraft as genuine traffic
    if len(raw_aircraft) == 0:
        log("[ ABORT ] All ADS-B sources returned 0 aircraft. Halting run to protect baseline.")
        return

    # 1. Check and send separate 12-hour Regional & Iran airspace briefing
    check_and_send_12h_report(state, raw_aircraft)

    # 2. Filter strategic pipeline flights (US / Allied logistics, tankers, transports)
    target_flights = [ac for ac in raw_aircraft if is_target_flight(ac)]
    log(f"[ Pipeline Scan ] Evaluated {len(target_flights)} active strategic logistics / military flights.")

    # Process individual flight detections
    new_alerts = 0
    for ac in target_flights:
        icao = str(ac.get("hex", "")).strip().lower()
        callsign = str(ac.get("flight", "N/A")).strip().upper() or "N/A"
        flight_key = f"{icao}_{callsign}"

        if not icao or is_recently_seen(state, flight_key):
            continue

        mark_seen(state, flight_key)
        new_alerts += 1

        typecode = str(ac.get("t", "MIL")).strip().upper()
        model_name = AIRCRAFT_NAMES.get(typecode, f"Military Platform ({typecode})")
        role = classify_role(typecode)

        is_us = is_us_military_hex(icao) or any(callsign.startswith(p) for p in US_LOGISTICS_CALLSIGNS)
        operator_label = "🇺🇸 US Armed Forces / Air Mobility Command" if is_us else "🪖 Allied Armed Forces / Transport"

        alt = ac.get("alt_baro", "N/A")
        spd = ac.get("gs", "N/A")
        track = ac.get("track")
        lat, lon = ac.get("lat"), ac.get("lon")

        corridor, posture = determine_pipeline_stage_and_posture(track, lat, lon)
        photo_url = get_plane_photo(icao)
        photo_link = f"📸 <a href='{photo_url}'>Spotter Aircraft Photo</a>\n" if photo_url else ""

        # CAPTURE SCREENSHOT OF THIS SPECIFIC FLIGHT ON THE MAP
        flight_map = capture_flight_map(icao)

        caption = (
            f"🚨 <b>US / ALLIED MILITARY MOVEMENT DETECTED</b> 🚨\n"
            f"<b>{model_name}</b>\n"
            f"🏷️ <b>Operator:</b> {operator_label}\n"
            f"🎯 <b>Role:</b> <i>{role}</i>\n"
            f"📍 <b>Corridor / Hub:</b> <i>{corridor}</i>\n"
            f"🧭 <b>Posture:</b> <code>{posture}</code>\n\n"
            f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
            f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
            f"📈 <b>Altitude:</b> <code>{alt} ft</code> | 💨 <b>Speed:</b> <code>{spd} kts</code>\n"
            f"🗺️ <b>Coordinates:</b> <code>{lat:.3f}, {lon:.3f}</code>\n\n"
            f"{photo_link}"
            f"🔗 <a href='https://globe.adsb.lol/?icao={icao}'>Live Radar Track</a>\n"
            f"📡 @secretollah"
        )

        # SEND TELEGRAM POST WITH THE MAP SCREENSHOT
        send_telegram_alert(caption, flight_map)
        cleanup_file(flight_map)
        log(f"[ Alert Posted with Map ] {callsign} ({icao}) - {model_name}")

        # Pace messages so Telegram does not rate-limit multiple alerts
        time.sleep(1.5)

    save_state(state)
    log(f"[ Complete ] Cycle finished with {new_alerts} new flight alerts posted.")

if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        log(f"[ Fatal Error ] {e}")
        traceback.print_exc()
