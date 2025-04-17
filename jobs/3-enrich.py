# jobs/3-enrich.py
import sys
import os
import asyncio
from dotenv import load_dotenv

# Add project root to path to allow importing EnvironmentData
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from EnvironmentData import EnvironmentData

def main():
    print("Starting scheduled weather enrichment job...")
    load_dotenv() # Load .env from /src/ inside container

    user_id_str = os.getenv("CATS_USER_ID")
    if not user_id_str:
        print("Error: CATS_USER_ID not found in .env file. Cannot run enrichment.")
        return

    try:
        user_id = int(user_id_str)
    except ValueError:
        print(f"Error: CATS_USER_ID ('{user_id_str}') is not a valid integer.")
        return

    env_data = None # Initialize to ensure it's defined for finally block
    try:
        is_testing_mode = os.getenv("TESTING", 'False').lower() == 'true'
        # Initialize EnvironmentData (assuming it's already initialized, pass testing mode)
        env_data = EnvironmentData(CatsUserID=user_id, testing=is_testing_mode)
        print("EnvironmentData initialized for enrichment.")

        # Run the enrichment
        print("Running enrich_readings_with_weather...")
        asyncio.run(env_data.enrich_readings_with_weather())
        print("Weather enrichment job finished.")

    except Exception as e:
        print(f"An error occurred during the enrichment job: {e}")
        if env_data and env_data.logger:
             env_data.error(f"Weather enrichment job failed: {e}") # Log error using class logger

    finally:
        # Ensure logs are closed properly
        if env_data:
            try:
                env_data.close()
            except Exception as close_e:
                 print(f"Error closing EnvironmentData logs: {close_e}")

if __name__ == "__main__":
    main() 