"""Fixture loader utilities for tests."""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


def get_fixture_path(fixture_name: str, category: str = "athlete") -> Path:
    """Get path to a fixture file."""
    base_dir = Path(__file__).parent.parent
    return base_dir / "fixtures" / category / fixture_name


def load_fixture(fixture_name: str, category: str = "athlete") -> Dict[str, Any]:
    """
    Load a JSON fixture file.
    
    Args:
        fixture_name: Name of fixture file (e.g., "weekly_sample_1_input.json")
        category: Category folder (default: "athlete")
    
    Returns:
        Parsed JSON as dictionary
    """
    fixture_path = get_fixture_path(fixture_name, category)
    
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")
    
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text_fixture(fixture_name: str, category: str = "athlete") -> str:
    """Load a text fixture file."""
    fixture_path = get_fixture_path(fixture_name, category)
    
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")
    
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()

