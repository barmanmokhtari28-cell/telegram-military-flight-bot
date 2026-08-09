import os
import json
import random
import traceback
import requests
from playwright.sync_api import sync_playwright

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Bounding Box covering Iran, Israel, Iraq, Syria, Persian Gulf & Strait of Hormuz
LAT_MIN, LAT_MAX = 24.0, 39.0
LON_MIN, LON_MAX = 32.0, 65.0

# Military, Refueler, Cargo, Drone & Recon Types
TARGET_TYPES = [
    "C17", "C130", "C5", "K35R", "KC10", "KC46", "A332", "A400", 
    "IL76", "AN124", "RC135", "P8", "E3TF", "E8", "E2", "VC25", 
    "C32", "C40", "E4B", "RQ4", "MQ9", "U2", "B350"
]

# Military & Charter Callsign Prefixes
MIL_CALLSIGNS = [
    "RCH", "REACH", "DUKE", "PAT", "EVAC", "CNV", "TOPCAT", "SNOOP", 
    "JAKE", "SAM", "SPAR", "TEAL", "GOLD", "CLEAN", "NAVY", "EXEC", 
    "DOOM", "HOSER", "TUF", "BOEING", "CMB", "CAMBER", "GTI", "CKS", 
    "NATO", "RRR", "CTM", "IAF", "LAGR", "NCHO", "FORTE", "HOMER"
]

STATE_FILE = "seen_flights.json"


def load_seen_flights():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen_flights(seen_set):
    recent_list = list(seen_set)[-300:]
    with open(STATE_FILE, "w") as f:
        json.dump(recent_list, f)


def capture_map_screenshot(icao: str) -> str:
    """Takes a live radar snapshot using Playwright"""
    map_url = f"https://globe.airplanes.live/?icao={icao.lower()}"
    screenshot_filename = f"map_{icao}.png"
    
    try:
        print(f"[ Playwright ] Opening radar map for {icao}...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(map_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)
            page.screenshot(path=screenshot_filename)
            browser.close()
            print(f"[ Playwright ] Map snapshot captured: {screenshot_filename}")
            return screenshot_filename
    except Exception as e:
        print(f"[ Error ] Map screenshot failed for {icao}: {e}")
        return None


def get_plane_photo(icao: str) -> str:
    """Fetches plane photo from Planespotters API"""
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{icao}"
        headers = {"User-Agent": "OSINT-Flight-Bot/1.0"}
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("photos"):
            return res["photos"][0]["thumbnail_large"]["src"]
    except Exception:
        pass
    return None


def send_telegram_alert(caption: str, screenshot_path: str = None, photo_url: str = None):
    """Posts photo/screenshot + details caption to Telegram channel"""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    
    if screenshot_path and os.path.exists(screenshot_path):
        with open(screenshot_path, "rb") as image_file:
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML"
            }
            files = {"photo": image_file}
            res = requests.post(api_url, data=payload, files=files)
            os.remove(screenshot_path)
            return res.json()
    elif photo_url:
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML"
        }
        res = requests.post(api_url, data=payload)
        return res.json()


def fetch_airplanes_live_data():
    """Fetches active regional flights from Airplanes.live open API"""
    center_lat = (LAT_MIN + LAT_MAX) / 2
    center_lon = (LON_MIN + LON_MAX) / 2
    
    url = f"https://api.airplanes.live/v2/point/{center_lat}/{center_lon}/1200"
    headers = {"User-Agent": "OSINT-Flight-Tracker/1.0"}
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json().get("ac", [])
    except Exception as e:
        print(f"[ Error ] Fetching ADS-B data failed: {e}")
    return []


def is_target_military(aircraft: dict) -> bool:
    """Filters aircraft for Military, Cargo, Refueler, Recon in regional bounding box"""
    lat = aircraft.get("lat")
    lon = aircraft.get("lon")
    
    if lat is None or lon is None:
        return False

    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return False

    icao = str(aircraft.get("hex", "")).lower().strip()
    callsign = str(aircraft.get("flight", "")).upper().strip()
    typecode = str(aircraft.get("t", "")).upper().strip()
    db_flags = aircraft.get("dbFlags", 0)

    if icao.startswith("ae") or icao.startswith("af") or db_flags == 1:
        return True

    if any(t == typecode for t in TARGET_TYPES):
        return True

    if any(callsign.startswith(prefix) for prefix in MIL_CALLSIGNS):
        return True

    return False


def run_tracker():
    print("==================================================")
    print("   OSINT Flight Tracker (Iran/Israel/Iraq/Hormuz) ")
    print("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[ Error ] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID missing!")
        return

    seen_flights = load_seen_flights()
    aircraft_list = fetch_airplanes_live_data()
    
    # Filter flights strictly within lat/lon coordinates
    valid_flights = [
        ac for ac in aircraft_list 
        if ac.get("lat") and ac.get("lon") and (LAT_MIN <= ac.get("lat") <= LAT_MAX) and (LON_MIN <= ac.get("lon") <= LON_MAX)
    ]
    
    total_scanned = len(valid_flights)
    print(f"[ Scan ] Found {total_scanned} active regional flights.")

    military_matches = 0

    # 1. CHECK FOR MILITARY / CARGO FLIGHTS FIRST
    for ac in valid_flights:
        icao = str(ac.get("hex", "")).strip()
        callsign = str(ac.get("flight", "N/A")).strip()
        typecode = str(ac.get("t", "Unknown Type")).strip()
        alt = ac.get("alt_baro", "N/A")
        speed = ac.get("gs", "N/A")
        lat = ac.get("lat")
        lon = ac.get("lon")
        country = ac.get("ownOp", ac.get("r", "Unknown"))

        if not icao:
            continue

        flight_key = f"{icao}_{callsign}"

        if is_target_military(ac):
            military_matches += 1
            print(f"[ MILITARY DETECTED ] Callsign: {callsign} | ICAO: {icao}")

            if flight_key not in seen_flights:
                seen_flights.add(flight_key)

                screenshot_file = capture_map_screenshot(icao)
                photo_url = get_plane_photo(icao)
                photo_link = f"📸 <a href='{photo_url}'>View Aircraft Photo</a>\n" if photo_url else ""

                caption = (
                    f"🚨 <b>MILITARY / CARGO / REFUEL FLIGHT DETECTED</b> 🚨\n"
                    f"📍 <i>Iran - Israel - Iraq - Strait of Hormuz</i>\n\n"
                    f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
                    f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                    f"🛩️ <b>Type:</b> <code>{typecode}</code>\n"
                    f"📈 <b>Altitude:</b> <code>{alt} ft</code>\n"
                    f"💨 <b>Speed:</b> <code>{speed} kts</code>\n"
                    f"🗺️ <b>Coordinates:</b> <code>{lat}, {lon}</code>\n\n"
                    f"{photo_link}"
                    f"🔗 <a href='https://globe.airplanes.live/?icao={icao}'>Live Radar Track</a>"
                )

                res = send_telegram_alert(caption, screenshot_file, photo_url)
                print(f"[ Alert Posted ] {res}")

    # 2. FALLBACK: IF NO MILITARY FLIGHT DETECTED, SCREENSHOT A REGIONAL ACTIVE FLIGHT
    if military_matches == 0 and valid_flights:
        print("[ Info ] No military flights found. Capturing regional active flight screenshot...")
        
        # Pick a flight (prefer high altitude or active flights)
        selected_ac = random.choice(valid_flights)
        
        icao = str(selected_ac.get("hex", "")).strip()
        callsign = str(selected_ac.get("flight", "N/A")).strip()
        typecode = str(selected_ac.get("t", "Commercial / General Aviation")).strip()
        alt = selected_ac.get("alt_baro", "N/A")
        speed = selected_ac.get("gs", "N/A")
        lat = selected_ac.get("lat")
        lon = selected_ac.get("lon")

        screenshot_file = capture_map_screenshot(icao)
        photo_url = get_plane_photo(icao)
        photo_link = f"📸 <a href='{photo_url}'>View Aircraft Photo</a>\n" if photo_url else ""

        alt_ft = int(alt * 3.28084) if isinstance(alt, (int, float)) else alt
        speed_kts = int(speed * 1.94384) if isinstance(speed, (int, float)) else speed

        caption = (
            f"📡 <b>REGIONAL AIRSPACE RADAR UPDATE</b>\n"
            f"📍 <i>Iran - Israel - Iraq - Strait of Hormuz</i>\n\n"
            f"ℹ️ <i>No active military transponders detected in this scan.</i>\n"
            f"📊 <b>Total Active Regional Flights:</b> <code>{total_scanned}</code>\n\n"
            f"✈️ <b>Featured Active Flight:</b> <code>{callsign}</code> ({typecode})\n"
            f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
            f"📈 <b>Altitude:</b> <code>{alt_ft} ft</code> | 💨 <b>Speed:</b> <code>{speed_kts} kts</code>\n"
            f"🗺️ <b>Coords:</b> <code>{lat}, {lon}</code>\n\n"
            f"{photo_link}"
            f"🔗 <a href='https://globe.airplanes.live/?icao={icao}'>Live Regional Map</a>"
        )

        res = send_telegram_alert(caption, screenshot_file, photo_url)
        print(f"[ Regional Map Update Posted ] {res}")

    save_seen_flights(seen_flights)


if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        print(f"[ Fatal Error ] {e}")
        traceback.print_exc()
