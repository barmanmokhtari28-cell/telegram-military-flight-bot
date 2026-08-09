import os
import json
import requests
from playwright.sync_api import sync_playwright

# Configurations
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Bounding box for Middle East / Strait of Hormuz
LAT_MIN, LAT_MAX = 12.0, 40.0
LON_MIN, LON_MAX = 32.0, 65.0

# Expanded US Military & Allied Transport/Cargo Callsign Prefixes
US_MIL_CALLSIGNS = [
    "RCH", "REACH", "DUKE", "PAT", "EVAC", "CNV", "TOPCAT", 
    "SNOOP", "JAKE", "SAM", "SPAR", "TEAL", "GOLD", "CLEAN", 
    "NAVY", "EXEC", "DOOM", "HOSER", "TUF", "BOEING"
]

STATE_FILE = "seen_flights.json"


def load_seen_flights():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_flights(seen_set):
    recent_list = list(seen_set)[-200:]
    with open(STATE_FILE, "w") as f:
        json.dump(recent_list, f)


def capture_map_screenshot(icao: str) -> str:
    """Takes a live radar screenshot using Playwright"""
    map_url = f"https://globe.adsbexchange.com/?icao={icao.lower()}"
    screenshot_filename = f"map_{icao}.png"
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(map_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)
            page.screenshot(path=screenshot_filename)
            browser.close()
            return screenshot_filename
    except Exception as e:
        print(f"[ Error ] Screenshot failed for {icao}: {e}")
        return None


def get_plane_photo(icao: str) -> str:
    """Fetches plane photo from Planespotters API (Free)"""
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
    else:
        # Text-only alert fallback
        text_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": caption,
            "parse_mode": "HTML"
        }
        res = requests.post(text_url, data=payload)
        return res.json()


def fetch_opensky_data():
    """OpenSky Network Free Public API"""
    url = f"https://opensky-network.org/api/states/all?lamin={LAT_MIN}&lamax={LAT_MAX}&lomin={LON_MIN}&lomax={LON_MAX}"
    try:
        res = requests.get(url, timeout=20)
        if res.status_code == 200:
            return res.json().get("states", [])
    except Exception as e:
        print(f"[ Error ] OpenSky API fetch failed: {e}")
    return []


def is_us_military(icao: str, callsign: str, country: str) -> bool:
    """
    US Military Transponders:
    1. Hex ranges starting with 'ae' or 'af' (US DoD Range).
    2. Callsign prefixes matching US Air Force / Navy / Army transport units.
    """
    icao = icao.lower().strip()
    callsign = callsign.upper().strip()
    
    # 1. Check US DoD Hex allocation (AE0000 - AFFFFF)
    if icao.startswith("ae") or icao.startswith("af"):
        return True
    
    # 2. Check military callsign prefixes
    if any(callsign.startswith(prefix) for prefix in US_MIL_CALLSIGNS):
        return True
        
    return False


def run_tracker():
    seen_flights = load_seen_flights()
    states = fetch_opensky_data()
    print(f"[ Info ] OpenSky returned {len(states) if states else 0} flights in target box.")
    
    if not states:
        print("[ Info ] No aircraft active in bounding box currently.")
        return

    military_matches = 0

    for s in states:
        icao = str(s[0]).strip()
        callsign = str(s[1]).strip() if s[1] else "N/A"
        country = str(s[2]).strip()
        lon = s[5]
        lat = s[6]
        alt_meters = s[7]
        speed_mps = s[9]

        if not lat or not lon:
            continue

        alt_ft = int(alt_meters * 3.28084) if alt_meters else "N/A"
        speed_kts = int(speed_mps * 1.94384) if speed_mps else "N/A"

        flight_key = f"{icao}_{callsign}"

        if is_us_military(icao, callsign, country):
            military_matches += 1
            print(f"[ MILITARY TARGET FOUND ] Callsign: {callsign} | ICAO: {icao} | Country: {country}")
            
            if flight_key not in seen_flights:
                seen_flights.add(flight_key)
                
                # Take screenshot & fetch plane photo
                screenshot_file = capture_map_screenshot(icao)
                photo_url = get_plane_photo(icao)
                photo_link = f"📸 <a href='{photo_url}'>View Plane Photo</a>\n" if photo_url else ""

                caption = (
                    f"🚨 <b>US MILITARY FLIGHT DETECTED</b> 🚨\n"
                    f"📍 <i>Middle East / Strait of Hormuz</i>\n\n"
                    f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
                    f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                    f"🌍 <b>Country:</b> {country}\n"
                    f"📈 <b>Altitude:</b> <code>{alt_ft} ft</code>\n"
                    f"💨 <b>Speed:</b> <code>{speed_kts} kts</code>\n"
                    f"🗺️ <b>Coords:</b> <code>{lat}, {lon}</code>\n\n"
                    f"{photo_link}"
                    f"🔗 <a href='https://globe.adsbexchange.com/?icao={icao}'>Live Radar Track</a>"
                )

                res = send_telegram_alert(caption, screenshot_file, photo_url)
                print(f"[ Telegram Response ] {res}")

    print(f"[ Summary ] Processed {len(states)} flights. Found {military_matches} US military aircraft.")
    save_seen_flights(seen_flights)


if __name__ == "__main__":
    run_tracker()
