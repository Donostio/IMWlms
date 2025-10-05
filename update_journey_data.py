import json
import os
from datetime import datetime, timedelta
import requests
import pytz 
import random 
import urllib.parse # <-- New import for URL encoding

# --- Configuration ---
LIVE_DATA_FILE = 'live_data.json'
MAX_JOURNEYS_TO_SAVE = 5 
MIN_TRANSFER_MINUTES = 3 
TFL_BASE_URL = "https://api.tfl.gov.uk/v1"

# National Rail CRS Codes used by TfL for these routes
STATION_CODES = {
    'FROM': 'SRC', # Streatham Common
    'TO': 'IMW',  # Imperial Wharf
    'VIA': 'CLJ'  # Clapham Junction (intermediate station)
}
STATIONS = {
    'SRC': 'Streatham Common Rail Station',
    'CLJ': 'Clapham Junction Rail Station',
    'IMW': 'Imperial Wharf Rail Station'
}
PLATFORMS = {
    'CLJ': ['1', '2', '3', '4', '5', '6'],
}
LONDON_TIMEZONE = pytz.timezone('Europe/London')


# --- Utility Functions ---

def parse_time_to_dt(time_str, date_dt):
    """Parses HH:MM time string and combines it with a date datetime object."""
    try:
        time_obj = datetime.strptime(time_str, '%H:%M').time()
        # Combine the date part with the time part
        dt_full = date_dt.replace(hour=time_obj.hour, minute=time_obj.minute, second=0, microsecond=0)
        return dt_full
    except ValueError:
        return None

def format_time(dt_string):
    """Formats an ISO datetime string (from TFL) to HH:MM."""
    try:
        # TFL often returns ZULU time (ends in Z) or no timezone info
        dt = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        # Ensure conversion to London Time before formatting
        dt_london = dt.astimezone(LONDON_TIMEZONE)
        return dt_london.strftime('%H:%M')
    except Exception:
        # If standard parsing fails, assume it's already HH:MM or return as is
        return dt_string 

def calculate_duration_str(duration_mins):
    """Formats duration in minutes to string."""
    return f"{duration_mins} min"

def get_status_from_leg(leg):
    """Determines simplified status (On Time, Delayed, Cancelled) from a TfL leg."""
    if leg.get('isCancelled'):
        return 'Cancelled'
    
    scheduled_departure = leg.get('scheduledDepartureTime')
    departure = leg.get('departureTime')
    
    if scheduled_departure and departure and departure and scheduled_departure != departure:
        # Simple detection of a delay
        return 'Delayed'
    
    return 'On Time'

def get_platform_info(leg, origin_code):
    """Extracts platform and formats the key correctly."""
    platform = leg.get('departurePoint', {}).get('platformName') or leg.get('departurePoint', {}).get('platform') or 'TBC'
    return platform

def map_leg_to_json(leg, origin_code, dest_code):
    """Maps a single TfL leg object to the required internal format."""
    
    platform = get_platform_info(leg, origin_code)
    
    return {
        "origin": leg.get('departurePoint', {}).get('commonName') or STATIONS.get(origin_code, origin_code),
        "destination": leg.get('arrivalPoint', {}).get('commonName') or STATIONS.get(dest_code, dest_code),
        "departure": format_time(leg.get('departureTime')),
        "scheduled_departure": format_time(leg.get('scheduledDepartureTime') or leg.get('departureTime')),
        "arrival": format_time(leg.get('arrivalTime')),
        # Use dynamic platform keying for clarity on the UI side
        f"departurePlatform_{origin_code}": platform,
        "operator": leg.get('instruction', {}).get('summary', 'Unknown Operator'),
        "status": get_status_from_leg(leg)
    }

# --- TFL Data Harvester ---

class TflRailDataHarvester:
    """
    Fetches real-time journey data from the TfL Unified API using the two-separate-query 
    approach to ensure all short-transfer options are captured and built manually.
    """
    def __init__(self):
        self.app_id = os.environ.get('TFL_APP_ID')
        self.app_key = os.environ.get('TFL_APP_KEY')
        self.journeys = []
        self.segment_id_counter = 1
        self.london_now = datetime.now(LONDON_TIMEZONE)
        
        if not self.app_id or not self.app_key:
            print("ERROR: TFL_APP_ID or TFL_APP_KEY environment variables are missing.")
            self._use_mock_fallback = True
            print("WARNING: Falling back to mock data generation.")
        else:
            self._use_mock_fallback = False

    def fetch_tfl_journeys(self, origin_crs, destination_crs, max_journeys=8, departure_time=None):
        """
        Fetches journey results between two CRS codes, optionally setting a 
        specific departure time for the second leg search. Uses full station names in the URL.
        """
        if self._use_mock_fallback:
            # Mock fallback logic remains here, unchanged for brevity
            from datetime import timedelta
            now = datetime.now(LONDON_TIMEZONE)
            # ... mock setup
            direct_journey = {
                'startDateTime': now.isoformat(), 
                'duration': 35, 
                'legs': [
                    {'duration': 35, 'departureTime': (now + timedelta(minutes=10)).isoformat(), 'scheduledDepartureTime': (now + timedelta(minutes=10)).isoformat(), 'arrivalTime': (now + timedelta(minutes=45)).isoformat(), 'arrivalPoint': {'crsCode': STATION_CODES['TO'], 'commonName': STATIONS['IMW']}, 'departurePoint': {'crsCode': STATION_CODES['FROM'], 'commonName': STATIONS['SRC']}, 'instruction': {'summary': 'Southern Service'}, 'isCancelled': False}
                ]
            }
            first_leg_journey = {
                'startDateTime': (now + timedelta(minutes=20)).isoformat(), 
                'duration': 12, 
                'legs': [
                    {'duration': 12, 'departureTime': (now + timedelta(minutes=20)).isoformat(), 'scheduledDepartureTime': (now + timedelta(minutes=20)).isoformat(), 'arrivalTime': (now + timedelta(minutes=32)).isoformat(), 'arrivalPoint': {'crsCode': STATION_CODES['VIA'], 'commonName': STATIONS['CLJ']}, 'departurePoint': {'crsCode': STATION_CODES['FROM'], 'commonName': STATIONS['SRC']}, 'instruction': {'summary': 'London Overground'}, 'isCancelled': False},
                ]
            }
            second_leg_journey = {
                'startDateTime': (now + timedelta(minutes=35)).isoformat(), 
                'duration': 5, 
                'legs': [
                    {'duration': 5, 'departureTime': (now + timedelta(minutes=35)).isoformat(), 'scheduledDepartureTime': (now + timedelta(minutes=35)).isoformat(), 'arrivalTime': (now + timedelta(minutes=40)).isoformat(), 'arrivalPoint': {'crsCode': STATION_CODES['TO'], 'commonName': STATIONS['IMW']}, 'departurePoint': {'crsCode': STATION_CODES['VIA'], 'commonName': STATIONS['CLJ']}, 'instruction': {'summary': 'London Overground'}, 'isCancelled': False},
                ]
            }

            return {'journeys': [direct_journey, first_leg_journey, second_leg_journey]}

        now_london = self.london_now
        
        # Base parameters
        params = {
            'app_id': self.app_id,
            'app_key': self.app_key,
            'date': now_london.strftime('%Y%m%d'),
            'timeIs': 'departing', 
            'journeyPreference': 'LeastInterchange',
            'maxJourneys': max_journeys 
        }

        # Override departure time if provided (used for CLJ -> IMW search)
        if departure_time:
            params['time'] = departure_time
        else:
            params['time'] = now_london.strftime('%H%M')

        # --- Use full station names, URL encoded, for the API path ---
        origin_name = STATIONS.get(origin_crs, origin_crs)
        destination_name = STATIONS.get(destination_crs, destination_crs)
        
        encoded_origin = urllib.parse.quote(origin_name)
        encoded_destination = urllib.parse.quote(destination_name)
        
        url = f"{TFL_BASE_URL}/Journey/JourneyResults/{encoded_origin}/to/{encoded_destination}"
        # -----------------------------------------------------------------
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status() 
            
            data = response.json()
            return data
            
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Failed to fetch data from TfL API ({origin_crs} to {destination_crs}): {e}")
            return None

    def run_two_leg_search(self):
        """
        Executes the two separate real-time API queries and stitches the results 
        to build the full journey list.
        """
        
        # 1. Fetch direct SRC -> IMW journeys
        direct_data = self.fetch_tfl_journeys(STATION_CODES['FROM'], STATION_CODES['TO'], max_journeys=3)
        if direct_data and direct_data.get('journeys'):
            for raw_journey in direct_data['journeys']:
                legs = raw_journey.get('legs', [])
                if len(legs) == 1 and legs[0].get('arrivalPoint', {}).get('crsCode') == STATION_CODES['TO']:
                    first_leg = map_leg_to_json(legs[0], STATION_CODES['FROM'], STATION_CODES['TO'])
                    
                    self.journeys.append({
                        "type": "Direct",
                        "first_leg": first_leg,
                        "connections": [],
                        "totalDuration": calculate_duration_str(raw_journey.get('duration')),
                        "arrivalTime": first_leg['arrival'],
                        "departureTime": first_leg['departure'],
                        "segment_id": self.segment_id_counter,
                        "live_updated_at": self.london_now.strftime('%H:%M:%S')
                    })
                    self.segment_id_counter += 1
                    if len(self.journeys) >= MAX_JOURNEYS_TO_SAVE:
                        return 

        # 2. Fetch all potential first legs: SRC -> CLJ
        first_leg_data = self.fetch_tfl_journeys(STATION_CODES['FROM'], STATION_CODES['VIA'], max_journeys=10)
        
        if not first_leg_data or not first_leg_data.get('journeys'):
            return 

        # 3. Fetch all potential second legs: CLJ -> IMW (Departure board style)
        second_leg_data = self.fetch_tfl_journeys(STATION_CODES['VIA'], STATION_CODES['TO'], max_journeys=15)
        
        if not second_leg_data or not second_leg_data.get('journeys'):
            return 
            
        # Extract and format second leg options for easy lookup/stitching
        second_legs = []
        for raw_journey in second_leg_data['journeys']:
            legs = raw_journey.get('legs', [])
            if len(legs) >= 1 and legs[0].get('departurePoint', {}).get('crsCode') == STATION_CODES['VIA'] and legs[0].get('arrivalPoint', {}).get('crsCode') == STATION_CODES['TO']:
                second_legs.append(map_leg_to_json(legs[0], STATION_CODES['VIA'], STATION_CODES['TO']))


        # 4. Stitching Logic: Iterate over first legs and find valid second legs
        processed_first_legs = set()
        
        for raw_first_leg_journey in first_leg_data['journeys']:
            
            # Check for max journey limit
            if len(self.journeys) >= MAX_JOURNEYS_TO_SAVE:
                break
                
            legs = raw_first_leg_journey.get('legs', [])
            if not legs: continue
            
            first_raw_leg = legs[0]
            first_leg = map_leg_to_json(first_raw_leg, STATION_CODES['FROM'], STATION_CODES['VIA'])
            
            dep_time_key = first_leg['departure']
            if dep_time_key in processed_first_legs:
                continue 
            processed_first_legs.add(dep_time_key)

            # Get the arrival time at CLJ as a datetime object
            clj_arrival_time_str = first_leg['arrival']
            clj_arrival_dt = parse_time_to_dt(clj_arrival_time_str, self.london_now)
            if not clj_arrival_dt: continue

            valid_connections = []
            
            # Find the next 3 valid connections from CLJ
            for second_leg in second_legs:
                
                # Get the departure time from CLJ as a datetime object
                clj_departure_time_str = second_leg['departure']
                clj_departure_dt = parse_time_to_dt(clj_departure_time_str, self.london_now)

                if not clj_departure_dt: continue

                # Adjust for midnight crossing 
                if clj_departure_dt < clj_arrival_dt:
                    clj_departure_dt += timedelta(days=1)
                
                transfer_duration = (clj_departure_dt - clj_arrival_dt).total_seconds() / 60
                
                # Check minimum transfer time
                if transfer_duration >= MIN_TRANSFER_MINUTES:
                    # Found a valid connection
                    valid_connections.append({
                        "transferTime": f"{int(transfer_duration)} min",
                        "second_leg": second_leg
                    })
                
                # Stop looking for connections once we have the required number
                if len(valid_connections) >= 3:
                    break
            
            # Only add the combined journey if at least one valid connection was found
            if valid_connections:
                
                # Calculate total duration based on the earliest valid connection
                earliest_connection = valid_connections[0]
                final_arrival_time_str = earliest_connection['second_leg']['arrival']
                
                dep_dt_full = parse_time_to_dt(first_leg['departure'], self.london_now)
                arr_dt_full = parse_time_to_dt(final_arrival_time_str, self.london_now)
                
                # Adjust total arrival for midnight crossing
                if arr_dt_full < dep_dt_full:
                    arr_dt_full += timedelta(days=1)
                
                total_duration = int((arr_dt_full - dep_dt_full).total_seconds() / 60)
                total_duration_str = calculate_duration_str(total_duration)
                
                journey = {
                    "type": "One Change",
                    "first_leg": first_leg,
                    "connections": valid_connections,
                    "totalDuration": total_duration_str,
                    "arrivalTime": final_arrival_time_str,
                    "departureTime": first_leg['departure'],
                    "segment_id": self.segment_id_counter,
                    "live_updated_at": self.london_now.strftime('%H:%M:%S')
                }
                self.journeys.append(journey)
                self.segment_id_counter += 1


def save_rail_data():
    """Initializes the TFL data harvester and saves it to JSON."""
    harvester = TflRailDataHarvester()
    
    # Run the new two-step search logic using only real API calls
    harvester.run_two_leg_search()
    
    data_to_save = harvester.journeys[:MAX_JOURNEYS_TO_SAVE]

    try:
        with open(LIVE_DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
        print(f"✓ Successfully generated and saved {len(data_to_save)} journey segments to {LIVE_DATA_FILE}")
    except Exception as e:
        print(f"Error saving data to JSON: {e}")

if __name__ == '__main__':
    try:
        import pytz 
        save_rail_data()
    except (ImportError, NameError) as e:
        print(f"FATAL ERROR: Failed to run live data script. Missing library: {e}. Please ensure 'requests' and 'pytz' are installed by running 'pip install -r requirements.txt'")
