import os
import json
import requests
import time
from datetime import datetime, timedelta

# --- Configuration ---
TFL_APP_ID = os.getenv("TFL_APP_ID", "")
TFL_APP_KEY = os.getenv("TFL_APP_KEY", "")
OUTPUT_FILE = "live_data.json"

# Journey parameters
ORIGIN = "Streatham Common Rail Station"
DESTINATION = "Imperial Wharf Rail Station"

# TFL API endpoint
TFL_BASE_URL = "https://api.tfl.gov.uk"
NUM_JOURNEYS = 4 # Target the next four journeys
MIN_TRANSFER_TIME_MINUTES = 2 # Minimum acceptable transfer time
MAX_RETRIES = 3 # Max retries for API calls

# Defined Naptan IDs for stops mentioned in the route (Used for live platform lookups)
# These IDs are often problematic with the TFL /Arrivals endpoint for National Rail services.
NAPTAN_IDS = {
    "Streatham Common": "910GSTRHMCM",
    "Clapham Junction Rail Station": "910GCLPHMJN",
    "Imperial Wharf": "910GIMPERWH", 
}

# --- Utility Functions ---

def retry_fetch(url, params, max_retries=MAX_RETRIES):
    """Fetches data from a URL with exponential backoff for resilience."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # Handle specific 404 error gracefully for the StopPoint/Arrivals endpoint
            if response.status_code == 404 and "StopPoint" in url:
                # This 404 is common for National Rail stops on this specific TFL API.
                print(f"ERROR fetching data from TFL StopPoint API for {url.split('/')[4]}: 404 Client Error: Not Found.")
                return None # Return None on 404 for Arrivals lookup, allowing script to proceed
            
            print(f"ERROR fetching data ({e}): Attempt {attempt + 1}/{max_retries}. Retrying in {2**attempt}s...")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                raise # Re-raise error on final attempt
        except requests.exceptions.RequestException as e:
            print(f"ERROR connecting to API ({e}): Attempt {attempt + 1}/{max_retries}. Retrying in {2**attempt}s...")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                raise

def get_journey_plan(origin, destination):
    """Fetch journey plans from TFL Journey Planner API."""
    url = f"{TFL_BASE_URL}/Journey/JourneyResults/{origin}/to/{destination}"
    
    params = {
        "mode": "overground,national-rail",
        "timeIs": "Departing",
        "journeyPreference": "LeastTime",
        "alternativeRoute": "true"
    }
    
    if TFL_APP_ID and TFL_APP_KEY:
        params["app_id"] = TFL_APP_ID
        params["app_key"] = TFL_APP_KEY
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching journeys from {origin} to {destination}...")
    try:
        json_data = retry_fetch(url, params)
        
        # --- VERBOSE LOGGING START ---
        if json_data:
            print("\n--- FULL TFL API RESPONSE (START) ---")
            print(json.dumps(json_data, indent=4))
            print("--- FULL TFL API RESPONSE (END) ---\n")
        # --- VERBOSE LOGGING END ---

        return json_data
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to get journey plan after all retries: {e}")
        return None

def get_live_arrivals(naptan_id):
    """Fetches live arrival board for a given Naptan ID."""
    url = f"{TFL_BASE_URL}/StopPoint/{naptan_id}/Arrivals"
    
    params = {}
    if TFL_APP_ID and TFL_APP_KEY:
        params["app_id"] = TFL_APP_ID
        params["app_key"] = TFL_APP_KEY
    
    # We explicitly allow the 404 to be handled gracefully in retry_fetch
    return retry_fetch(url, params, max_retries=1)

def find_legs_to_monitor(journey):
    """Identifies the first and second train legs for real-time monitoring."""
    train_legs = []
    
    # Find all train legs
    for leg in journey.get('legs', []):
        if leg.get('mode', {}).get('id') in ['overground', 'national-rail']:
            train_legs.append(leg)

    # Return the first leg (departure station) and the leg departing after the transfer
    if not train_legs:
        return None, None
    
    # First leg (The trip from the origin to the interchange)
    first_leg = train_legs[0]
    
    # Second leg (The trip from the interchange to the destination)
    second_leg = train_legs[1] if len(train_legs) > 1 else None

    return first_leg, second_leg

def get_platform_from_tfl_arrivals(live_arrivals, train_id, scheduled_departure_time):
    """Searches live arrivals for a specific train and returns its platform or 'TBC'."""
    if not live_arrivals:
        return "TBC"

    # Convert scheduled time to datetime object for comparison
    scheduled_dt = datetime.strptime(scheduled_departure_time, '%Y-%m-%dT%H:%M:%S')

    # Filter for the specific train based on its ID (or destination if ID is missing/unreliable)
    # TFL uses a variety of identifiers; here we look for a close match in time.
    
    # Look for arrivals that are scheduled close to the departure time
    for arrival in live_arrivals:
        # TFL uses 'expectedArrival' for the arrival at the stop
        expected_arrival_dt = datetime.fromisoformat(arrival.get('expectedArrival'))
        
        # We need to find the train *departing* at or near the scheduled time.
        # Check if the arrival time is within a small window of the scheduled time.
        time_diff = abs(expected_arrival_dt - scheduled_dt)
        
        # If the arrival is within 10 minutes of the scheduled departure time (as a rough heuristic)
        if time_diff < timedelta(minutes=10) and arrival.get('platformName'):
            # This is not perfect, but it's the best we can do without a reliable TFL train ID
            platform = arrival['platformName'].replace('Platform ', '')
            return platform
            
    return "TBC"

def get_journey_status(first_leg, second_leg):
    """Determine the overall status based on leg delays."""
    status = "On Time"
    
    # Ensure legs are not None before accessing properties
    first_delay = first_leg.get('departureDelay', 0) if first_leg else 0
    second_delay = second_leg.get('departureDelay', 0) if second_leg else 0

    if first_delay > 0 or second_delay > 0:
        status = "Delayed"
    
    # You can add more complex logic here (e.g., if any leg status is 'Cancelled')
    
    return status

def process_journey(journey, log_id):
    """
    Extracts key information from a raw TFL journey object, validates the transfer,
    and enriches with real-time data.
    
    Args:
        journey (dict): The raw TFL journey object.
        log_id (int): The index of the journey (1-based) from the TFL response, used for logging.
    """
    
    # Find the legs we care about (first train and second train)
    first_leg_raw, second_leg_raw = find_legs_to_monitor(journey)

    # CRITICAL CHECK 1: If no national-rail or overground legs were found, skip this journey.
    if not first_leg_raw:
        print(f"   Journey {log_id} skipped: No primary train leg found for processing.")
        return None

    # Check if the journey requires a change (One Change)
    num_changes = journey.get('journeyAts', {}).get('numChanges', 0)
    
    # We only care about Direct (0 changes) or One Change (1 change)
    if num_changes > 1:
        return None
    
    # If the journey is a transfer, we MUST have a second leg
    if num_changes == 1 and not second_leg_raw:
        # This can happen if the second leg is non-train (e.g., walk, bus) which we filtered out
        print(f"   Journey {log_id} skipped: One Change journey does not have a subsequent train leg.")
        return None

    # --- Robust Time Extraction and Transfer Validation ---
    
    try:
        # First Leg Times
        first_departure_time_str = first_leg_raw['departurePoint']['departureTime']
        first_arrival_time_str = first_leg_raw['arrivalPoint']['arrivalTime']
        
        first_departure_time_formatted = datetime.fromisoformat(first_departure_time_str).strftime('%H:%M')
        first_arrival_time_formatted = datetime.fromisoformat(first_arrival_time_str).strftime('%H:%M')
        
        transfer_time_minutes = 0
        if num_changes == 1:
            # Second Leg Times (required for transfer calculation)
            second_departure_time_str = second_leg_raw['departurePoint']['departureTime']
            second_arrival_time_str = second_leg_raw['arrivalPoint']['arrivalTime']

            second_departure_time_formatted = datetime.fromisoformat(second_departure_time_str).strftime('%H:%M')
            second_arrival_time_formatted = datetime.fromisoformat(second_arrival_time_str).strftime('%H:%M')
            
            # Calculate transfer time
            first_arrival_time = datetime.fromisoformat(first_arrival_time_str)
            second_departure_time = datetime.fromisoformat(second_departure_time_str)
            
            time_difference = second_departure_time - first_arrival_time
            transfer_time_minutes = int(time_difference.total_seconds() / 60)
            
            if transfer_time_minutes < MIN_TRANSFER_TIME_MINUTES:
                print(f"   Journey {log_id} skipped: Transfer time of {transfer_time_minutes} min is less than the minimum required {MIN_TRANSFER_TIME_MINUTES} min.")
                return None
                
    except KeyError as e:
        # Catches the 'departureTime' or 'arrivalPoint' missing error
         print(f"   Journey {log_id} skipped: Critical journey data missing ({e}). Skipping malformed journey.")
         return None
    except ValueError:
        # Catches malformed time strings (e.g., if it's not ISO format)
         print(f"   Journey {log_id} skipped: Time data is in an invalid format. Skipping malformed journey.")
         return None

    # --- Live Data Enrichment ---
    
    # 1. First Leg (Origin to Interchange)
    first_leg_naptan = NAPTAN_IDS.get(first_leg_raw['departurePoint']['commonName'])
    first_leg_arrivals = get_live_arrivals(first_leg_naptan) if first_leg_naptan else None
    
    first_platform = "TBC"
    if first_leg_arrivals:
        first_platform = get_platform_from_tfl_arrivals(
            first_leg_arrivals, 
            first_leg_raw.get('line', {}).get('id'), # Use line ID as a train identifier
            first_departure_time_str # Use the raw time string
        )
    
    # 2. Second Leg (Interchange to Destination, only for changes)
    second_platform = "TBC"
    if second_leg_raw:
        second_leg_naptan = NAPTAN_IDS.get(second_leg_raw['departurePoint']['commonName'])
        second_leg_arrivals = get_live_arrivals(second_leg_naptan) if second_leg_naptan else None
        
        if second_leg_arrivals:
            second_platform = get_platform_from_tfl_arrivals(
                second_leg_arrivals, 
                second_leg_raw.get('line', {}).get('id'), # Use line ID as a train identifier
                second_departure_time_str # Use the raw time string
            )

    # --- Construct Final Data ---
    
    current_time = datetime.now().strftime('%H:%M:%S')
    
    processed_legs = []
    
    # First Leg: Origin to Interchange/Destination
    processed_legs.append({
        "origin": first_leg_raw['departurePoint']['commonName'],
        "destination": first_leg_raw['arrivalPoint']['commonName'],
        "departure": first_departure_time_formatted,
        "arrival": first_arrival_time_formatted,
        # Dynamic key for platform, e.g., "departurePlatform_Streatham"
        f"departurePlatform_{first_leg_raw['departurePoint']['commonName'].split(' ')[0]}": first_platform,
        "operator": first_leg_raw.get('operator', {}).get('id', 'N/A'),
        "status": first_leg_raw.get('status', 'On Time'),
    })

    if num_changes == 1:
        # Transfer Leg
        processed_legs.append({
            "type": "transfer",
            "location": first_leg_raw['arrivalPoint']['commonName'],
            "transferTime": f"{transfer_time_minutes} min"
        })
        
        # Second Leg: Interchange to Destination
        processed_legs.append({
            "origin": second_leg_raw['departurePoint']['commonName'],
            "destination": second_leg_raw['arrivalPoint']['commonName'],
            "departure": second_departure_time_formatted,
            "arrival": second_arrival_time_formatted,
            # Dynamic key for platform, e.g., "departurePlatform_Clapham"
            f"departurePlatform_{second_leg_raw['departurePoint']['commonName'].split(' ')[0]}": second_platform,
            "operator": second_leg_raw.get('operator', {}).get('id', 'N/A'),
            "status": second_leg_raw.get('status', 'On Time'),
        })
    
    # Total duration is returned as minutes from TFL
    total_duration_minutes = journey.get('duration', 'N/A')
    
    # Use the status from the overall journey status checker
    overall_status = get_journey_status(first_leg_raw, second_leg_raw)

    # Note: 'id' is intentionally omitted here and will be assigned sequentially
    # in fetch_and_process_tfl_data based on the order of successful processing.
    return {
        "type": "One Change" if num_changes == 1 else "Direct",
        "departureTime": processed_legs[0]['departure'],
        "arrivalTime": processed_legs[-1]['arrival'],
        "totalDuration": f"{total_duration_minutes} min",
        "status": overall_status,
        "live_updated_at": current_time,
        "legs": processed_legs
    }


def fetch_and_process_tfl_data(num_journeys):
    """Fetches TFL data, processes journeys, and filters for a fixed number of valid train journeys."""
    
    journey_data = get_journey_plan(ORIGIN, DESTINATION)
    
    if not journey_data or 'journeys' not in journey_data:
        print("ERROR: No journey data received from TFL API")
        return []
    
    journeys = journey_data.get('journeys', [])
    print(f"Found {len(journeys)} total journeys from TFL in the response.")
    
    processed = []
    for idx, journey in enumerate(journeys, 1):
        try:
            # Pass the raw index 'idx' for logging purposes during skipping/error reporting
            processed_journey = process_journey(journey, idx)
            if processed_journey:
                # Assign the final sequential ID based on successful processing order
                processed_journey['id'] = len(processed) + 1
                processed.append(processed_journey)
                print(f"✓ Journey {processed_journey['id']} ({processed_journey['type']}): {processed_journey['departureTime']} → {processed_journey['arrivalTime']} | Status: {processed_journey['status']}")
                
                if len(processed) >= num_journeys:
                    break
        except Exception as e:
            # Catching generic errors inside the loop helps the script process subsequent journeys
            print(f"ERROR processing journey {idx}: {e}")
            continue
    
    if len(processed) == 0:
        # Reverting to the old message style for clarity, removing "stitched" since we process direct/one change
        print(f"\n⚠ No valid rail journeys found with a maximum of one change and a minimum {MIN_TRANSFER_TIME_MINUTES}-minute transfer.")
        
    print(f"\nSuccessfully processed {len(processed)} train journeys (Direct or One Change)")
    return processed


def main():
    data = fetch_and_process_tfl_data(NUM_JOURNEYS)
    
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"\n✓ Successfully saved {len(data)} journeys to {OUTPUT_FILE}")
    else:
        print(f"\n⚠ Failed to retrieve or process any valid journey data. {OUTPUT_FILE} remains unchanged.")


if __name__ == "__main__":
    main()


