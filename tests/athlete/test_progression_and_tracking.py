"""Tests for progression and tracking_metrics canonicalization."""
import pytest
from app.fitness.workout_plan.pipeline_utils import (
    canonicalize_progression,
    canonicalize_tracking_metrics
)
from tests.utils.validators import assert_progression_format, assert_tracking_metrics_format


class TestProgressionCanonicalization:
    """Test progression canonicalization."""
    
    def test_string_progression_parsed(self):
        """String progression should be parsed and converted to canonical format"""
        prog = "+2.5 kg if conditions met"
        result = canonicalize_progression(prog)
        assert_progression_format(result)
        assert result["week"] == 1
        assert result["type"] in ["load_intensity", "add_weight"]
        assert "condition" in result
    
    def test_dict_progression_with_week(self):
        """Dict progression with week should be canonicalized"""
        prog = {
            "week": 2,
            "type": "load_intensity",
            "value": "5%",
            "condition": "if all sets completed"
        }
        result = canonicalize_progression(prog)
        assert_progression_format(result)
        assert result["week"] == 2
        assert result["type"] == "load_intensity"
        assert result["value"] == "5%"
        assert result["condition"] == "if all sets completed"
    
    def test_dict_progression_with_week_number(self):
        """Dict progression with week_number should map to week"""
        prog = {
            "week_number": 3,
            "typeOfProgression": "distance_increase",
            "percentageIncreasePerWeek": "10%"
        }
        result = canonicalize_progression(prog)
        assert_progression_format(result)
        assert result["week"] == 3
        assert result["type"] == "distance_increase"
        assert result["value"] == "10%"
    
    def test_empty_progression_returns_empty_dict(self):
        """Empty progression should return empty dict"""
        result = canonicalize_progression(None)
        assert result == {}
        
        result = canonicalize_progression({})
        assert result == {}
    
    def test_progression_with_rule_maps_to_condition(self):
        """Progression with 'rule' key should map to 'condition'"""
        prog = {
            "week": 1,
            "rule": "+2.5 kg if conditions met"
        }
        result = canonicalize_progression(prog)
        assert_progression_format(result)
        assert result["condition"] == "+2.5 kg if conditions met"


class TestTrackingMetricsCanonicalization:
    """Test tracking_metrics canonicalization."""
    
    def test_string_metric_with_type(self):
        """String metric with type in parentheses should be parsed"""
        metrics = ["session_RPE(int)", "sets_completed(int)"]
        result = canonicalize_tracking_metrics(metrics)
        assert_tracking_metrics_format(result)
        assert len(result) == 2
        assert result[0]["metric"] == "session_RPE"
        assert result[0]["type"] == "int"
        assert result[1]["metric"] == "sets_completed"
        assert result[1]["type"] == "int"
    
    def test_string_metric_without_type(self):
        """String metric without type should default to 'string'"""
        metrics = ["session_RPE", "notes"]
        result = canonicalize_tracking_metrics(metrics)
        assert_tracking_metrics_format(result)
        assert len(result) == 2
        assert result[0]["metric"] == "session_RPE"
        assert result[0]["type"] == "string"
        assert result[1]["metric"] == "notes"
        assert result[1]["type"] == "string"
    
    def test_dict_metric_with_metric_key(self):
        """Dict metric with 'metric' key should be canonicalized"""
        metrics = [
            {"metric": "session_RPE", "type": "int"},
            {"metric": "sets_completed", "type": "int"}
        ]
        result = canonicalize_tracking_metrics(metrics)
        assert_tracking_metrics_format(result)
        assert len(result) == 2
        assert result[0]["metric"] == "session_RPE"
        assert result[0]["type"] == "int"
    
    def test_dict_metric_with_label_key(self):
        """Dict metric with 'label' key should map to 'metric'"""
        metrics = [
            {"label": "session_RPE", "fieldType": "integer"},
            {"label": "distance", "fieldType": "float"}
        ]
        result = canonicalize_tracking_metrics(metrics)
        assert_tracking_metrics_format(result)
        assert len(result) == 2
        assert result[0]["metric"] == "session_RPE"
        assert result[0]["type"] == "int"  # integer -> int
        assert result[1]["metric"] == "distance"
        assert result[1]["type"] == "float"
    
    def test_dict_metric_with_name_key(self):
        """Dict metric with 'name' key should map to 'metric'"""
        metrics = [{"name": "heart_rate", "type": "int"}]
        result = canonicalize_tracking_metrics(metrics)
        assert_tracking_metrics_format(result)
        assert result[0]["metric"] == "heart_rate"
        assert result[0]["type"] == "int"
    
    def test_type_normalization(self):
        """Type values should be normalized to valid types"""
        metrics = [
            {"metric": "value1", "type": "integer"},
            {"metric": "value2", "type": "number"},
            {"metric": "value3", "type": "bool"},
            {"metric": "value4", "type": "decimal"}
        ]
        result = canonicalize_tracking_metrics(metrics)
        assert result[0]["type"] == "int"
        assert result[1]["type"] == "int"  # number -> int
        assert result[2]["type"] == "boolean"
        assert result[3]["type"] == "float"
    
    def test_empty_metrics_returns_empty_list(self):
        """Empty metrics should return empty list"""
        result = canonicalize_tracking_metrics(None)
        assert result == []
        
        result = canonicalize_tracking_metrics([])
        assert result == []
    
    def test_single_metric_not_in_list(self):
        """Single metric (not in list) should be wrapped in list"""
        metric = "session_RPE(int)"
        result = canonicalize_tracking_metrics(metric)
        assert_tracking_metrics_format(result)
        assert len(result) == 1
        assert result[0]["metric"] == "session_RPE"
        assert result[0]["type"] == "int"

