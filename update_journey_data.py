
import os
import json
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# --- Configuration ---
# TFL API credentials (set as environment variables in GitHub Secrets)
TFL_APP_ID = os.getenv("TFL_APP_ID", "")
TFL_APP_KEY = os.getenv("TFL_APP_KEY", "")
OUTPUT_FILE = "live_data.json"

# Journey Parameters using NAPTAN IDs for StopPoint API
# NAPTAN IDs for National Rail/Overground stations
NAPTAN_SRC = "910GSTRHMCM" # Streatham Common
NAPTAN_CPJ = "910GCLPHMJN" # Clapham Junction
NAPTAN_IMW = "910GIMPWHF" # Imperial Wharf

# TFL API endpoint
TFL_BASE_URL = "https://api.tfl.gov.uk"
NUM_JOURNEYS = 8 # We will fetch more and then filter down to the best 8
MIN_TRANSFER_MINUTES = 2 # Minimum connection time allowed at CPJ

# --- Utility Functions ---

def get_arrivals_board(naptan_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch live arrival/departure predictions for a StopPoint (station).
    This endpoint often includes platform information and current status.
    """
    url = f"{TFL_BASE_URL}/StopPoint/{naptan_id}/Arrivals"
    
    params = {}
    if TFL_APP_ID and TFL_APP_KEY:
        params["app_id"] = TFL_APP_ID
        params["app_key"] = TFL_APP_KEY
    
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching live departures for Naptan ID: {naptan_id}...")
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        # TFL returns an array of individual arrivals/departures for services
        arrivals = response.json()
        
        # Sort by expected arrival/departure time
        arrivals.sort(key=lambda x: x.get('expectedArrival') or x.get('expectedDeparture') or x.get('scheduledArrival'))

        return arrivals
    
    except requests.exceptions.RequestException as e:
        print(f"ERROR fetching data from TFL StopPoint API for {naptan_id}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

def find_services_between(start_naptan: str, end_naptan: str) -> List[Dict[str, Any]]:
    """
    Filters the full arrivals board to find services going directly from
    start_naptan to end_naptan.
    """
    all_arrivals = get_arrivals_board(start_naptan)
    if not all_arrivals:
        return []

    # Use the TFL Common Name for the destination for easier human readability later
    # We must use the 'destinationNaptanId' from the prediction object to filter.
    
    # Filter arrivals based on the destination Naptan ID.
    # Note: TFL API returns 'Arrivals' for the current station, 
    # but the JSON object usually represents the train's journey.
    # We look for the service where the *next* or final destination is our target.
    services = []
    
    # The TFL API for arrivals at a stop point sometimes doesn't directly give the 'next stop'
    # but the service's *ultimate* destination. Since the user's route is specific, 
    # we filter by 'destinationNaptanId' and ensure the mode is appropriate.

    for service in all_arrivals:
        if (service.get('destinationNaptanId') == end_naptan and 
            service.get('modeName') in ['overground', 'national-rail']):
            services.append(service)

    print(f"Found {len(services)} relevant services from {start_naptan} to {end_naptan}.")
    return services

def format_time(iso_time_str: str) -> str:
    """Converts TFL ISO time string to 'HH:MM' format."""
    try:
        # TFL ISO times might contain timezone info (+01:00) which we need to strip 
        # for a simple, local time display.
        dt_obj = datetime.fromisoformat(iso_time_str.replace('Z', '+00:00'))
        return dt_obj.strftime('%H:%M')
    except:
        return "N/A"

def calculate_time_diff_minutes(time_a: str, time_b: str) -> float:
    """Calculates the difference in minutes between two ISO time strings (b - a)."""
    try:
        dt_a = datetime.fromisoformat(time_a.replace('Z', '+00:00'))
        dt_b = datetime.fromisoformat(time_b.replace('Z', '+00:00'))
        return (dt_b - dt_a).total_seconds() / 60
    except:
        return 0.0

# --- Main Logic ---

def stitch_journeys(leg1_services: List[Dict[str, Any]], leg2_services: List[Dict[str, Any]], min_transfer: int) -> List[Dict[str, Any]]:
    """
    Manually stitches Leg 1 (SRC->CPJ) and Leg 2 (CPJ->IMW) services 
    with a minimum transfer time.
    """
    stitched_journeys = []
    
    # Get the current time for the live_updated_at field
    current_time_str = datetime.now().strftime('%H:%M:%S')

    for leg1 in leg1_services:
        # Leg 1: SRC to CPJ
        src_departure_iso = leg1.get('expectedDeparture', leg1.get('scheduledDeparture'))
        cpj_arrival_iso = leg1.get('expectedArrival', leg1.get('scheduledArrival'))
        
        if not src_departure_iso or not cpj_arrival_iso:
            continue # Skip invalid legs

        # Convert times for comparison
        cpj_arrival_dt = datetime.fromisoformat(cpj_arrival_iso.replace('Z', '+00:00'))

        for leg2 in leg2_services:
            # Leg 2: CPJ to IMW
            cpj_departure_iso = leg2.get('expectedDeparture', leg2.get('scheduledDeparture'))
            imw_arrival_iso = leg2.get('expectedArrival', leg2.get('scheduledArrival'))

            if not cpj_departure_iso or not imw_arrival_iso:
                continue # Skip invalid legs

            # Convert times for comparison
            cpj_departure_dt = datetime.fromisoformat(cpj_departure_iso.replace('Z', '+00:00'))

            # Check the required connection time
            transfer_time_minutes = (cpj_departure_dt - cpj_arrival_dt).total_seconds() / 60

            if transfer_time_minutes >= min_transfer:
                # Valid connection found! Stitch the journey.
                
                # Total journey time
                total_duration_minutes = calculate_time_diff_minutes(src_departure_iso, imw_arrival_iso)
                
                # Processed Journey Structure
                journey = {
                    "type": "One Change (Stitched)",
                    "departureTime": format_time(src_departure_iso),
                    "arrivalTime": format_time(imw_arrival_iso),
                    "totalDuration": f"{int(total_duration_minutes)} min",
                    # Status is derived from the legs. For simplicity, we just check if both are 'On Time'
                    "status": "On Time" if leg1.get('timing', {}).get('status') == 'On Time' and leg2.get('timing', {}).get('status') == 'On Time' else "Delayed",
                    "live_updated_at": current_time_str,
                    "legs": [
                        {
                            "origin": "Streatham Common",
                            "destination": "Clapham Junction",
                            "departure": format_time(src_departure_iso),
                            "arrival": format_time(cpj_arrival_iso),
                            "platform": leg1.get('platformName', 'TBC'), # Live Platform!
                            "operator": leg1.get('platformName', 'Southern'), # TFL doesn't always populate this well on Arrivals API, use a default
                            "status": leg1.get('timing', {}).get('status', 'Scheduled'),
                        },
                        {
                            "type": "transfer",
                            "location": "Clapham Junction",
                            "transferTime": f"{int(transfer_time_minutes)} min"
                        },
                        {
                            "origin": "Clapham Junction",
                            "destination": "Imperial Wharf",
                            "departure": format_time(cpj_departure_iso),
                            "arrival": format_time(imw_arrival_iso),
                            "platform": leg2.get('platformName', 'TBC'), # Live Platform!
                            "operator": leg2.get('platformName', 'Overground'), # TFL doesn't always populate this well on Arrivals API, use a default
                            "status": leg2.get('timing', {}).get('status', 'Scheduled'),
                        }
                    ]
                }
                stitched_journeys.append(journey)

    # Sort the final results by departure time
    stitched_journeys.sort(key=lambda j: datetime.strptime(j['departureTime'], '%H:%M'))
    
    # Assign IDs and return only the top N
    for i, journey in enumerate(stitched_journeys):
        journey['id'] = i + 1

    return stitched_journeys[:NUM_JOURNEYS]

def main():
    # 1. Fetch live departure data for both legs
    leg1_services = find_services_between(NAPTAN_SRC, NAPTAN_CPJ)
    leg2_services = find_services_between(NAPTAN_CPJ, NAPTAN_IMW)
    
    # 2. Stitch the valid journeys together
    data = stitch_journeys(leg1_services, leg2_services, MIN_TRANSFER_MINUTES)
    
    # 3. Save the results
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"\n✓ Successfully saved {len(data)} stitched journeys to {OUTPUT_FILE}")
    else:
        print(f"\n⚠ No valid stitched journeys found with a minimum {MIN_TRANSFER_MINUTES}-minute transfer.")
        # Optionally, save an empty or error JSON to prevent the front-end from crashing
        with open(OUTPUT_FILE, 'w') as f:
            json.dump([], f, indent=4)
        
if __name__ == "__main__":
    main()
