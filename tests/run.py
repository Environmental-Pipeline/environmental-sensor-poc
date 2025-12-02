#!/usr/bin/env python3
"""
Test runner script that executes all test scripts in the test directory.
Excludes itself from execution.
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    """Run all test scripts in the current directory except this script."""
    
    # Get the directory where this script is located
    test_dir = Path(__file__).parent
    
    # Get the name of this script to exclude it
    current_script = Path(__file__).name
    
    # Find all Python files in the test directory
    all_python_files = [f for f in os.listdir(test_dir) if f.endswith('.py') and f != current_script]
    
    # Define test order - some tests need to run after others
    ordered_tests = [
        'test_conserv_integration.py',  # This needs to run first to create data
        'test_multiple_days.py',        # This needs data to exist first
    ]
    
    # Add remaining tests in alphabetical order
    remaining_tests = [f for f in all_python_files if f not in ordered_tests]
    remaining_tests.sort()
    
    python_files = ordered_tests + remaining_tests
    
    # Filter to only include files that actually exist
    python_files = [f for f in python_files if f in all_python_files]
    
    if not python_files:
        print("No test scripts found to run.")
        return 0
    
    print(f"Found {len(python_files)} test scripts to run:")
    for script in python_files:
        print(f"  - {script}")
    print()
    
    # Run each test script
    failed_tests = []
    successful_tests = []
    
    for script in python_files:
        script_path = test_dir / script
        print(f"Running {script}...")
        print("-" * 50)
        
        try:
            # Run the script and capture output
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=test_dir,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            # Print the output
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr)
            
            if result.returncode == 0:
                print(f"✅ {script} completed successfully")
                successful_tests.append(script)
            else:
                print(f"❌ {script} failed with exit code {result.returncode}")
                failed_tests.append(script)
                
        except subprocess.TimeoutExpired:
            print(f"❌ {script} timed out after 5 minutes")
            failed_tests.append(script)
        except Exception as e:
            print(f"❌ {script} failed with exception: {e}")
            failed_tests.append(script)
        
        print("-" * 50)
        print()
    
    # Print summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Total tests: {len(python_files)}")
    print(f"Successful: {len(successful_tests)}")
    print(f"Failed: {len(failed_tests)}")
    
    if successful_tests:
        print("\n✅ Successful tests:")
        for test in successful_tests:
            print(f"  - {test}")
    
    if failed_tests:
        print("\n❌ Failed tests:")
        for test in failed_tests:
            print(f"  - {test}")
        return 1
    
    print("\n🎉 All tests passed!")
    return 0

if __name__ == "__main__":
    sys.exit(main())