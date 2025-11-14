"""
Quick Hobolink API Explorer

Simple interactive script to quickly explore raw API responses.

HOW TO RUN:
1. Activate virtual environment: & "C:\Users\super\Documents\arbaiza-consulting\environmental-sensor-poc\.venv\Scripts\Activate.ps1"
2. Set environment variable: $env:HOBOLINK_API_KEY = "your_bearer_token_here"
3. Run: python quick_hobolink_explore.py
4. Follow the prompts to explore different API endpoints

QUICK SAMPLE GENERATION:
You can also generate samples directly with:
python -c "from modules.hobolink_client import HobolinkClient; HobolinkClient().sample_raw_data()"
"""

import json
import datetime
from modules.hobolink_client import HobolinkClient

def pretty_print(data, max_items=3):
    """Pretty print with optional truncation for large datasets."""
    if isinstance(data, list) and len(data) > max_items:
        print(f"📋 Showing first {max_items} of {len(data)} items:")
        truncated_data = data[:max_items]
        print(json.dumps(truncated_data, indent=2, default=str))
        print(f"... ({len(data) - max_items} more items)")
    else:
        print(json.dumps(data, indent=2, default=str))

def explore_devices():
    """Explore devices endpoint."""
    print("\n🔍 Exploring devices endpoint...")
    print("   API Call: GET https://api.licor.cloud/v2/devices?includeSensors=true")
    
    client = HobolinkClient()
    raw_response = client.get_devices(include_sensors=True)
    
    print("\n📄 Raw API Response:")
    pretty_print(raw_response)
    
    return raw_response

def explore_sensor_data(devices_data):
    """Explore sensor data endpoint."""
    print("\n🔍 Exploring sensor data endpoint...")
    
    if not devices_data or len(devices_data) == 0:
        print("❌ No devices available")
        return
    
    # Find device with sensors
    device_with_sensors = None
    for device in devices_data:
        if device.get('sensors') and len(device['sensors']) > 0:
            device_with_sensors = device
            break
    
    if not device_with_sensors:
        print("❌ No devices with sensors found")
        return
    
    device_serial = device_with_sensors['serialNumber']
    sensor_serial = device_with_sensors['sensors'][0]['serialNumber']
    
    print(f"   Using: Device {device_serial}, Sensor {sensor_serial}")
    
    # Get last few hours of data
    end_time = datetime.datetime.now()
    start_time = end_time - datetime.timedelta(hours=6)  # Just 6 hours for quick test
    
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    print(f"   API Call: GET https://api.licor.cloud/v2/data?deviceSerialNumber={device_serial}&sensorSerialNumber={sensor_serial}&startTime={start_ms}&endTime={end_ms}")
    
    client = HobolinkClient()
    raw_response = client.get_sensor_data(
        device_serial=device_serial,
        sensor_serial=sensor_serial,
        start_time_ms=start_ms,
        end_time_ms=end_ms
    )
    
    print("\n📄 Raw API Response:")
    pretty_print(raw_response)

def quick_inspect():
    """Quick inspection mode - just show key info."""
    print("\n⚡ Quick Inspection Mode")
    
    client = HobolinkClient()
    
    print("\n1️⃣ Getting devices...")
    devices = client.get_devices(include_sensors=True)
    
    if isinstance(devices, list):
        print(f"   Found {len(devices)} devices")
        
        devices_with_sensors = [d for d in devices if d.get('sensors')]
        print(f"   {len(devices_with_sensors)} devices have sensors")
        
        if devices_with_sensors:
            sample_device = devices_with_sensors[0]
            print(f"   Sample device: {sample_device.get('serialNumber')} ({sample_device.get('name', 'No name')})")
            print(f"   Sample device sensors: {len(sample_device.get('sensors', []))}")
            
            if sample_device.get('sensors'):
                sample_sensor = sample_device['sensors'][0]
                print(f"   Sample sensor: {sample_sensor.get('serialNumber')} ({sample_sensor.get('sensorName', 'No name')})")
    
    print(f"\n💡 To see full raw responses, use the explore functions above.")

def main():
    """Interactive exploration menu."""
    print("🔍 Hobolink API Explorer")
    print("========================")
    
    try:
        while True:
            print(f"\nWhat would you like to explore?")
            print(f"1. 📋 Devices endpoint (raw JSON)")
            print(f"2. 📊 Sensor data endpoint (raw JSON)")  
            print(f"3. ⚡ Quick inspection (summary)")
            print(f"4. 🚪 Exit")
            
            choice = input("\nEnter choice (1-4): ").strip()
            
            if choice == '1':
                devices = explore_devices()
            elif choice == '2':
                print("First getting devices to find available sensors...")
                devices = HobolinkClient().get_devices(include_sensors=True)
                explore_sensor_data(devices)
            elif choice == '3':
                quick_inspect()
            elif choice == '4':
                print("👋 Goodbye!")
                break
            else:
                print("❌ Invalid choice. Please enter 1-4.")
                
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("Make sure HOBOLINK_API_KEY environment variable is set")

if __name__ == "__main__":
    main()