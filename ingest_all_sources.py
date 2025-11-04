#!/usr/bin/env python3
"""
Unified Data Ingestion Script for Environmental Sensor Data

This script integrates data from multiple sources:
- Coris API (existing functionality)
- Conserv API (5 customer tenants)

Designed to run in Docker container, completing in <15 minutes.
"""

import os
import sys
import logging
import datetime
import traceback
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


def main():
    """Main ingestion workflow."""
    logger = setup_logging()
    start_time = datetime.datetime.now()

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
        conserv_enabled = (
            read_env_variable("CONSERV_ENABLED", "False").lower() == "true"
        )
        testing = read_env_variable("TESTING", "False").lower() == "true"
        run_window_hours = int(read_env_variable("RUN_WINDOW_HOURS", "24"))

        # Debug Conserv API keys availability
        conserv_keys_found = []
        for customer_id in [1545, 333, 307, 2671, 1696]:
            key = read_env_variable(f"CONSERV_API_KEY_{customer_id}")
            if key:
                conserv_keys_found.append(customer_id)

        # logger.info(f"  CATS_USER_ID: {cats_user_id}")
        # logger.info(f"  CONSERV_ENABLED: {conserv_enabled}")
        # logger.info(f"  TESTING: {testing}")
        # logger.info(f"  RUN_WINDOW_HOURS: {run_window_hours}")
        # logger.info(f"  Conserv API keys found for customers: {conserv_keys_found}")

        # Critical validation
        if conserv_enabled and not conserv_keys_found:
            logger.error("CONSERV_ENABLED=True but no Conserv API keys found!")
        elif conserv_enabled:
            logger.info(
                f"{len(conserv_keys_found)} Conserv customers"
            )
        elif not conserv_enabled:
            logger.info("Conserv integration is disabled.")

        env_data = EnvironmentData(
            CatsUserID=cats_user_id,
            out_of_scope=['-80', 'Cryo tank', 'Water'],
            data_path="./data",
            conserv_enabled=conserv_enabled,
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

        # This method now handles both Coris and Conserv APIs
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
