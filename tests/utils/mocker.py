"""Mock LLM responses for testing."""
import json
from typing import Dict, Any, Optional
from unittest.mock import patch, MagicMock


def mock_llm(response_data: Dict[str, Any], status_code: int = 200) -> MagicMock:
    """
    Create a mock LLM response.
    
    Args:
        response_data: Dictionary to return as JSON response
        status_code: HTTP status code (default: 200)
    
    Returns:
        Mock response object
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = json.dumps(response_data, indent=2)
    mock_response.json.return_value = response_data
    return mock_response


def mock_llm_empty() -> MagicMock:
    """Mock empty LLM response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json.side_effect = ValueError("Empty response")
    return mock_response


def mock_llm_malformed() -> MagicMock:
    """Mock malformed LLM response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"invalid": json, missing quotes}'
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    return mock_response


def patch_llm_call(response_data: Dict[str, Any]):
    """
    Context manager to patch LLM HTTP calls.
    
    Usage:
        with patch_llm_call(mock_data):
            result = generate_plan(...)
    """
    return patch("httpx.post", return_value=mock_llm(response_data))

