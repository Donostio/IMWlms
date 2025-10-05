import json
import random
from datetime import datetime, timedelta
import pytz # Import pytz for timezone handling

# --- Configuration ---
LIVE_DATA_FILE = 'live_data.json'
MAX_JOURNEYS_TO_SAVE = 3
LONDON_TIMEZONE = pytz.timezone('Europe/London')
STATIONS = {
    'SRC': 'Streatham Common Rail Station',
    'CLJ': 'Clapham Junction Rail Station',import json
import os
from datetime import datetime, timedelta
import requests
import pytz 
import random # Required for mock connections

# --- Configuration ---
LIVE_DATA_FILE = 'live_data.json'
MAX_JOURNEYS_TO_SAVE = 3
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
    
    if scheduled_departure and departure and scheduled_departure != departure:
        # Simple detection of a delay
        return 'Delayed'
    
    return 'On Time'

def get_platform_info(leg, origin_code):
    """Extracts platform and formats the key correctly."""
    platform = leg.get('departurePoint', {}).get('platformName') or leg.get('departurePoint', {}).get('platform') or 'TBC'
    return platform

# --- TFL Data Harvester ---

class TflRailDataHarvester:
    """
    Fetches real-time journey data from the TfL Unified API using a two-step approach
    to allow for short, custom transfer times.
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

    def fetch_tfl_journeys(self, origin, destination):
        """Fetches journey results between two CRS codes."""
        if self._use_mock_fallback:
            # Mock fallback logic remains for safety
            # Simple mock data generation for fail-safe testing when keys are missing
            from datetime import timedelta
            now = datetime.now(LONDON_TIMEZONE) # Use London time for consistency
            
            # Create a mock direct journey
            direct_journey = {
                'startDateTime': now.isoformat(), 
                'duration': 35, 
                'legs': [
                    {'duration': 35, 'departureTime': (now + timedelta(minutes=10)).isoformat(), 'scheduledDepartureTime': (now + timedelta(minutes=10)).isoformat(), 'arrivalTime': (now + timedelta(minutes=45)).isoformat(), 'arrivalPoint': {'crsCode': STATION_CODES['TO'], 'commonName': STATIONS['IMW']}, 'departurePoint': {'crsCode': STATION_CODES['FROM'], 'commonName': STATIONS['SRC']}, 'instruction': {'summary': 'Southern Service'}, 'isCancelled': False}
                ]
            }
            
            # Create a mock one-change journey
            change_journey = {
                'startDateTime': now.isoformat(), 
                'duration': 40,
                'legs': [
                    {'duration': 12, 'departureTime': (now + timedelta(minutes=20)).isoformat(), 'scheduledDepartureTime': (now + timedelta(minutes=20)).isoformat(), 'arrivalTime': (now + timedelta(minutes=32)).isoformat(), 'arrivalPoint': {'crsCode': STATION_CODES['VIA'], 'commonName': STATIONS['CLJ']}, 'departurePoint': {'crsCode': STATION_CODES['FROM'], 'commonName': STATIONS['SRC']}, 'instruction': {'summary': 'London Overground'}, 'isCancelled': False},
                    # Note: We don't need leg 2 here as it will be generated by the mock connections
                    {'duration': 5, 'departureTime': (now + timedelta(minutes=37)).isoformat(), 'scheduledDepartureTime': (now + timedelta(minutes=37)).isoformat(), 'arrivalTime': (now + timedelta(minutes=42)).isoformat(), 'arrivalPoint': {'crsCode': STATION_CODES['TO'], 'commonName': STATIONS['IMW']}, 'departurePoint': {'crsCode': STATION_CODES['VIA'], 'commonName': STATIONS['CLJ']}, 'instruction': {'summary': 'Southern Service'}, 'isCancelled': False}
                ]
            }

            return {'journeys': [direct_journey, change_journey]}

        now_london = self.london_now

        params = {
            'app_id': self.app_id,
            'app_key': self.app_key,
            'date': now_london.strftime('%Y%m%d'),
            'time': now_london.strftime('%H%M'),
            'timeIs': 'departing', 
            'journeyPreference': 'LeastInterchange',
            'maxJourneys': 8 
        }
        
        url = f"{TFL_BASE_URL}/Journey/JourneyResults/{origin}/to/{destination}"
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status() 
            
            data = response.json()
            print(f"DEBUG: API returned {len(data.get('journeys', []))} raw journeys.")
            return data
            
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Failed to fetch data from TfL API ({origin} to {destination}): {e}")
            return None

    def fetch_clj_connections(self, arrival_dt):
        """
        Mock generator to create 3 connection options for the UI, simulating a 
        departure board lookup at CLJ which allows for short transfers.
        """
        CLJ_PLATFORMS = ['1', '2', '3', '4', '5'] 
        IMW_OPERATORS = ['London Overground', 'Southern', 'South Western Railway']
        mock_connections = []
        
        # Start looking for connections 3 minutes after arrival at CLJ
        # This is the crucial part that bypasses the TfL Journey Planner's minimum transfer time.
        clj_dep_base_dt = arrival_dt + timedelta(minutes=3) 
        
        for i in range(3):
            # Stagger departures every 7-10 minutes
            dep_dt = clj_dep_base_dt + timedelta(minutes=i * random.randint(7, 10))
            arr_dt = dep_dt + timedelta(minutes=random.randint(4, 6)) # 4-6 min run time CLJ -> IMW

            dep_time_str = dep_dt.strftime('%H:%M')
            arr_time_str = arr_dt.strftime('%H:%M')
            
            # Calculate transfer time
            transfer_time = (dep_dt - arrival_dt).total_seconds() / 60
            
            # Scenario: Ensure the first connection is sometimes cancelled to test UI logic
            status = 'Cancelled' if (i == 0 and random.random() < 0.3) else 'On Time'
            
            mock_connections.append({
                "transferTime": f"{int(transfer_time)} min",
                "second_leg": {
                    "origin": STATIONS['CLJ'],
                    "destination": STATIONS['IMW'],
                    "departure": dep_time_str,
                    "scheduled_departure": dep_time_str,
                    "arrival": arr_time_str,
                    "departurePlatform_CLJ": random.choice(CLJ_PLATFORMS),
                    "operator": random.choice(IMW_OPERATORS),
                    "status": status
                }
            })
        return mock_connections

    def map_leg_to_json(self, leg, origin_code, dest_code):
        """Maps a single TfL leg object to the required internal format."""
        
        # TFL uses minutes for duration
        duration_mins = leg.get('duration', 0)
        
        platform = get_platform_info(leg, origin_code)
        
        return {
            "origin": leg.get('departurePoint', {}).get('commonName') or STATIONS.get(origin_code, origin_code),
            "destination": leg.get('arrivalPoint', {}).get('commonName') or STATIONS.get(dest_code, dest_code),
            "departure": format_time(leg.get('departureTime')),
            "scheduled_departure": format_time(leg.get('scheduledDepartureTime') or leg.get('departureTime')),
            "arrival": format_time(leg.get('arrivalTime')),
            f"departurePlatform_{origin_code}": platform,
            "operator": leg.get('instruction', {}).get('summary', 'Unknown Operator'),
            "status": get_status_from_leg(leg)
        }


    def process_tfl_journeys(self, tfl_data):
        """Processes and filters raw TFL data using the two-step approach."""
        
        if not tfl_data or not tfl_data.get('journeys'):
            return
            
        processed_departures = set() 
        
        for raw_journey in tfl_data['journeys']:
            legs = raw_journey.get('legs', [])
            
            if len(self.journeys) >= MAX_JOURNEYS_TO_SAVE:
                break
            
            if not legs: continue
            
            leg1 = legs[0]
            dep_time = format_time(leg1.get('departureTime'))
            
            if dep_time in processed_departures:
                continue 
            
            processed_departures.add(dep_time)
            
            # --- Scenario 1: Direct Journey (1 Leg) ---
            if len(legs) == 1 and leg1.get('arrivalPoint', {}).get('crsCode') == STATION_CODES['TO']:
                
                first_leg = self.map_leg_to_json(leg1, STATION_CODES['FROM'], STATION_CODES['TO'])
                
                journey = {
                    "type": "Direct",
                    "first_leg": first_leg,
                    "connections": [],
                    "totalDuration": calculate_duration_str(raw_journey.get('duration')),
                    "arrivalTime": first_leg['arrival'],
                    "departureTime": first_leg['departure'],
                    "segment_id": self.segment_id_counter,
                    "live_updated_at": self.london_now.strftime('%H:%M:%S')
                }
                self.journeys.append(journey)
                self.segment_id_counter += 1
                print(f"✓ Created DIRECT Journey (Segment ID {journey['segment_id']}): Depart {journey['departureTime']} / Arrive {journey['arrivalTime']}")
                    
            # --- Scenario 2: One Change Journey (Leg 1 is SRC -> CLJ) ---
            elif leg1.get('arrivalPoint', {}).get('crsCode') == STATION_CODES['VIA']:
                
                first_leg = self.map_leg_to_json(leg1, STATION_CODES['FROM'], STATION_CODES['VIA'])
                
                # Convert the arrival time string at CLJ back to a datetime object
                clj_arr_str = first_leg['arrival']
                clj_arr_time = datetime.strptime(clj_arr_str, '%H:%M').time()
                clj_arr_dt = self.london_now.replace(hour=clj_arr_time.hour, minute=clj_arr_time.minute, second=0, microsecond=0)
                
                # --- Step 2: Fetch and Stitch Connection Options (CLJ -> IMW) ---
                # NOTE: This uses the mock generator to allow for short transfers
                connections = self.fetch_clj_connections(clj_arr_dt)

                # --- Calculate Final Metrics (based on first valid connection) ---
                valid_connection = next((conn for conn in connections if conn['second_leg']['status'] != 'Cancelled'), None)
                
                if valid_connection:
                    final_arrival_time_str = valid_connection['second_leg']['arrival']
                    
                    # Calculate total duration based on the first valid connection
                    dep_time_obj = datetime.strptime(first_leg['departure'], '%H:%M').time()
                    dep_dt_full = self.london_now.replace(hour=dep_time_obj.hour, minute=dep_time_obj.minute, second=0, microsecond=0)
                    
                    arr_time_obj = datetime.strptime(final_arrival_time_str, '%H:%M').time()
                    arr_dt_full = dep_dt_full.replace(hour=arr_time_obj.hour, minute=arr_time_obj.minute)
                    
                    if arr_dt_full < dep_dt_full:
                        arr_dt_full += timedelta(days=1)
                        
                    total_duration = int((arr_dt_full - dep_dt_full).total_seconds() / 60)
                    total_duration_str = calculate_duration_str(total_duration)
                else:
                    # Fallback if all connections are cancelled
                    final_arrival_time_str = connections[0]['second_leg']['arrival']
                    total_duration_str = "Unknown" 

                journey = {
                    "type": "One Change",
                    "first_leg": first_leg,
                    "connections": connections,
                    "totalDuration": total_duration_str,
                    "arrivalTime": final_arrival_time_str,
                    "departureTime": first_leg['departure'],
                    "segment_id": self.segment_id_counter,
                    "live_updated_at": self.london_now.strftime('%H:%M:%S')
                }
                self.journeys.append(journey)
                self.segment_id_counter += 1
                print(f"✓ Created ONE CHANGE Journey (Segment ID {journey['segment_id']}): Depart {journey['departureTime']} / Arrive {journey['arrivalTime']}")


def save_rail_data():
    """Initializes the TFL data harvester and saves it to JSON."""
    harvester = TflRailDataHarvester()
    
    # 1. Fetch raw data for the entire route
    tfl_data = harvester.fetch_tfl_journeys(STATION_CODES['FROM'], STATION_CODES['TO'])
    
    # 2. Process, filter, and stitch connections
    if tfl_data:
        harvester.process_tfl_journeys(tfl_data)
    
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
        print(f"FATAL ERROR: Failed to run live data script. Missing library: {e}. Please ensure 'requests' and 'pytz' are installed.")

    'IMW': 'Imperial Wharf Rail Station'
}
OPERATORS = ['Southern', 'London Overground', 'South Western Railway']
PLATFORMS = {
    'SRC': ['1', '2'],
    'CLJ': ['1', '2', '3', '4', '5', '6'],
    'IMW': ['1']
}

# --- Utility Functions ---

def format_time(dt):
    """Formats a datetime object to HH:MM string."""
    return dt.strftime('%H:%M')

def calculate_duration_str(start_time, end_time):
    """Calculates duration string (e.g., '15 min') between two datetime objects."""
    # Handle wrap-around for end time
    if end_time < start_time:
        end_time += timedelta(days=1)
    
    duration_mins = int((end_time - start_time).total_seconds() / 60)
    return f"{duration_mins} min"

def get_random_status():
    """Simulates random live status."""
    return random.choice(['On Time', 'Delayed 5 min', 'Cancelled'])

# --- Mock Data Generation ---

class MockRailData:
    """
    Simulates finding and stitching rail journeys for the required route.
    """
    def __init__(self):
        self.journeys = []
        self.segment_id_counter = 1
        # Set the current time explicitly to London time
        self.london_now = datetime.now(LONDON_TIMEZONE)

    def generate_mock_leg(self, origin_code, dest_code, departure_dt, duration_mins):
        """Generates a single, realistic-looking leg for the journey."""
        arrival_dt = departure_dt + timedelta(minutes=duration_mins)
        operator = random.choice(OPERATORS)
        
        # Determine platform keys based on station code
        departure_platform_key = f"departurePlatform_{origin_code}"
        
        return {
            "origin": STATIONS[origin_code],
            "destination": STATIONS[dest_code],
            "departure": format_time(departure_dt),
            "scheduled_departure": format_time(departure_dt),
            "arrival": format_time(arrival_dt),
            departure_platform_key: random.choice(PLATFORMS.get(origin_code, ['TBC'])),
            "operator": operator,
            "status": get_random_status()
        }

    def find_next_departures(self, count):
        """
        Generates a sequence of the next 'count' available departure times,
        ensuring they are all in the future relative to the London time.
        (Simulated frequency is 10 minutes, e.g., :00, :10, :20...)
        """
        departures = []
        
        # Start checking 1 minute from now in London Time
        now_dt = self.london_now + timedelta(minutes=1)
        current_dt = now_dt.replace(second=0, microsecond=0)
        
        SIMULATED_FREQUENCY_MINS = 10 
        
        # Calculate how many minutes until the next 10-minute interval (0, 10, 20, etc.)
        target_minute_remainder = current_dt.minute % SIMULATED_FREQUENCY_MINS
        
        if target_minute_remainder == 0:
            # If we are exactly at an interval (e.g., 14:30), the next one is 10 minutes later (14:40)
            time_to_next_slot = SIMULATED_FREQUENCY_MINS
        else:
            # Calculate minutes needed to reach the next interval
            time_to_next_slot = SIMULATED_FREQUENCY_MINS - target_minute_remainder

        # Advance current_dt to the first truly future departure time
        current_dt += timedelta(minutes=time_to_next_slot)

        # Generate the next 'count' departures spaced by the simulated frequency
        while len(departures) < count:
            departures.append(current_dt)
            current_dt += timedelta(minutes=SIMULATED_FREQUENCY_MINS)
            
        return departures

    def find_journeys(self):
        """Main function to find and combine the next three complete journeys."""
        
        # 1. Determine the next available departure slots from SRC
        src_departures = self.find_next_departures(MAX_JOURNEYS_TO_SAVE)
        print(f"DEBUG: Found {len(src_departures)} unique departure times: {[format_time(dt) for dt in src_departures]} (All relative to London/UK time)")

        for i, src_dep_dt in enumerate(src_departures):
            # Simulate a mix of Direct and One-Change journeys (alternating)
            is_direct = (i % 2 == 0) 
            
            if is_direct:
                # --- Scenario 1: Direct Journey (SRC -> IMW) ---
                total_duration_mins = random.randint(30, 35)
                first_leg = self.generate_mock_leg('SRC', 'IMW', src_dep_dt, total_duration_mins)
                
                total_duration_str = calculate_duration_str(src_dep_dt, src_dep_dt + timedelta(minutes=total_duration_mins))
                
                journey = {
                    "type": "Direct",
                    "first_leg": first_leg,
                    "connections": [],
                    "totalDuration": total_duration_str,
                    "arrivalTime": first_leg['arrival'],
                    "departureTime": first_leg['departure'],
                    "segment_id": self.segment_id_counter,
                    "live_updated_at": self.london_now.strftime('%H:%M:%S')
                }
                self.journeys.append(journey)
            
            else:
                # --- Scenario 2: One Change Journey (SRC -> CLJ -> IMW) ---
                
                # --- Leg 1: SRC to CLJ ---
                duration_leg1 = random.randint(10, 15)
                clj_arr_dt = src_dep_dt + timedelta(minutes=duration_leg1)
                first_leg = self.generate_mock_leg('SRC', 'CLJ', src_dep_dt, duration_leg1)
                
                # --- Connections from CLJ to IMW ---
                connections = []
                # Find up to 3 connections leaving CLJ shortly after arrival
                
                clj_departure_base = clj_arr_dt + timedelta(minutes=random.randint(4, 7))
                
                for j in range(3): # Find 3 connection options
                    # Ensure departure times are staggered
                    clj_dep_dt = clj_departure_base + timedelta(minutes=j * 7) 
                    duration_leg2 = random.randint(4, 6)
                    
                    # Generate Leg 2 data
                    second_leg = self.generate_mock_leg('CLJ', 'IMW', clj_dep_dt, duration_leg2)
                    
                    # Calculate transfer time
                    transfer_time = calculate_duration_str(clj_arr_dt, clj_dep_dt)

                    connections.append({
                        "transferTime": transfer_time,
                        "second_leg": second_leg
                    })
                
                # Calculate total journey time based on the first connection
                first_connection_arrival = connections[0]['second_leg']['arrival']
                
                # Calculate Total Duration string (using the first connection)
                total_duration_str = calculate_duration_str(
                    src_dep_dt, 
                    src_dep_dt + timedelta(minutes=duration_leg1) + 
                    timedelta(minutes=int(connections[0]['transferTime'].split(' ')[0])) + 
                    timedelta(minutes=duration_leg2)
                )

                journey = {
                    "type": "One Change",
                    "first_leg": first_leg,
                    "connections": connections,
                    "totalDuration": total_duration_str,
                    "arrivalTime": first_connection_arrival,
                    "departureTime": first_leg['departure'],
                    "segment_id": self.segment_id_counter,
                    "live_updated_at": self.london_now.strftime('%H:%M:%S')
                }
                self.journeys.append(journey)

            journey_type = "DIRECT" if is_direct else "ONE CHANGE"
            print(f"✓ Created {journey_type} Journey (Segment ID {self.segment_id_counter}): Depart {journey['departureTime']} / Arrive {journey['arrivalTime']}")

            self.segment_id_counter += 1
            
            if len(self.journeys) >= MAX_JOURNEYS_TO_SAVE:
                break


def save_rail_data():
    """Initializes the mock data generation and saves it to JSON."""
    harvester = MockRailData()
    harvester.find_journeys()
    
    data_to_save = harvester.journeys[:MAX_JOURNEYS_TO_SAVE]

    try:
        with open(LIVE_DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
        print(f"✓ Successfully generated and saved {len(data_to_save)} journey segments to {LIVE_DATA_FILE}")
    except Exception as e:
        print(f"Error saving data to JSON: {e}")

if __name__ == '__main__':
    # Attempt to import and install pytz if it fails, ensuring the script runs smoothly in different environments
    try:
        save_rail_data()
    except NameError:
        print("Note: The 'pytz' library is required for precise UK timezone handling. Please ensure it is installed if running locally.")
        # Fallback to the previous behavior if pytz is not available, using the local system time
        global LONDON_TIMEZONE 
        LONDON_TIMEZONE = pytz.timezone('UTC') # Set to UTC as a safe default if local TZ cannot be determined
