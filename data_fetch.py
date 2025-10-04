import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime
import os

# --- CONFIGURATION ---
# Note: The API token is now stored securely in the Python script running on the runner.
USER_TOKEN = "8aaaf362-b5d6-4886-9c24-08e137bd4a7b"
DARWIN_API_ENDPOINT = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb9.asmx"
ORIGIN_CRS = "SRC"  # Streatham Common
DESTINATION_CRS = "IMW"  # Imperial Wharf
NUM_TRAINS = 5
DATA_FILE = "data.json"

# XML Namespaces (required for parsing the SOAP response)
NS = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope',
    'ldb': 'http://thalesgroup.com/RTTI/2017-02-02/ldb/',
    'ldbsv': 'http://thalesgroup.com/RTTI/2017-02-02/ldbsv/',
}
# --- END CONFIGURATION ---

def get_soap_payload():
    """Constructs the SOAP XML payload for GetDepartureBoard."""
    return f"""
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:typ="http://thalesgroup.com/RTTI/2017-02-02/Token/types" xmlns:ldb="http://thalesgroup.com/RTTI/2017-02-02/ldb/">
    <soap:Header>
        <typ:AccessToken>
            <typ:TokenValue>{USER_TOKEN}</typ:TokenValue>
        </typ:AccessToken>
    </soap:Header>
    <soap:Body>
        <ldb:GetDepartureBoardRequest>
            <ldb:numRows>{NUM_TRAINS}</ldb:numRows>
            <ldb:crs>{ORIGIN_CRS}</ldb:crs>
            <ldb:filterCrs>{DESTINATION_CRS}</ldb:filterCrs>
            <ldb:filterType>to</ldb:filterType>
            <ldb:timeOffset>0</ldb:timeOffset>
            <ldb:timeWindow>120</ldb:timeWindow>
            <ldb:includeDetails>true</ldb:includeDetails>
        </ldb:GetDepartureBoardRequest>
    </soap:Body>
</soap:Envelope>
"""

def get_xml_text(node, tag):
    """Safely extracts text content from an XML sub-element."""
    # This uses a simple XPath-like approach compatible with Python's ET
    element = node.find(tag, NS)
    return element.text if element is not None else 'N/A'

def parse_calling_points(service_node):
    """Parses calling points for a single service."""
    # Corrected path for calling points
    calling_points_node = service_node.find('ldbsv:callingPoints', NS)
    if calling_points_node is None:
        return []

    points_list = []
    for cp in calling_points_node.findall('ldbsv:callingPoint', NS):
        location_name = get_xml_text(cp, 'ldb:locationName')
        st = get_xml_text(cp, 'ldb:st')
        et = get_xml_text(cp, 'ldb:et')

        status = 'On Time'
        if et in ['Delayed', 'Cancelled', 'N/A']:
            status = et
        elif et and et != st and et != 'On time':
            status = 'Updated'

        points_list.append({
            'locationName': location_name,
            'scheduledTime': st,
            'estimatedTime': et,
            'status': status,
            'isInterchange': 'Clapham Junction' in location_name,
        })
    return points_list

def parse_xml_to_json(xml_root):
    """Converts the XML response into a structured JSON list."""
    services = []
    
    # Path to find services (change depending on the API response structure)
    services_path = 'soap:Body/ldb:GetDepartureBoardResponse/ldb:GetDepartureBoardResult/ldb:trainServices/ldb:service'
    
    # Use ET.find() and ET.findall() with the correct namespaces
    departure_board_result = xml_root.find('soap:Body/ldb:GetDepartureBoardResponse/ldb:GetDepartureBoardResult', NS)
    if departure_board_result is None:
        return [] # No result body found

    # Handle case where 'trainServices' might not exist (e.g., no services found)
    train_services_node = departure_board_result.find('ldb:trainServices', NS)
    if train_services_node is None:
        return []

    for service_node in train_services_node.findall('ldb:service', NS):
        std = get_xml_text(service_node, 'ldb:std')
        etd = get_xml_text(service_node, 'ldb:etd')
        
        delay_status = 'On Time' if etd in ['On time', std] else ('Delayed' if etd == 'Delayed' else etd)
        
        services.append({
            'scheduledDepartureTime': std,
            'estimatedDepartureTime': etd,
            'delayStatus': delay_status,
            'destination': get_xml_text(service_node, 'ldb:destination'),
            'platform': get_xml_text(service_node, 'ldb:platform'),
            'operator': get_xml_text(service_node, 'ldb:operator'),
            'callingPoints': parse_calling_points(service_node),
        })
        
    return services

def fetch_and_save():
    """Fetches data from Darwin API and appends it to data.json."""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] Starting data fetch for {ORIGIN_CRS} to {DESTINATION_CRS}...")
    
    try:
        response = requests.post(
            DARWIN_API_ENDPOINT,
            data=get_soap_payload(),
            headers={'Content-Type': 'application/soap+xml; charset=utf-8'},
            timeout=30
        )
        response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)

        # Parse XML
        xml_root = ET.fromstring(response.content)
        
        # Check for SOAP Faults
        fault = xml_root.find('soap:Body/soap:Fault', NS)
        if fault is not None:
            fault_string_node = fault.find('soap:Reason/soap:Text', NS)
            fault_string = fault_string_node.text if fault_string_node is not None else 'Unknown Fault'
            raise Exception(f"API Fault: {fault_string}")

        # Extract services
        services_data = parse_xml_to_json(xml_root)
        
        # Prepare the snapshot object
        snapshot = {
            'timestamp': timestamp,
            'status': 'Success',
            'services': services_data,
            'serviceCount': len(services_data)
        }
        
        print(f"[{timestamp}] Success. Found {len(services_data)} services.")
        
    except Exception as e:
        print(f"[{timestamp}] ERROR during fetch: {e}")
        snapshot = {
            'timestamp': timestamp,
            'status': 'Error',
            'errorMessage': str(e),
            'services': [],
            'serviceCount': 0
        }

    # Load existing data or initialize an empty list
    data = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                # Handle case where file is empty or corrupted JSON
                file_content = f.read().strip()
                if file_content:
                    data = json.loads(file_content)
        except (json.JSONDecodeError, FileNotFoundError):
            print(f"Warning: Existing {DATA_FILE} is empty or invalid. Starting fresh.")
            data = []

    # Ensure data is an array
    if not isinstance(data, list):
        data = []
        
    # Append the new snapshot and save
    data.append(snapshot)
    with open(DATA_FILE, 'w') as f:
        # Save with indentation for readability on GitHub
        json.dump(data, f, indent=2)
    
    print(f"[{timestamp}] Data saved. Total snapshots: {len(data)}")

if __name__ == "__main__":
    fetch_and_save()
