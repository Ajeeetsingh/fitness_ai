"""Local test runner for athlete mode tests."""
import pytest
import sys

if __name__ == "__main__":
    # Run tests in quiet mode, only show failures
    exit_code = pytest.main([
        "-q",  # Quiet mode
        "-v",  # Verbose output for failures
        "tests/athlete/",  # Only run athlete tests
        "--tb=short"  # Short traceback format
    ])
    sys.exit(exit_code)

