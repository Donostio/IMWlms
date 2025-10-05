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
INTERCHANGE_STATION = "Clapham Junction Rail Station"

# TFL API endpoint
TFL_BASE_URL = "https://api.tfl.gov.uk"
NUM_JOURNEYS = 8 # Target the next eight best stitched segments (First Legs)
MIN_TRANSFER_TIME_MINUTES = 3 # Minimum acceptable transfer time
MAX_RETRIES = 3 # Max retries for API calls

# NOTE: Live platform lookups have been removed as the TFL StopPoint API frequently
# returns 404 for these National Rail stations. Platform data will default to "TBC".
# NAPTAN_IDS dictionary and related platform fetching functions have been deleted.

# --- Utility Functions ---

def retry_fetch(url, params, max_retries=MAX_RETRIES):
    """Fetches data from a URL with exponential backoff for resilience."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # Removed the 404 StopPoint handling here as the platform fetch functions were removed.
            print(f"ERROR fetching data ({e}): Attempt {attempt + 1}/{max_retries}. Retrying in {2**attempt}s...")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                raise
        except requests.exceptions.RequestException as e:
            print(f"ERROR connecting to API ({e}): Attempt {attempt + 1}/{max_retries}. Retrying in {2**attempt}s...")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                raise

def get_segment_journeys(origin, destination, departure_time=None):
    """
    Fetch a list of planned journeys for a single segment using the TFL Journey Planner.
    This is used to get all viable train legs for stitching.
    The optional departure_time forces the TFL API to search from a specific point.
    """
    url = f"{TFL_BASE_URL}/Journey/JourneyResults/{origin}/to/{destination}"
    
    params = {
        "mode": "overground,national-rail",
        "timeIs": "Departing",
        "journeyPreference": "LeastTime",
        "alternativeRoute": "true"
    }
    
    # If a specific departure time is provided, use it in the API call
    if departure_time:
        # TFL API expects time in HHMM format and date in YYYYMMDD
        params["time"] = departure_time.strftime('%H%M')
        params["date"] = departure_time.strftime('%Y%m%d')
        print(f"DEBUG: Forcing API search for segment from {origin} to start at {departure_time.strftime('%H:%M')}.")
    
    if TFL_APP_ID and TFL_APP_KEY:
        params["app_id"] = TFL_APP_ID
        params["app_key"] = TFL_APP_KEY
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching segment journeys from {origin} to {destination}...")
    try:
        json_data = retry_fetch(url, params)
        return json_data.get('journeys', []) if json_data else []
    except Exception as e:
        print(f"ERROR: Failed to get segment journeys for {origin} to {destination}: {e}")
        return []

def extract_valid_train_legs(journeys, expected_destination):
    """
    Extracts the primary train leg from each journey result that matches the expected
    destination and returns a list of cleaned-up leg objects.
    It filters for unique train services based on time and line ID.
    """
    valid_legs = []
    
    for journey in journeys:
        # A journey result can contain multiple legs (e.g., walk + train), we only care about the first train leg.
        for leg in journey.get('legs', []):
            if leg.get('mode', {}).get('id') in ['overground', 'national-rail']:
                # Basic validation: ensure the arrival point is the expected destination
                if leg.get('arrivalPoint', {}).get('commonName') == expected_destination:
                    valid_legs.append(leg)
                break # Move to the next journey once the first train leg is found
                
    # Use a set comprehension to filter out duplicate legs (multiple journeys might return the same train)
    unique_legs = {
        (leg['departureTime'], leg['arrivalTime'], leg.get('line', {}).get('id')): leg 
        for leg in valid_legs
    }.values()
    
    return list(unique_legs)

def group_connections_by_first_leg(first_legs, second_legs, num_segments):
    """Groups valid second legs (connections) under their corresponding first leg."""
    
    grouped_segments = {}
    
    # Sort first legs by departure time for chronological display
    sorted_first_legs = sorted(first_legs, key=lambda l: datetime.fromisoformat(l['departureTime']))

    # --- DEBUGGING: Display available legs for clarity ---
    l1_departures = [datetime.fromisoformat(l['departureTime']).strftime('%H:%M') for l in sorted_first_legs]
    l2_departures = [datetime.fromisoformat(l['departureTime']).strftime('%H:%M') for l in second_legs]
    print(f"DEBUG: L1 (Streatham Common → Clapham Junction) Departures: {', '.join(l1_departures)}")
    print(f"DEBUG: L2 (Clapham Junction → Imperial Wharf) Departures: {', '.join(l2_departures)}")
    # --- END DEBUGGING ---

    for leg1 in sorted_first_legs:
        # Use a unique key for the first leg
        leg1_key = (leg1['departureTime'], leg1['arrivalTime'])
        
        # --- Prepare First Leg Data Structure ---
        if leg1_key not in grouped_segments:
            
            # PLATFORM EXTRACTION LOGIC:
            # Check for the 'platform' field directly on the leg object.
            # Use 'TBC' if the platform is not present in the scheduled data.
            first_platform = leg1.get('platform', 'TBC')

            dep_time_l1 = datetime.fromisoformat(leg1['departureTime'])
            arr_time_l1 = datetime.fromisoformat(leg1['arrivalTime'])
            
            # Extract scheduled time
            scheduled_dep = leg1.get('scheduledDepartureTime')
            # Format the scheduled time, default to the expected time if not present
            scheduled_dep_str = datetime.fromisoformat(scheduled_dep).strftime('%H:%M') if scheduled_dep else dep_time_l1.strftime('%H:%M')

            first_leg_data = {
                "origin": leg1['departurePoint']['commonName'],
                "destination": leg1['arrivalPoint']['commonName'],
                "departure": dep_time_l1.strftime('%H:%M'),
                "scheduled_departure": scheduled_dep_str, # NEW FIELD for displaying delay
                "arrival": arr_time_l1.strftime('%H:%M'),
                # Use the extracted platform here
                f"departurePlatform_{leg1['departurePoint']['commonName'].split(' ')[0]}": first_platform,
                "operator": leg1.get('operator', {}).get('id', 'N/A'),
                "status": leg1.get('status', 'On Time'),
                "rawArrivalTime": leg1['arrivalTime']
            }

            grouped_segments[leg1_key] = {
                "first_leg": first_leg_data,
                "connections": []
            }
        
        # --- Find and Process Valid Connections (Second Legs) ---
        for leg2 in second_legs:
            arr_time_l1 = datetime.fromisoformat(leg1['arrivalTime'])
            dep_time_l2 = datetime.fromisoformat(leg2['departureTime'])
            
            time_difference = dep_time_l2 - arr_time_l1
            transfer_time_minutes = int(time_difference.total_seconds() / 60)
            
            # The previous detailed per-combination print is removed for cleaner logs.
            # The logic here correctly filters out negative/too short transfers.
            
            if transfer_time_minutes >= MIN_TRANSFER_TIME_MINUTES:
                
                # PLATFORM EXTRACTION LOGIC:
                # Check for the 'platform' field directly on the leg object.
                # Use 'TBC' if the platform is not present in the scheduled data.
                second_platform = leg2.get('platform', 'TBC')

                dep_time_l2 = datetime.fromisoformat(leg2['departureTime'])
                arr_time_l2 = datetime.fromisoformat(leg2['arrivalTime'])
                
                second_leg_data = {
                    "origin": leg2['departurePoint']['commonName'],
                    "destination": leg2['arrivalPoint']['commonName'],
                    "departure": dep_time_l2.strftime('%H:%M'),
                    "arrival": arr_time_l2.strftime('%H:%M'),
                    # Use the extracted platform here
                    f"departurePlatform_{leg2['departurePoint']['commonName'].split(' ')[0]}": second_platform,
                    "operator": leg2.get('operator', {}).get('id', 'N/A'),
                    "status": leg2.get('status', 'On Time'),
                    "rawDepartureTime": leg2['departureTime'] # Keep raw time for connection sorting
                }

                # Add the connection
                grouped_segments[leg1_key]['connections'].append({
                    "transferTime": f"{transfer_time_minutes} min",
                    "second_leg": second_leg_data
                })

    # 4. Final Processing and Formatting
    final_output = []
    
    # Filter segments to only include those with at least one connection
    segments_with_connections = [s for s in grouped_segments.values() if s['connections']]
    
    # Sort the list of segments by the departure time of the first leg
    sorted_segments = sorted(segments_with_connections, key=lambda x: datetime.strptime(x['first_leg']['departure'], '%H:%M'))
    
    current_time = datetime.now().strftime('%H:%M:%S')

    for idx, segment in enumerate(sorted_segments):
        # Sort connections for each first leg by the departure time of the second leg
        segment['connections'].sort(key=lambda x: datetime.strptime(x['second_leg']['departure'], '%H:%M'))
        
        # Add metadata and clean up internal fields
        segment['segment_id'] = idx + 1
        segment['live_updated_at'] = current_time
        
        # Remove raw times from final output
        segment['first_leg'].pop('rawArrivalTime')
        for conn in segment['connections']:
            conn['second_leg'].pop('rawDepartureTime')
            
        final_output.append(segment)
        
        # Log the result for the console output
        conn_times = [c['second_leg']['departure'] for c in segment['connections']]
        print(f"✓ Segment {idx + 1} ({segment['first_leg']['departure']} → {segment['first_leg']['arrival']}): Found {len(conn_times)} connections ({', '.join(conn_times)})")

    # Limit to NUM_JOURNEYS segments
    return final_output[:num_journeys]


def stitch_and_process_journeys(num_segments):
    """
    Fetches all train legs for the two segments and manually groups them together
    to show all possible connections.
    """
    
    # 1. Fetch all unique train legs from Streatham Common to Clapham Junction (searches from now)
    journeys_l1 = get_segment_journeys(ORIGIN, INTERCHANGE_STATION)
    first_legs = extract_valid_train_legs(journeys_l1, INTERCHANGE_STATION)
    print(f"DEBUG: Found {len(first_legs)} unique legs for the first segment.")
    
    if not first_legs:
        print("ERROR: Could not retrieve any first train legs.")
        return []
    
    # 2. Fetch all unique train legs from Clapham Junction to Imperial Wharf
    # Look 90 minutes into the future to ensure we capture a good range of connecting trains.
    future_time = datetime.now() + timedelta(minutes=90)
    journeys_l2 = get_segment_journeys(INTERCHANGE_STATION, DESTINATION, departure_time=future_time)
    second_legs = extract_valid_train_legs(journeys_l2, DESTINATION)
    print(f"DEBUG: Found {len(second_legs)} unique legs for the second segment.")
    
    if not second_legs:
        print("ERROR: Could not retrieve sufficient train legs for stitching.")
        return []

    # 3. Group and process the connections
    processed_segments = group_connections_by_first_leg(first_legs, second_legs, num_segments)

    if not processed_segments:
        print(f"No valid segments found with connections meeting the minimum {MIN_TRANSFER_TIME_MINUTES}-minute transfer.")
        return []

    return processed_segments

# The main function is now simplified to use the new stitching logic
def main():
    data = stitch_and_process_journeys(NUM_JOURNEYS)
    
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"\n✓ Successfully saved {len(data)} journey segments to {OUTPUT_FILE}")
    else:
        print(f"\n⚠ Failed to retrieve or process any valid journey data. {OUTPUT_FILE} remains unchanged.")


if __name__ == "__main__":
    main()
