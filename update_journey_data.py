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
    'CLJ': 'Clapham Junction Rail Station',
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
