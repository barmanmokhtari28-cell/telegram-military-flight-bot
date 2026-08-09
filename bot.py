import os
import time
import requests
from dotenv import load_dotenv
from telegram import Bot
from telegram.constants import ParseMode

load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # e.g., "@my_osint_channel" or -100xxxxxxx

# Middle East / Strait of Hormuz Bounding Box Coordinates
# [min_lat, max_lat, min_lon, max_lon]
LAT_MIN, LAT_MAX = 12.0, 40.0
LON_MIN, LON_MAX = 32.0, 65.0

# USAF / US Army Military Cargo Aircraft Types
TARGET_AIRCRAFT_TYPES = ["C17", "C130", "C5", "K35R", "KC10", "V22", "C32", "C40"]

# Frequently used US Military Air Mobility Callsign Prefixes
US_MIL_CALLSIGNS = ["RCH", "DUKE", "PAT", "EVAC", "CNV", "TOPCAT"]

bot = Bot(token=TELEGRAM_BOT_TOKEN)
seen_flights = set()


def get_plane_photo(registration_or_hex):
    """Fetches a high-res photo of the specific plane from Planespotters.net API"""
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{registration_or_hex}"
        headers = {"User-Agent": "OSINT-Flight-Tracker-Bot/1.0"}
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("photos"):
            return res["photos"][0]["thumbnail_large"]["src"]
    except Exception as e:
        print(f"Error fetching photo: {e}")
    return None


def fetch_adsb_data():
    """
    Fetches unfiltered aircraft data from ADS-B Exchange (via RapidAPI or direct endpoint).
    Alternative: OpenSky Network API.
    """
    # RapidAPI ADS-B Exchange endpoint example
    url = f"https://adsbexchange-com1.p.rapidapi.com/v2/lat/{ (LAT_MIN+LAT_MAX)/2 }/lon/{ (LON_MIN+LON_MAX)/2 }/dist/1000/"
    headers = {
        "X-RapidAPI-Key": os.getenv("RAPIDAPI_KEY"),
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.json().get("ac", [])
    except Exception as e:
        print(f"Error fetching ADS-B data: {e}")
    return []


def is_target_military_flight(aircraft):
    """Filters specifically for US military cargo / transiting aircraft in target area"""
    lat = aircraft.get("lat")
    lon = aircraft.get("lon")
    
    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return False

    callsign = aircraft.get("flight", "").strip().upper()
    typecode = aircraft.get("t", "").strip().upper()
    is_mil = aircraft.get("dbFlags", 0) == 1  # ADS-B flag indicating military aircraft

    # Check match by callsign, type, or military flag
    type_match = any(t in typecode for t in TARGET_AIRCRAFT_TYPES)
    callsign_match = any(callsign.startswith(prefix) for prefix in US_MIL_CALLSIGNS)

    return is_mil or type_match or callsign_match


def process_and_alert():
    aircraft_list = fetch_adsb_data()
    
    for ac in aircraft_list:
        icao = ac.get("hex")
        callsign = ac.get("flight", "N/A").strip()
        typecode = ac.get("t", "Unknown Type")
        alt = ac.get("alt_baro", "N/A")
        speed = ac.get("gs", "N/A")
        lat = ac.get("lat")
        lon = ac.get("lon")
        
        # Flight identifier key
        flight_key = f"{icao}_{callsign}"

        if is_target_military_flight(ac) and flight_key not in seen_flights:
            seen_flights.add(flight_key)
            
            # 1. Fetch plane image
            photo_url = get_plane_photo(icao) or "https://via.placeholder.com/800x600?text=No+Photo+Available"
            
            # 2. Build OSINT Telegram Caption
            caption = (
                f"🚨 <b>MILITARY FLIGHT ALERT (Middle East / Strait of Hormuz)</b> 🚨\n\n"
                f"✈️ <b>Callsign:</b> <code>{callsign}</code>\n"
                f"🆔 <b>ICAO Hex:</b> <code>{icao.upper()}</code>\n"
                f"🛩️ <b>Aircraft Type:</b> <code>{typecode}</code>\n"
                f"📈 <b>Altitude:</b> {alt} ft\n"
                f"💨 <b>Ground Speed:</b> {speed} knots\n"
                f"📍 <b>Location:</b> <code>{lat}, {lon}</code>\n\n"
                f"🔗 <a href='https://globe.adsbexchange.com/?icao={icao}'>View Live on ADS-B Exchange Map</a>\n"
                f"🔗 <a href='https://www.flightradar24.com/{callsign}'>Check on Flightradar24</a>"
            )

            # 3. Post to Telegram Channel
            try:
                bot.send_photo(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    photo=photo_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
                print(f"[+] Alert sent for {callsign} ({icao})")
            except Exception as e:
                print(f"Failed to send Telegram alert: {e}")


if __name__ == "__main__":
    print("Starting OSINT Military Flight Radar Telegram Bot...")
    while True:
        process_and_alert()
        time.sleep(120)  # Check every 2 minutes
