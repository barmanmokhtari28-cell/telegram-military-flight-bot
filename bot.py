import os
import json
import traceback
import requests
from playwright.sync_api import sync_playwright

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Bounding Box covering Iran, Israel, Iraq, Syria, Persian Gulf & Strait of Hormuz
# Lat: 24.0°N to 39.0°N | Lon: 32.0°E to 65.0°E
LAT_MIN, LAT_MAX = 24.0, 39.0
LON_MIN, LON_MAX = 32.0, 65.0

# Target Military Aircraft Types (Cargo, Refuelers, Recon, Command)
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
        except Exception as e:
            print(f"[ Warning ] Could not load state file: {e}")
    return set()


def save_seen_flights(seen_set):
    recent_list = list(seen_set)[-300:]
    with open(STATE_FILE, "w") as f:
        json.dump(recent_list, f)


def send_telegram_text(text: str):
    """Sends text update to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, data=payload, timeout=10)
        return res.json()
    except Exception as e:
        print(f"[ Error ] Failed sending Telegram text: {e}")
        return None


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
    """Fetches high-res aircraft photo from Planespotters API"""
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
    """
    Fetches unfiltered aircraft transponder data from Airplanes.live open API.
    Does not rate-limit GitHub Actions.
    """
    center_lat = (LAT_MIN + LAT_MAX) / 2
    center_lon = (LON_MIN + LON_MAX) / 2
    
    url = f"https://api.airplanes.live/v2/point/{center_lat}/{center_lon}/1200"
    headers = {"User-Agent": "OSINT-Flight-Tracker/1.0"}
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json().get("ac", [])
        else:
            print(f"[ Warning ] Airplanes.live returned status code {res.status_code}")
    except Exception as e:
        print(f"[ Error ] Fetching ADS-B data failed: {e}")
    return []


def is_target_military(aircraft: dict) -> bool:
    """Filters aircraft for Cargo, Refueler, ISR, and Military Transports in regional bounding box"""
    lat = aircraft.get("lat")
    lon = aircraft.get("lon")
    
    if lat is None or lon is None:
        return False

    # Verify coordinates are in target region (Iran, Israel, Iraq, Strait of Hormuz)
    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return False

    icao = str(aircraft.get("hex", "")).lower().strip()
    callsign = str(aircraft.get("flight", "")).upper().strip()
    typecode = str(aircraft.get("t", "")).upper().strip()
    db_flags = aircraft.get("dbFlags", 0)

    # 1. US DoD Hex Range (starts with 'ae' or 'af') or military flag
    if icao.startswith("ae") or icao.startswith("af") or db_flags == 1:
        return True

    # 2. Match Target Cargo/Tanker/ISR aircraft type
    if any(t == typecode for t in TARGET_TYPES):
        return True

    # 3. Match Military/Charter Callsigns
    if any(callsign.startswith(prefix) for prefix in MIL_CALLSIGNS):
        return True

    return False


def run_tracker():
    print("==================================================")
    print("   OSINT Flight Radar Bot (Iran/Israel/Iraq/Hormuz)")
    print("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[ Critical Error ] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variables are missing!")
        return

    seen_flights = load_seen_flights()
    aircraft_list = fetch_airplanes_live_data()
    total_scanned = len(aircraft_list)
    print(f"[ Data Ingestion ] Scanned {total_scanned} active flights in target area.")

    military_matches = 0

    for ac in aircraft_list:
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
            print(f"[ MILITARY TARGET DETECTED ] Callsign: {callsign} | ICAO: {icao} | Type: {typecode}")

            if flight_key not in seen_flights:
                seen_flights.add(flight_key)

                # Capture map screenshot & aircraft photo
                screenshot_file = capture_map_screenshot(icao)
                photo_url = get_plane_photo(icao)
                photo_link = f"📸 <a href='{photo_url}'>View Aircraft Picture</a>\n" if photo_url else ""

                caption = (
                    f"🚨 <b>MILITARY / CARGO / TANKER DETECTED</b> 🚨\n"
                    f"📍 <i>Iran - Israel - Iraq - Hormuz Region</i>\n\n"
                    f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
                    f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                    f"🛩️ <b>Type:</b> <code>{typecode}</code>\n"
                    f"📈 <b>Altitude:</b> <code>{alt} ft</code>\n"
                    f"💨 <b>Ground Speed:</b> <code>{speed} kts</code>\n"
                    f"🗺️ <b>Coordinates:</b> <code>{lat}, {lon}</code>\n\n"
                    f"{photo_link}"
                    f"🔗 <a href='https://globe.airplanes.live/?icao={icao}'>Live Radar Track</a> | "
                    f"<a href='https://globe.adsbexchange.com/?icao={icao}'>ADSBexchange</a>"
                )

                res = send_telegram_alert(caption, screenshot_file, photo_url)
                print(f"[ Telegram Alert Sent ] Response: {res}")

    print(f"[ Scan Complete ] {total_scanned} aircraft evaluated. {military_matches} military/cargo matched.")

    # ALWAYS POST STATUS VERIFICATION MESSAGE TO TELEGRAM
    status_msg = (
        f"🛰️ <b>OSINT Regional Radar Active</b>\n"
        f"<b>Region:</b> Iran - Israel - Iraq - Strait of Hormuz\n"
        f"<b>Flights Scanned:</b> <code>{total_scanned}</code>\n"
        f"<b>Military / Cargo Detected:</b> <code>{military_matches}</code>"
    )
    status_res = send_telegram_text(status_msg)
    print(f"[ Status Alert Sent ] Response: {status_res}")

    save_seen_flights(seen_flights)


if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        print(f"[ Fatal Error ] Script crashed: {e}")
        traceback.print_exc()
