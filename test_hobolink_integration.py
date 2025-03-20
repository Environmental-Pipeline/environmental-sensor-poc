#!/usr/bin/env python
"""
Test script to verify HOBOlink integration with EnvironmentData class.
This script creates a temporary .env file from the test_hobolink_env file
and tests the HOBOlink authentication and data retrieval functions directly.
"""

import os
import sys
import shutil
from EnvironmentData import EnvironmentData

def main():
    # Use test_hobolink_env by default, or specify env file as command line arg
    env_file = sys.argv[1] if len(sys.argv) > 1 else "test_hobolink_env"
    print(f"Loading environment from: {env_file}")
    
    # Make a temporary backup of .env if it exists
    backup_created = False
    if os.path.exists(".env"):
        shutil.copy2(".env", ".env.backup")
        backup_created = True
    
    # Copy the test env file to .env
    shutil.copy2(env_file, ".env")
    
    try:
        # Create a minimal EnvironmentData instance without initializing database
        # to avoid CORIS API calls
        ed = EnvironmentData.__new__(EnvironmentData)
        
        # Set up minimal required attributes manually
        ed.logger = type('DummyLogger', (), {
            'info': lambda msg: print(f"INFO: {msg}"),
            'debug': lambda msg: print(f"DEBUG: {msg}"),
            'error': lambda msg: print(f"ERROR: {msg}"),
            'warning': lambda msg: print(f"WARNING: {msg}")
        })
        ed.logger_err = ed.logger
        
        # Define the error method manually
        ed.error = lambda msg, raise_exception=False: (
            print(f"ERROR: {msg}"),
            (_ for _ in ()).throw(Exception(msg)) if raise_exception else None
        )[1]
        
        # Read environment variables manually
        def read_env_variable(var_name, default=None):
            try:
                with open('.env') as f:
                    for line in f:
                        if line.strip() and not line.strip().startswith('#') and line.startswith(var_name):
                            return line.split('=', 1)[1].strip()
            except Exception as e:
                print(f"Error reading environment variable {var_name}: {e}")
            return default
        
        # Set up HOBOlink configuration
        ed.hobolink = {
            'client_id': read_env_variable('HOBOLINK_CLIENT_ID'),
            'client_secret': read_env_variable('HOBOLINK_CLIENT_SECRET'),
            'user_id': read_env_variable('HOBOLINK_USER_ID'),
            'loggers': read_env_variable('HOBOLINK_LOGGERS', '').split(','),
            'enabled': True,
            'token': None,
            'token_expiry': 0
        }
        
        # Check if credentials are available
        ed.hobolink['available'] = (
            ed.hobolink['client_id'] is not None and 
            ed.hobolink['client_secret'] is not None and 
            ed.hobolink['user_id'] is not None and 
            len(ed.hobolink['loggers']) > 0 and 
            ed.hobolink['loggers'][0] != ''
        )
        
        # Add missing methods
        ed.get_current_utc = lambda: int(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).timestamp())
        
        print("HOBOLINK CONFIGURATION:")
        print(f"Client ID available: {ed.hobolink['client_id'] is not None}")
        print(f"Client Secret available: {ed.hobolink['client_secret'] is not None}")
        print(f"User ID available: {ed.hobolink['user_id'] is not None}")
        print(f"Loggers configured: {ed.hobolink['loggers']}")
        print(f"Credentials available: {ed.hobolink['available']}")
        
        if not ed.hobolink['available']:
            print("ERROR: HOBOlink credentials not available. Check your .env file.")
            return
        
        # Test HOBOlink token retrieval
        print("\nTesting HOBOlink authentication...")
        token = ed.get_hobolink_token()
        if token:
            print("✓ Successfully retrieved HOBOlink token")
            print(f"Token (first 10 chars): {token[:10]}...")
        else:
            print("✗ Failed to retrieve HOBOlink token")
            return
        
        # Test HOBOlink data retrieval
        print("\nTesting HOBOlink data retrieval...")
        hobolink_data = ed.get_hobolink_data(days_back=30)  # Try for the last 30 days
        if hobolink_data is not None:
            print(f"✓ Successfully retrieved {hobolink_data.shape[0]} HOBOlink readings")
            
            # Show sample of the data
            print("\nSample of HOBOlink data:")
            print(hobolink_data.head(5))
        else:
            print("✗ Failed to retrieve HOBOlink data")
        
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Restore the original .env file if we created a backup
        if backup_created:
            shutil.copy2(".env.backup", ".env")
            os.remove(".env.backup")
        else:
            # Otherwise remove the temporary .env file
            if os.path.exists(".env"):
                os.remove(".env")

if __name__ == "__main__":
    main() 