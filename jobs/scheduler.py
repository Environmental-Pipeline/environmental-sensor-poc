#!/usr/bin/env python3
"""
Cross-platform scheduler for environmental sensor data pipeline.
Replaces cron for better Windows/Docker compatibility.

This scheduler:
- Runs data pulls every 15 minutes (matching Conserv API frequency)
- Runs consolidation at the 45-minute mark of each hour
- Provides graceful shutdown handling
- Works on Windows, Linux, and in containers
"""

import sys
import os
import time
import signal
import logging
import threading
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, '/src/')  # Docker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Local

from EnvironmentData import EnvironmentData

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('data/scheduler.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)


def read_env_variable(var_name: str, default: str = None) -> str:
    """Read a variable from the .env file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists('/src/.env'):
        env_path = '/src/.env'

    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and line.startswith(var_name + '='):
                    return line.split('=', 1)[1].strip()
    except FileNotFoundError:
        pass

    return default


def get_environment_data() -> EnvironmentData:
    """Create and return an EnvironmentData instance with config from .env."""
    return EnvironmentData(
        days_back=int(read_env_variable('DAYS_BACK', '730')),
        testing=read_env_variable('TESTING', 'False').lower() == 'true',
        coris_enabled=read_env_variable('CORIS_ENABLED', 'False').lower() == 'true',
        conserv_enabled=read_env_variable('CONSERV_ENABLED', 'False').lower() == 'true',
        licor_enabled=read_env_variable('LICOR_ENABLED', 'False').lower() == 'true',
    )


class SensorScheduler:
    """
    Scheduler for sensor data pipeline operations.

    Schedule:
    - Pull readings: Every 15 minutes (0, 15, 30, 45 past the hour)
    - Consolidate: At 45 minutes past each hour
    """

    def __init__(self):
        self.running = True
        self.last_pull_minute = -1
        self.last_consolidate_hour = -1

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def run_pull(self):
        """Execute the data pull job."""
        try:
            logger.info("Starting scheduled data pull...")
            api = get_environment_data()
            api.get_current_readings()
            logger.info("Data pull completed successfully")
        except Exception as e:
            logger.error(f"Data pull failed: {e}", exc_info=True)

    def run_consolidate(self):
        """Execute the consolidation job."""
        try:
            logger.info("Starting scheduled consolidation...")
            api = get_environment_data()
            api.consolidate_readings()
            logger.info("Consolidation completed successfully")
        except Exception as e:
            logger.error(f"Consolidation failed: {e}", exc_info=True)

    def should_pull(self, now: datetime) -> bool:
        """Check if we should run a data pull (every 15 minutes)."""
        current_slot = now.minute // 15
        if current_slot != self.last_pull_minute // 15 or self.last_pull_minute == -1:
            # Only pull on the 0, 15, 30, 45 minute marks
            if now.minute in [0, 15, 30, 45]:
                self.last_pull_minute = now.minute
                return True
        return False

    def should_consolidate(self, now: datetime) -> bool:
        """Check if we should run consolidation (at 45 past each hour)."""
        if now.minute == 45 and now.hour != self.last_consolidate_hour:
            self.last_consolidate_hour = now.hour
            return True
        return False

    def run(self):
        """Main scheduler loop."""
        logger.info("=" * 60)
        logger.info("Sensor Data Scheduler Started")
        logger.info("Schedule: Pull every 15 min, Consolidate at :45 each hour")
        logger.info("=" * 60)

        while self.running:
            now = datetime.now()

            # Check if we need to run tasks
            if self.should_pull(now):
                # Run in a thread to not block the scheduler
                thread = threading.Thread(target=self.run_pull)
                thread.start()

            if self.should_consolidate(now):
                thread = threading.Thread(target=self.run_consolidate)
                thread.start()

            # Sleep for 30 seconds before checking again
            # This gives us good resolution without busy-waiting
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("Scheduler stopped")


def main():
    """Entry point for the scheduler."""
    # Ensure data directory exists
    os.makedirs('data', exist_ok=True)

    scheduler = SensorScheduler()
    scheduler.run()


if __name__ == "__main__":
    main()
