import os
import json
import random
import traceback
import requests
from playwright.sync_api import sync_playwright

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Regional Bounding Box
LAT_MIN, LAT_MAX = 24.0, 39.0
LON_MIN, LON_MAX = 32.0, 65.0

# Middle East / Iran Regional Overview Coordinates
REGIONAL_CENTER_LAT = 32.0
REGIONAL_CENTER_LON = 53.0

# Friendly Aircraft Name Mapping for Exact Title Description
AIRCRAFT_NAMES = {
    "C17": "C-17A Globemaster III",
    "C130": "C-130 Hercules",
    "C30J": "C-130J Super Hercules",
    "C5": "C-5M Super Galaxy",
    "K35R": "KC-135 Stratotanker",
    "KC135": "KC-135 Stratotanker",
    "KC10": "KC-10 Extender",
    "KC46": "KC-46 Pegasus",
    "A332": "A330 MRTT Tanker",
    "A400": "A400M Atlas",
    "P8": "P-8A Poseidon ISR",
    "RC135": "RC-135 Rivet Joint",
    "E3TF": "E-3 Sentry AWACS",
    "E3": "E-3 Sentry AWACS",
    "E8": "E-8 Joint STARS",
    "E2": "E-2 Hawkeye",
    "IL76": "Ilyushin Il-76 Cargo",
    "AN124": "Antonov An-124 Heavy Cargo",
    "RQ4": "RQ-4 Global Hawk Drone",
    "MQ9": "MQ-9 Reaper Drone",
    "VC25": "Air Force One (VC-25)",
    "C32": "Boeing C-32 VIP",
    "C40": "Boeing C-40 Clipper"
}

# Target Aircraft Types
TARGET_TYPES = list(AIRCRAFT_NAMES.keys())

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


def get_exact_aircraft_title(typecode: str, callsign: str) -> str:
    """Returns exact descriptive flight title for the caption header"""
    typecode = typecode.upper().strip()
    callsign = callsign.upper().strip()
    
    exact_name = AIRCRAFT_NAMES.get(typecode, typecode if typecode else "Military Aircraft")
    
    if callsign and callsign != "N/A":
        return f"🚨 {exact_name} ({callsign}) DETECTED 🚨"
    else:
        return f"🚨 {exact_name} DETECTED 🚨"


def determine_airspace_sector(lat, lon) -> str:
    """Calculates a dynamic, informative strategic airspace label based on coordinates"""
    if lat is None or lon is None:
        return "🌐 Middle East Strategic Airspace"

    if 24.0 <= lat <= 30.0 and 53.0 <= lon <= 60.0:
        return "🌊 Strait of Hormuz & Persian Gulf Maritime Corridor"
    elif 25.0 <= lat <= 39.0 and 45.0 <= lon <= 64.0:
        return "🇮🇷 Iranian Airspace & Central Sector"
    elif 29.0 <= lat <= 37.0 and 38.0 <= lon <= 46.0:
        return "🇮🇶 Iraqi Airspace & Northern Levant Zone"
    elif 29.0 <= lat <= 34.0 and 34.0 <= lon <= 37.0:
        return "🇮🇱 Levant & Eastern Mediterranean Air Corridor"
    else:
        return "🌐 Middle East Regional Airspace Sector"


def capture_map_screenshot(icao: str) -> str:
    """Takes a live radar snapshot of a specific flight using Playwright"""
    map_url = f"https://globe.airplanes.live/?icao={icao.lower()}"
    screenshot_filename = f"map_flight_{icao}.png"
    
    try:
        print(f"[ Playwright ] Capturing flight track map for {icao}...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(map_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)
            page.screenshot(path=screenshot_filename)
            browser.close()
            return screenshot_filename
    except Exception as e:
        print(f"[ Error ] Flight map screenshot failed for {icao}: {e}")
        return None


def capture_regional_overview_map() -> str:
    """Captures a wide-angle radar map of Iran & Middle East showing all live flights"""
    map_url = f"https://globe.airplanes.live/?lat={REGIONAL_CENTER_LAT}&lon={REGIONAL_CENTER_LON}&zoom=5"
    screenshot_filename = "regional_overview_iran_me.png"
    
    try:
        print("[ Playwright ] Capturing wide-angle Iran & Middle East regional flight map...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(map_url, wait_until="networkidle", timeout=35000)
            page.wait_for_timeout(6000)
            page.screenshot(path=screenshot_filename)
            browser.close()
            return screenshot_filename
    except Exception as e:
        print(f"[ Error ] Regional overview screenshot failed: {e}")
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


def send_telegram_media_group(caption: str, photo_paths: list):
    """Sends a gallery of photos (Regional Overview Map + Flight Track Map) as a single Telegram Media Group"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media = []
    files = {}

    for i, path in enumerate(photo_paths):
        if path and os.path.exists(path):
            file_key = f"photo_{i}"
            files[file_key] = open(path, "rb")
            media_item = {
                "type": "photo",
                "media": f"attach://{file_key}"
            }
            if i == 0:
                media_item["caption"] = caption
                media_item["parse_mode"] = "HTML"
            media.append(media_item)

    if media:
        payload = {"chat_id": TELEGRAM_CHANNEL_ID, "media": json.dumps(media)}
        try:
            res = requests.post(url, data=payload, files=files)
            for f in files.values():
                f.close()
            for path in photo_paths:
                if path and os.path.exists(path):
                    os.remove(path)
            return res.json()
        except Exception as e:
            print(f"[ Error ] sendMediaGroup failed: {e}")
            for f in files.values():
                f.close()
    return None


def fetch_adsb_data():
    """Fetches aircraft from multiple open OSINT endpoints"""
    aircraft = []
    headers = {"User-Agent": "OSINT-Flight-Tracker/1.0"}

    # Airplanes.live Global Military Endpoint
    try:
        res = requests.get("https://api.airplanes.live/v2/mil", headers=headers, timeout=10)
        if res.status_code == 200:
            aircraft.extend(res.json().get("ac", []))
    except Exception:
        pass

    # ADSB.one Regional Point Search
    try:
        res = requests.get(f"https://api.adsb.one/v2/point/{REGIONAL_CENTER_LAT}/{REGIONAL_CENTER_LON}/250", headers=headers, timeout=10)
        if res.status_code == 200:
            aircraft.extend(res.json().get("ac", []))
    except Exception:
        pass

    unique_ac = {}
    for ac in aircraft:
        hex_code = ac.get("hex")
        if hex_code and hex_code not in unique_ac:
            unique_ac[hex_code] = ac

    return list(unique_ac.values())


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
    print("   OSINT Regional Sky Radar (Middle East & Iran) ")
    print("==================================================")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[ Error ] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID missing!")
        return

    seen_flights = load_seen_flights()
    aircraft_list = fetch_adsb_data()
    
    valid_flights = [
        ac for ac in aircraft_list 
        if ac.get("lat") and ac.get("lon") and (LAT_MIN <= ac.get("lat") <= LAT_MAX) and (LON_MIN <= ac.get("lon") <= LON_MAX)
    ]
    
    total_scanned = len(valid_flights)
    print(f"[ Scan ] Evaluated {total_scanned} active regional aircraft.")

    military_matches = 0

    # 1. TARGET MILITARY / CARGO / REFUEL FLIGHT DETECTED
    for ac in valid_flights:
        icao = str(ac.get("hex", "")).strip()
        callsign = str(ac.get("flight", "N/A")).strip()
        typecode = str(ac.get("t", "Unknown Type")).strip()
        alt = ac.get("alt_baro", "N/A")
        speed = ac.get("gs", "N/A")
        lat = ac.get("lat")
        lon = ac.get("lon")

        if not icao:
            continue

        flight_key = f"{icao}_{callsign}"

        if is_target_military(ac):
            military_matches += 1
            sector = determine_airspace_sector(lat, lon)
            title_header = get_exact_aircraft_title(typecode, callsign)

            print(f"[ TARGET DETECTED ] {title_header} | Sector: {sector}")

            if flight_key not in seen_flights:
                seen_flights.add(flight_key)

                regional_overview = capture_regional_overview_map()
                flight_map = capture_map_screenshot(icao)
                photo_url = get_plane_photo(icao)
                photo_link = f"📸 <a href='{photo_url}'>مشاهده عکس هواپیما</a>\n" if photo_url else ""

                caption = (
                    f"<b>{title_header}</b>\n"
                    f"📍 <b>Sector:</b> <i>{sector}</i>\n\n"
                    f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
                    f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                    f"🛩️ <b>Type:</b> <code>{typecode}</code>\n"
                    f"📈 <b>Altitude:</b> <code>{alt} ft</code> | 💨 <b>Speed:</b> <code>{speed} kts</code>\n"
                    f"🗺️ <b>Coordinates:</b> <code>{lat}, {lon}</code>\n\n"
                    f"{photo_link}"
                    f"🔗 <a href='https://globe.airplanes.live/?icao={icao}'>ردیابی زنده رادار</a>\n"
                    f"✈️ @secretollah"
                )

                photo_list = [p for p in [regional_overview, flight_map] if p]
                res = send_telegram_media_group(caption, photo_list)
                print(f"[ Post Status ] Response: {res}")

    # 2. FALLBACK: REGIONAL RADAR OVERVIEW UPDATE (IF NO MILITARY DETECTED)
    if military_matches == 0:
        print("[ Info ] No military transponders detected. Capturing dual regional radar gallery...")
        
        regional_overview = capture_regional_overview_map()

        if valid_flights:
            selected_ac = random.choice(valid_flights)
            icao = str(selected_ac.get("hex", "")).strip()
            callsign = str(selected_ac.get("flight", "N/A")).strip()
            typecode = str(selected_ac.get("t", "Commercial Aviation")).strip()
            alt = selected_ac.get("alt_baro", "N/A")
            speed = selected_ac.get("gs", "N/A")
            lat = selected_ac.get("lat")
            lon = selected_ac.get("lon")
            sector = determine_airspace_sector(lat, lon)

            flight_map = capture_map_screenshot(icao)
            photo_url = get_plane_photo(icao)
            photo_link = f"📸 <a href='{photo_url}'>مشاهده عکس هواپیما</a>\n" if photo_url else ""

            caption = (
                f"🌐 <b>REGIONAL AIRSPACE RADAR SCAN</b>\n"
                f"📍 <b>Sector:</b> <i>{sector}</i>\n\n"
                f"ℹ️ <i>Routine Scan: 0 military transponders detected in this interval.</i>\n"
                f"📊 <b>Total Regional Aircraft Monitored:</b> <code>{total_scanned}</code>\n\n"
                f"✈️ <b>Featured Active Flight:</b> <code>{callsign}</code> ({typecode})\n"
                f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                f"📈 <b>Altitude:</b> <code>{alt} ft</code> | 💨 <b>Speed:</b> <code>{speed} kts</code>\n"
                f"🗺️ <b>Coordinates:</b> <code>{lat}, {lon}</code>\n\n"
                f"{photo_link}"
                f"🔗 <a href='https://globe.airplanes.live/?lat={REGIONAL_CENTER_LAT}&lon={REGIONAL_CENTER_LON}&zoom=5'>ردیابی زنده رادار</a>\n"
                f"✈️ @secretollah"
            )
            photo_list = [p for p in [regional_overview, flight_map] if p]
        else:
            sector = determine_airspace_sector(REGIONAL_CENTER_LAT, REGIONAL_CENTER_LON)
            caption = (
                f"🌐 <b>REGIONAL AIRSPACE OVERVIEW SCAN</b>\n"
                f"📍 <b>Sector:</b> <i>{sector}</i>\n\n"
                f"ℹ️ <i>Quiet regional sky: 0 military transponders detected in this scan interval.</i>\n\n"
                f"🔗 <a href='https://globe.airplanes.live/?lat={REGIONAL_CENTER_LAT}&lon={REGIONAL_CENTER_LON}&zoom=5'>ردیابی زنده رادار</a>\n"
                f"✈️ @secretollah"
            )
            photo_list = [p for p in [regional_overview] if p]

        res = send_telegram_media_group(caption, photo_list)
        print(f"[ Regional Update Posted ] Response: {res}")

    save_seen_flights(seen_flights)


if __name__ == "__main__":
    try:
        run_tracker()
    except Exception as e:
        print(f"[ Fatal Error ] {e}")
        traceback.print_exc()
