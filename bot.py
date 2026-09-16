import os
import sys
import json
import math
import traceback
from datetime import datetime, timezone, timedelta

import requests
from playwright.sync_api import sync_playwright

# ==================================================================
# CONFIG & SECRETS (Safe for public GitHub repo)
# ==================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Optional OpenSky credentials (can be added as GitHub Secrets if available)
OPENSKY_USER = os.getenv("OPENSKY_USER")
OPENSKY_PASS = os.getenv("OPENSKY_PASS")

# Regional bounding box: Eastern Mediterranean, Levant, Iraq, Iran, Gulf, Red Sea
LAT_MIN, LAT_MAX = 15.0, 39.0
LON_MIN, LON_MAX = 32.0, 63.0

# Strategic points covering key choke points, logistics hubs & corridors
REGION_POINTS = [
    ("Al Udeid / Persian Gulf / Qatar", 25.1, 51.3),
    ("Strait of Hormuz / S. Iran", 26.5, 56.0),
    ("Al Dhafra / UAE", 24.2, 54.5),
    ("Ali Al Salem / Kuwait / N. Gulf", 29.3, 47.5),
    ("Prince Sultan AB / Saudi Arabia", 24.1, 47.6),
    ("Iraq / Syria Border / Levant", 33.5, 42.0),
    ("Israel / Jordan / E. Med", 31.8, 35.2),
    ("Bab el-Mandeb / S. Red Sea", 13.0, 43.5),
    ("Tehran / Central Iran", 35.7, 51.4),
    ("Oman / Arabian Sea Approach", 23.5, 58.5),
]
POINT_RADIUS_NM = 250

HORMUZ_LAT_MIN, HORMUZ_LAT_MAX = 24.0, 30.0
HORMUZ_LON_MIN, HORMUZ_LON_MAX = 53.0, 60.0

OVERVIEW_LAT, OVERVIEW_LON, OVERVIEW_ZOOM = 27.5, 47.0, 5
STATE_FILE = "state.json"
REANNOUNCE_AFTER_HOURS = 8

def log(msg):
    print(msg, flush=True)

now_utc = lambda: datetime.now(timezone.utc)

# ==================================================================
# AIRCRAFT TYPE / CATEGORY DATA
# ==================================================================

AIRCRAFT_NAMES = {
    # Strategic Airlift / Heavy Cargo
    "C17": "C-17A Globemaster III (Strategic Airlifter)",
    "C5": "C-5M Super Galaxy (Heavy Airlifter)",
    "C130": "C-130 Hercules (Tactical Transport)",
    "C30J": "C-130J Super Hercules",
    "A400": "A400M Atlas (Heavy Transport)",
    "IL76": "Ilyushin Il-76 (Strategic Transport)",
    "AN124": "Antonov An-124 Ruslan",
    # Aerial Refueling Tankers
    "KC135": "KC-135R Stratotanker",
    "K35R": "KC-135R Stratotanker",
    "KC46": "KC-46A Pegasus",
    "KC10": "KC-10A Extender",
    "A332": "A330 MRTT (Multi-Role Tanker)",
    # ISR, Reconnaissance & Airborne Command
    "P8": "P-8A Poseidon (Maritime Recon/ASW)",
    "RC135": "RC-135 Rivet Joint / Combat Sent (SIGINT)",
    "E3TF": "E-3 Sentry (AWACS Command)",
    "E3": "E-3 Sentry (AWACS Command)",
    "E8": "E-8C Joint STARS (Ground Surveillance)",
    "E2": "E-2D Advanced Hawkeye (Carrier AEW)",
    "RQ4": "RQ-4 Global Hawk / MQ-4C Triton (HALE Drone)",
    "MQ9": "MQ-9 Reaper (Armed Recon Drone)",
    "U2": "U-2S Dragon Lady (High Altitude Recon)",
    # Combat & Strike
    "B52": "B-52H Stratofortress",
    "B1": "B-1B Lancer",
    "B2": "B-2A Spirit (Stealth Bomber)",
    "F15": "F-15 Strike Eagle",
    "F16": "F-16 Fighting Falcon",
    "F22": "F-22A Raptor",
    "F35": "F-35 Lightning II",
    "F18": "F/A-18 Super Hornet",
    "A10": "A-10C Thunderbolt II",
    # Executive / Command
    "VC25": "Air Force One (VC-25)",
    "C32": "Boeing C-32A (Air Force Two / VIP)",
    "C40": "Boeing C-40 Clipper",
    "E4B": "E-4B Nightwatch (National Airborne Ops Center)",
}

US_CALLSIGN_PREFIXES = [
    "RCH", "REACH", "MOOSE", "SLAM", "ORDER",  # Air Mobility Command (AMC)
    "LAGR", "NCHO", "GOLD", "CLEAN", "BOBBY", "PEARL", "SHELL",  # Tankers
    "FORTE", "HOMER", "OLIVE", "COBRA", "PYTHON", "SNOOP", "JAKE",  # ISR / Recon
    "DOOM", "DEATH", "MYTEE", "BONE", "DARK", "SKULL",  # Bombers
    "NAVY", "TOPCAT", "VNDL", "GOTO",  # US Navy
    "PAT", "EVAC", "SAM", "EXEC", "SPAR",  # Priority Logistics & Command
]

ALLIED_MIL_PREFIXES = [
    "RRR", "ASCOT", "TARTAN",  # Royal Air Force
    "IAF", "ISF",              # Israeli Air Force
    "CTM", "COTAM",            # French Air Force
    "GAF", "GAM",              # German Air Force
    "NATO", "NAF",
]

def is_us_military_hex(icao_hex: str) -> bool:
    """Mode-S hex range allocated to US DoD / Military."""
    try:
        val = int(icao_hex, 16)
        # ae0000 to aeffff or af0000 to afffff
        return (0xAE0000 <= val <= 0xAFFFFF)
    except Exception:
        return False

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
    return any(s["key"] == flight_key for s in state.get("seen_flights", []))

def mark_seen(state, flight_key):
    state.setdefault("seen_flights", []).append({"key": flight_key, "ts": now_utc().isoformat()})

# ==================================================================
# DATA FETCHING (ADSB.lol, ADSB.fi & OpenSky)
# ==================================================================

def fetch_from_adsb_lol():
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    # 1. Global military feed
    try:
        url = "https://api.adsb.lol/v2/mil"
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            ac = res.json().get("ac", [])
            log(f"[ adsb.lol ] Global mil endpoint returned {len(ac)} aircraft")
            results.extend(ac)
        else:
            log(f"[ adsb.lol ] Global mil returned HTTP {res.status_code}")
    except Exception as e:
        log(f"[ Warn ] adsb.lol mil endpoint failed: {e}")

    # 2. Regional point queries
    for label, lat, lon in REGION_POINTS:
        try:
            url = f"https://api.adsb.lol/v2/point/{lat}/{lon}/{POINT_RADIUS_NM}"
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                ac = res.json().get("ac", [])
                log(f"[ adsb.lol ] {label} -> {len(ac)} aircraft")
                results.extend(ac)
        except Exception as e:
            log(f"[ Warn ] adsb.lol point failed for {label}: {e}")
    return results

def fetch_from_adsb_fi():
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MilitaryFlightTracker/2.0)", "Accept": "application/json"}
    try:
        url = "https://opendata.adsb.fi/api/v2/mil"
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            ac = res.json().get("ac", [])
            log(f"[ adsb.fi ] Military endpoint returned {len(ac)} aircraft")
            results.extend(ac)
    except Exception as e:
        log(f"[ Warn ] adsb.fi failed: {e}")
    return results

def fetch_from_opensky():
    """Fallback query to OpenSky Network covering the regional bounding box."""
    results = []
    headers = {"User-Agent": "MilitaryFlightTracker/2.0"}
    auth = (OPENSKY_USER, OPENSKY_PASS) if (OPENSKY_USER and OPENSKY_PASS) else None
    url = f"https://opensky-network.org/api/states/all?lamin={LAT_MIN}&lomin={LON_MIN}&lamax={LAT_MAX}&lomax={LON_MAX}"
    try:
        res = requests.get(url, headers=headers, auth=auth, timeout=15)
        if res.status_code == 200:
            data = res.json()
            states = data.get("states", []) or []
            log(f"[ OpenSky ] Regional query returned {len(states)} aircraft")
            for s in states:
                # OpenSky format: [icao24, callsign, origin_country, time_pos, last_contact, lon, lat, baro_alt, on_ground, vel, track, ...]
                results.append({
                    "hex": str(s[0]).strip().lower(),
                    "flight": str(s[1]).strip() if s[1] else "",
                    "lat": s[6],
                    "lon": s[5],
                    "alt_baro": int(s[7] * 3.28084) if s[7] is not None else None,
                    "gs": int(s[9] * 1.94384) if s[9] is not None else None,
                    "track": s[10],
                    "t": "",  # OpenSky basic state vector doesn't have aircraft type
                    "dbFlags": 1 if s[2] == "United States" and is_us_military_hex(str(s[0])) else 0,
                })
    except Exception as e:
        log(f"[ Warn ] OpenSky fetch failed: {e}")
    return results

def fetch_all_aircraft():
    """Collects and deduplicates aircraft across active endpoints."""
    raw = []
    raw.extend(fetch_from_adsb_lol())
    raw.extend(fetch_from_adsb_fi())

    # If primary community feeds were blocked/down, fallback to OpenSky
    if len(raw) == 0:
        log("[ Fallback ] Primary ADS-B sources yielded 0. Polling OpenSky Network...")
        raw.extend(fetch_from_opensky())

    dedup = {}
    for ac in raw:
        hex_code = str(ac.get("hex", "")).strip().lower()
        if hex_code and hex_code not in dedup:
            dedup[hex_code] = ac

    log(f"[ Fetch ] Total unique regional aircraft fetched: {len(dedup)}")
    return list(dedup.values())

def get_plane_photo(icao: str):
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{icao}"
        headers = {"User-Agent": "MilitaryFlightTracker/2.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        if res.get("photos"):
            return res["photos"][0]["thumbnail_large"]["src"]
    except Exception:
        pass
    return None

# ==================================================================
# CLASSIFICATION & FLIGHT ORIENTATION
# ==================================================================

def in_region(lat, lon) -> bool:
    return lat is not None and lon is not None and LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX

def in_hormuz_corridor(lat, lon) -> bool:
    return (lat is not None and lon is not None
            and HORMUZ_LAT_MIN <= lat <= HORMUZ_LAT_MAX and HORMUZ_LON_MIN <= lon <= HORMUZ_LON_MAX)

def classify_role(typecode: str):
    typecode = typecode.upper().strip()
    if typecode in ["C17", "C5", "C130", "C30J", "A400", "IL76", "AN124"]:
        return "STRATEGIC AIRLIFT / LOGISTICS"
    if typecode in ["KC135", "K35R", "KC46", "KC10", "A332"]:
        return "AERIAL REFUELING TANKER"
    if typecode in ["P8", "RC135", "E3TF", "E3", "E8", "E2", "RQ4", "MQ9", "U2"]:
        return "ISR & RECONNAISSANCE"
    if typecode in ["B52", "B1", "B2", "F15", "F16", "F22", "F35", "F18", "A10"]:
        return "COMBAT / STRIKE ASSET"
    if typecode in ["VC25", "C32", "C40", "E4B"]:
        return "VIP / STRATEGIC COMMAND"
    return "TACTICAL MILITARY SUPPORT"

def is_target_military(ac: dict) -> bool:
    icao = str(ac.get("hex", "")).strip().lower()
    callsign = str(ac.get("flight", "")).strip().upper()
    typecode = str(ac.get("t", "")).strip().upper()
    db_flags = ac.get("dbFlags", 0)

    if is_us_military_hex(icao) or db_flags == 1:
        return True
    if typecode in AIRCRAFT_NAMES:
        return True
    if any(callsign.startswith(p) for p in US_CALLSIGN_PREFIXES + ALLIED_MIL_PREFIXES):
        return True
    return False

def determine_movement_posture(track, lat, lon):
    """Evaluates whether an aircraft is reinforcing into the theater, exiting, or patrolling."""
    if track is None:
        return "Track Bearing Unknown"
    # Heading 45° to 135° = Eastbound (inbound from Europe/US towards Persian Gulf)
    if 45 <= track <= 135:
        return "➡️ INBOUND / REINFORCEMENT CORRIDOR (Heading East toward Gulf/Levant)"
    # Heading 225° to 315° = Westbound (egress / return to bases in Europe/US)
    elif 225 <= track <= 315:
        return "⬅️ OUTBOUND / EGRESS TRANSIT (Heading West)"
    elif 136 <= track <= 224:
        return "⬇️ SOUTHBOUND TRANSIT (Red Sea / Arabian Gulf)"
    else:
        return "🔄 THEATER PATROL / ORBIT POSTURE"

def determine_airspace_sector(lat, lon) -> str:
    if lat is None or lon is None:
        return "🌐 Regional Airspace"
    if HORMUZ_LAT_MIN <= lat <= HORMUZ_LAT_MAX and HORMUZ_LON_MIN <= lon <= HORMUZ_LON_MAX:
        return "🌊 Strait of Hormuz & Persian Gulf Chokepoint"
    if 25.0 <= lat <= 39.0 and 45.0 <= lon <= 63.0:
        return "🇮🇷 Iranian Airspace / Boundary"
    if 29.0 <= lat <= 37.0 and 38.0 <= lon <= 46.0:
        return "🇮🇶 Iraqi Airspace Corridor"
    if 29.0 <= lat <= 34.0 and 34.0 <= lon <= 37.0:
        return "🇮🇱 Israel / Levant Forward Sector"
    if 22.0 <= lat <= 27.0 and 46.0 <= lon <= 52.0:
        return "🇸🇦 Saudi Arabia Central/Eastern Sector"
    if 22.5 <= lat <= 26.5 and 50.5 <= lon <= 56.5:
        return "🇦🇪🇶🇦 Al Udeid / Al Dhafra Base Airspace (UAE/Qatar)"
    if 13.0 <= lat <= 22.0 and 38.0 <= lon <= 45.0:
        return "🔴 Red Sea / Bab el-Mandeb Strategic Corridor"
    return "🌐 Regional Middle East Sector"

# ==================================================================
# SCREENSHOTS (Playwright)
# ==================================================================

def _screenshot(map_url: str, filename: str, render_wait_ms: int = 7000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(map_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(render_wait_ms)
            page.screenshot(path=filename)
            browser.close()
            log(f"[ Playwright ] Screenshot saved: {filename}")
            return filename
    except Exception as e:
        log(f"[ Warn ] Screenshot failed for {map_url}: {e}")
        return None

def capture_flight_map(icao: str) -> str:
    map_url = f"https://globe.adsb.lol/?icao={icao.lower()}"
    return _screenshot(map_url, f"map_{icao}.png", render_wait_ms=5000)

def capture_regional_overview_map() -> str:
    map_url = f"https://globe.adsb.lol/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}"
    return _screenshot(map_url, "overview.png", render_wait_ms=6000)

def cleanup_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

# ==================================================================
# TELEGRAM MESSAGING
# ==================================================================

def send_telegram_media(caption, photo_paths):
    valid = [p for p in photo_paths if p and os.path.exists(p)]
    if not valid:
        # Fallback to plain text
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        return

    if len(valid) == 1:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(valid[0], "rb") as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": f}, timeout=25)
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
        media = []
        files = {}
        opened = []
        try:
            for i, p in enumerate(valid):
                k = f"p_{i}"
                fh = open(p, "rb")
                opened.append(fh)
                files[k] = fh
                item = {"type": "photo", "media": f"attach://{k}"}
                if i == 0:
                    item["caption"] = caption
                    item["parse_mode"] = "HTML"
                media.append(item)
            requests.post(url, data={"chat_id": TELEGRAM_CHANNEL_ID, "media": json.dumps(media)}, files=files, timeout=30)
        finally:
            for fh in opened:
                fh.close()

# ==================================================================
# MAIN ORCHESTRATION
# ==================================================================

def run_tracker():
    log("==================================================")
    log("   OSINT Sky Radar — US & Allied Mideast Logistics Tracker ")
    log("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        log("[ Error ] Telegram credentials missing. Check GitHub Repository Secrets!")
        return

    state = load_state()
    raw_aircraft = fetch_all_aircraft()

    # CRITICAL GUARD: Never process zero aircraft as genuine status (prevents false alerts)
    if len(raw_aircraft) == 0:
        log("[ ABORT ] All ADS-B endpoints returned 0 aircraft (likely network/rate limit). Preserving state without alerts.")
        return

    valid_flights = [ac for ac in raw_aircraft if in_region(ac.get("lat"), ac.get("lon"))]
    log(f"[ Scan ] Filtered {len(valid_flights)} aircraft inside Middle East theater bounding box.")

    mil_flights = [ac for ac in valid_flights if is_target_military(ac)]
    civil_hormuz_flights = [ac for ac in valid_flights if not is_target_military(ac) and in_hormuz_corridor(ac.get("lat"), ac.get("lon"))]

    log(f"[ Status ] Active military assets: {len(mil_flights)} | Civil Gulf traffic: {len(civil_hormuz_flights)}")

    # Update baseline logs
    state["mil_snapshot_log"].append({"ts": now_utc().isoformat(), "count": len(mil_flights)})
    state["civil_corridor_log"].append({"ts": now_utc().isoformat(), "count": len(civil_hormuz_flights)})

    overview_img = capture_regional_overview_map()

    try:
        new_alerts = 0
        for ac in mil_flights:
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
            is_us = is_us_military_hex(icao) or any(callsign.startswith(p) for p in US_CALLSIGN_PREFIXES)
            operator_label = "🇺🇸 US Armed Forces / Air Mobility Command" if is_us else "🪖 Allied / Regional Military"

            alt = ac.get("alt_baro", "N/A")
            spd = ac.get("gs", "N/A")
            track = ac.get("track")
            lat, lon = ac.get("lat"), ac.get("lon")

            posture = determine_movement_posture(track, lat, lon)
            sector = determine_airspace_sector(lat, lon)
            photo_url = get_plane_photo(icao)
            photo_link = f"📸 <a href='{photo_url}'>Spotter Aircraft Photo</a>\n" if photo_url else ""

            flight_map = capture_flight_map(icao)

            caption = (
                f"🚨 <b>MILITARY FLIGHT DETECTED</b> 🚨\n"
                f"<b>{model_name}</b>\n"
                f"🏷️ <b>Operator:</b> {operator_label}\n"
                f"🎯 <b>Mission Role:</b> <i>{role}</i>\n"
                f"🧭 <b>Posture:</b> <code>{posture}</code>\n"
                f"📍 <b>Airspace:</b> <i>{sector}</i>\n\n"
                f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
                f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                f"📈 <b>Altitude:</b> <code>{alt} ft</code> | 💨 <b>Speed:</b> <code>{spd} kts</code>\n"
                f"🗺️ <b>Coords:</b> <code>{lat:.3f}, {lon:.3f}</code>\n\n"
                f"{photo_link}"
                f"🔗 <a href='https://globe.adsb.lol/?icao={icao}'>Live Radar Track</a>\n"
                f"📡 @secretollah"
            )

            photos = [p for p in [overview_img, flight_map] if p]
            send_telegram_media(caption, photos)
            cleanup_file(flight_map)
            log(f"[ Alert Posted ] {callsign} ({icao}) - {model_name}")

        # Routine Heartbeat (only post if no alerts and >2 hours since last heartbeat)
        if new_alerts == 0:
            last_hb = state.get("last_fallback_post_ts")
            should_hb = (last_hb is None or datetime.fromisoformat(last_hb) < now_utc() - timedelta(hours=3))
            if should_hb and len(valid_flights) > 10:  # Validates that scan was healthy
                hb_caption = (
                    f"🌐 <b>REGIONAL AIRSPACE SURVEILLANCE STATUS</b>\n\n"
                    f"ℹ️ <i>Routine automated scan — Middle East theater.</i>\n"
                    f"📊 <b>Monitored Aircraft:</b> <code>{len(valid_flights)}</code>\n"
                    f"🪖 <b>Active Military Tracks:</b> <code>{len(mil_flights)}</code>\n"
                    f"🌊 <b>Gulf Civilian Corridor:</b> <code>{len(civil_hormuz_flights)}</code>\n\n"
                    f"🔗 <a href='https://globe.adsb.lol/?lat={OVERVIEW_LAT}&lon={OVERVIEW_LON}&zoom={OVERVIEW_ZOOM}'>Live Regional Map</a>\n"
                    f"📡 @secretollah"
                )
                send_telegram_media(hb_caption, [overview_img] if overview_img else [])
                state["last_fallback_post_ts"] = now_utc().isoformat()
                log("[ Heartbeat Posted ] Routine surveillance status broadcasted.")

    finally:
        cleanup_file(overview_img)

    save_state(state)
    log("[ Complete ] Cycle finished.")

if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        log(f"[ Fatal Error ] {e}")
        traceback.print_exc()
