#!/usr/bin/env python3
"""
Unified Data Ingestion Script for Environmental Sensor Data

This script integrates data from multiple sources:
- Coris API (existing functionality)
- Conserv API (5 customer tenants)
- LI-COR API (additional sensor data source)

Designed to run in Docker container, completing in <15 minutes.
"""

import os
import sys
import logging
import datetime
import traceback
import shutil
from EnvironmentData import EnvironmentData


def setup_logging():
    """Set up logging for the ingestion process."""
    # Create logs directory if it doesn't exist
    log_dir = "./data/logs"
    os.makedirs(log_dir, exist_ok=True)

    # Configure logging
    log_file = f'{log_dir}/ingest_all_sources_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )

    return logging.getLogger("ingest_all_sources")


def read_env_variable(var_name, default=None):
    """Read environment variable from .env file or environment."""
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith(var_name):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass

    return os.getenv(var_name, default)


def clear_data_folder():
    """Clear the data folder and logs to ensure fresh start with consolidated schema."""
    
    data_dir = "./data"
    if os.path.exists(data_dir):
        # Clear everything including logs
        for item in os.listdir(data_dir):
            item_path = os.path.join(data_dir, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        print(f"Cleared data directory: {data_dir}")
    else:
        os.makedirs(data_dir, exist_ok=True)
        print(f"Created data directory: {data_dir}")


def main():
    """Main ingestion workflow."""
    start_time = datetime.datetime.now()

    # Clear data folder BEFORE setting up logging to avoid file locks
    print("Clearing data folder for fresh start...")
    clear_data_folder()
    print("Data folder cleared successfully")
    
    # Now set up logging after clearing
    logger = setup_logging()

    try:
        logger.info("Starting ingest_all_sources.py")
        # logger.info(f"  Current working directory: {os.getcwd()}")
        # logger.info(f"  .env file exists: {os.path.exists('.env')}")
        # if os.path.exists(".env"):
        #     with open(".env") as f:
        #         env_lines = f.readlines()
                # logger.info(f"  .env file has {len(env_lines)} lines")
                # Log non-sensitive config lines
                # for line in env_lines[:5]:  # Only first 5 lines to avoid API keys
                #     if not any(key in line for key in ["API_KEY", "KEY"]):
                #         logger.info(f"    {line.strip()}")

        # Required configuration
        cats_user_id = int(read_env_variable("CATS_USER_ID"))
        coris_enabled = (
            read_env_variable("CORIS_ENABLED", "True").lower() == "true"
        )
        conserv_enabled = (
            read_env_variable("CONSERV_ENABLED", "False").lower() == "true"
        )
        licor_enabled = (
            read_env_variable("LICOR_ENABLED", "False").lower() == "true"
        )
        testing = read_env_variable("TESTING", "False").lower() == "true"
        run_window_hours = int(read_env_variable("RUN_WINDOW_HOURS", "24"))

        # Debug Coris API key availability
        coris_key_found = read_env_variable("CORIS_API_KEY") is not None

        # Debug Conserv API keys availability
        conserv_keys_found = []
        for customer_id in [1545, 333, 307, 2671, 1696]:
            key = read_env_variable(f"CONSERV_API_KEY_{customer_id}")
            if key:
                conserv_keys_found.append(customer_id)

        # Debug LI-COR API key availability
        licor_key_found = read_env_variable("LICOR_API_KEY") is not None

        # logger.info(f"  CATS_USER_ID: {cats_user_id}")
        # logger.info(f"  CONSERV_ENABLED: {conserv_enabled}")
        # logger.info(f"  LICOR_ENABLED: {licor_enabled}")
        # logger.info(f"  TESTING: {testing}")
        # logger.info(f"  RUN_WINDOW_HOURS: {run_window_hours}")
        # logger.info(f"  Conserv API keys found for customers: {conserv_keys_found}")
        # logger.info(f"  LI-COR API key found: {licor_key_found}")

        # Critical validation
        if coris_enabled and not coris_key_found:
            logger.error("CORIS_ENABLED=True but CORIS_API_KEY not found!")
        elif coris_enabled:
            logger.info("Coris integration enabled")
        elif not coris_enabled:
            logger.info("Coris integration is disabled.")

        if conserv_enabled and not conserv_keys_found:
            logger.error("CONSERV_ENABLED=True but no Conserv API keys found!")
        elif conserv_enabled:
            logger.info(
                f"{len(conserv_keys_found)} Conserv customers"
            )
        elif not conserv_enabled:
            logger.info("Conserv integration is disabled.")

        if licor_enabled and not licor_key_found:
            logger.error("LICOR_ENABLED=True but LICOR_API_KEY not found!")
        elif licor_enabled:
            logger.info("LI-COR integration enabled")
        elif not licor_enabled:
            logger.info("LI-COR integration is disabled.")

        env_data = EnvironmentData(
            data_path="./data",
            coris_enabled=coris_enabled,
            conserv_enabled=conserv_enabled,
            licor_enabled=licor_enabled,
            testing=testing,
        )

        # logger.info("EnvironmentData initialized successfully")
        # logger.info(f"  Conserv enabled: {env_data.conserv_enabled}")
        # if env_data.conserv_client:
        #     logger.info(
        #         f"  Conserv customers: {len(env_data.conserv_client.customers)}"
        #     )
        # logger.info(f"  Cron status: {env_data.cron_status}")

        # ============ PULL CURRENT READINGS ============
        logger.info("Pulling current readings from all sources")

        # This method now handles Coris, Conserv, and LI-COR APIs
        env_data.get_current_readings()

        logger.info("Current readings completed successfully")

        # ============ CONSOLIDATE READINGS ============
        logger.info("Consolidating readings...")

        # This method now handles mixed Coris/Conserv data
        env_data.consolidate_readings()

        # logger.info("Data consolidation completed successfully")

        # ============ SUCCESS SUMMARY ============
        end_time = datetime.datetime.now()
        duration = end_time - start_time
        logger.info(f"Completed ingest_all_sources.py in {duration.total_seconds() / 60:.2f} minutes")

        # logger.info("=" * 60)
        # logger.info("UNIFIED DATA INGESTION COMPLETED SUCCESSFULLY")
        # logger.info(f"Start time: {start_time}")
        # logger.info(f"End time: {end_time}")
        # logger.info(f"Total duration: {duration}")
        # logger.info(f"Duration in minutes: {duration.total_seconds() / 60:.2f}")

        # Check if we're under the 15-minute target
        # if duration.total_seconds() > 900:  # 15 minutes = 900 seconds
        #     logger.warning(f"WARNING: DURATION EXCEEDED 15 MINUTES: {duration}")
        # else:
        #     logger.info("SUCCESS: COMPLETED WITHIN 15-MINUTE TARGET")

        # logger.info("=" * 60)

        # Clean up
        env_data.close()

        return 0

    except Exception as e:
        logger.error("=" * 60)
        logger.error("CRITICAL ERROR IN DATA INGESTION")
        logger.error(f"Error: {str(e)}")
        logger.error("Traceback:")
        logger.error(traceback.format_exc())
        logger.error("=" * 60)

        # Try to clean up if env_data was created
        try:
            if "env_data" in locals():
                env_data.close()
        except Exception:
            pass

    return 1


if __name__ == "__main__":
    sys.exit(main())
