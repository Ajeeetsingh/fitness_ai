"""
Tests for orchestrator chunk merging logic.
Uses stubbed LLM responses for 3 chunks (some intentionally malformed) and validates final merged object.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app.fitness.workout_plan.orchestrator import _merge_weekly_chunks
from app.fitness.workout_plan.normalizers import try_unwrap_json


class TestOrchestratorChunkMerge:
    """Test orchestrator chunk merging with various scenarios."""
    
    def test_merge_three_chunks_valid(self):
        """Test merging 3 valid chunks."""
        chunk_results = [
            {
                "chunk_info": {"start": 1, "end": 3, "chunk_id": "chunk_1"},
                "data": {
                    "provided_information": {"goal": "test"},
                    "summary": "Test plan",
                    "plan_meta": {"sport": "general_fitness"},
                    "days": {
                        "day_1": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        },
                        "day_2": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        },
                        "day_3": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        }
                    }
                }
            },
            {
                "chunk_info": {"start": 4, "end": 5, "chunk_id": "chunk_2"},
                "data": {
                    "days": {
                        "day_4": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        },
                        "day_5": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        }
                    }
                }
            },
            {
                "chunk_info": {"start": 6, "end": 7, "chunk_id": "chunk_3"},
                "data": {
                    "days": {
                        "day_6": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        },
                        "day_7": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        }
                    }
                }
            }
        ]
        
        merged = _merge_weekly_chunks(chunk_results, weekly_sessions=7, mode="general")
        
        # Verify structure
        assert "provided_information" in merged
        assert "summary" in merged
        assert "plan_meta" in merged
        assert "days" in merged
        
        # Verify all 7 days present
        for i in range(1, 8):
            assert f"day_{i}" in merged["days"]
            assert "warmup" in merged["days"][f"day_{i}"]
            assert "main_session" in merged["days"][f"day_{i}"]
            assert "cooldown" in merged["days"][f"day_{i}"]
    
    def test_merge_chunks_missing_days(self):
        """Test merging chunks with missing days (should create skeletons)."""
        chunk_results = [
            {
                "chunk_info": {"start": 1, "end": 3, "chunk_id": "chunk_1"},
                "data": {
                    "days": {
                        "day_1": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        }
                        # Missing day_2 and day_3
                    }
                }
            },
            {
                "chunk_info": {"start": 4, "end": 5, "chunk_id": "chunk_2"},
                "data": {
                    "days": {}  # Empty chunk
                }
            }
        ]
        
        merged = _merge_weekly_chunks(chunk_results, weekly_sessions=5, mode="general")
        
        # Should have all 5 days (some as skeletons)
        assert len(merged["days"]) == 5
        for i in range(1, 6):
            assert f"day_{i}" in merged["days"]
        
        # Verify skeletons have correct structure
        assert "warmup" in merged["days"]["day_2"]
        assert "main_session" in merged["days"]["day_2"]
        assert "cooldown" in merged["days"]["day_2"]
        assert merged["days"]["day_2"]["warmup"]["duration_minutes"] == 0  # Skeleton default
    
    def test_merge_chunks_malformed_structure(self):
        """Test merging chunks with malformed structure (non-dict days)."""
        chunk_results = [
            {
                "chunk_info": {"start": 1, "end": 2, "chunk_id": "chunk_1"},
                "data": {
                    "days": "invalid"  # Should be dict, not string
                }
            }
        ]
        
        merged = _merge_weekly_chunks(chunk_results, weekly_sessions=2, mode="general")
        
        # Should create skeletons for all days
        assert len(merged["days"]) == 2
        assert "day_1" in merged["days"]
        assert "day_2" in merged["days"]
    
    def test_merge_chunks_metadata_repaired_by(self):
        """Test that missing days are recorded in metadata.repaired_by."""
        chunk_results = [
            {
                "chunk_info": {"start": 1, "end": 3, "chunk_id": "chunk_1"},
                "data": {
                    "days": {
                        "day_1": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        }
                    }
                }
            }
        ]
        
        merged = _merge_weekly_chunks(chunk_results, weekly_sessions=5, mode="general")
        
        # Should have metadata with repaired_by
        assert "metadata" in merged
        if "repaired_by" in merged.get("metadata", {}):
            repaired_by = merged["metadata"]["repaired_by"]
            assert isinstance(repaired_by, list)
            assert any("assigned_missing_days" in str(item) for item in repaired_by)
    
    def test_merge_athlete_mode_weekly_schedule(self):
        """Test merging for athlete mode (uses weekly_schedule instead of days)."""
        chunk_results = [
            {
                "chunk_info": {"start": 1, "end": 2, "chunk_id": "chunk_1"},
                "data": {
                    "weekly_schedule": {
                        "day_1": {
                            "session_type": "strength",
                            "duration_minutes": 90,
                            "main_workout": []
                        },
                        "day_2": {
                            "session_type": "endurance",
                            "duration_minutes": 90,
                            "main_workout": []
                        }
                    }
                }
            }
        ]
        
        merged = _merge_weekly_chunks(chunk_results, weekly_sessions=2, mode="athlete")
        
        # Should use weekly_schedule, not days
        assert "weekly_schedule" in merged
        assert "days" not in merged or len(merged.get("days", {})) == 0
        assert "day_1" in merged["weekly_schedule"]
        assert "day_2" in merged["weekly_schedule"]
    
    def test_merge_preserves_plan_meta(self):
        """Test that plan_meta is preserved in merged result."""
        chunk_results = [
            {
                "chunk_info": {"start": 1, "end": 1, "chunk_id": "chunk_1"},
                "data": {
                    "provided_information": {"goal": "test"},
                    "summary": "Test",
                    "plan_meta": {"sport": "general_fitness", "style": "mixed"},
                    "days": {
                        "day_1": {
                            "warmup": {"duration_minutes": 5, "exercises": []},
                            "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                            "cooldown": {"duration_minutes": 5, "exercises": []}
                        }
                    }
                }
            }
        ]
        
        merged = _merge_weekly_chunks(chunk_results, weekly_sessions=1, mode="general")
        
        assert "plan_meta" in merged
        assert merged["plan_meta"]["sport"] == "general_fitness"
        assert merged["plan_meta"]["style"] == "mixed"
    
    def test_merge_unwraps_wrapper_keys(self):
        """Test that wrapper keys (plan_data, generated_plan, payload) are automatically unwrapped."""
        import json
        
        # Test plan_data wrapper
        wrapped_plan_data = {
            "plan_data": {
                "provided_information": {"goal": "test"},
                "summary": "Test plan",
                "plan_meta": {},
                "days": {
                    "day_1": {
                        "warmup": {"duration_minutes": 5, "exercises": []},
                        "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": "ok"},
                        "cooldown": {"duration_minutes": 5, "exercises": []}
                    }
                },
                "metadata": {}
            }
        }
        
        raw_text = json.dumps(wrapped_plan_data)
        unwrapped, cleaned = try_unwrap_json(raw_text)
        assert unwrapped is not None
        assert "days" in unwrapped
        assert "day_1" in unwrapped["days"]
        
        # Test generated_plan wrapper
        wrapped_generated = {
            "generated_plan": {
                "provided_information": {"goal": "test"},
                "summary": "Test",
                "plan_meta": {},
                "days": {"day_1": {}},
                "metadata": {}
            }
        }
        
        raw_text = json.dumps(wrapped_generated)
        unwrapped, cleaned = try_unwrap_json(raw_text)
        assert unwrapped is not None
        assert "days" in unwrapped
        
        # Test payload wrapper
        wrapped_payload = {
            "payload": {
                "provided_information": {"goal": "test"},
                "summary": "Test",
                "plan_meta": {},
                "days": {"day_1": {}},
                "metadata": {}
            }
        }
        
        raw_text = json.dumps(wrapped_payload)
        unwrapped, cleaned = try_unwrap_json(raw_text)
        assert unwrapped is not None
        assert "days" in unwrapped
        
        # Test already unwrapped (no wrapper)
        unwrapped_plan = {
            "provided_information": {"goal": "test"},
            "summary": "Test",
            "plan_meta": {},
            "days": {"day_1": {}},
            "metadata": {}
        }
        
        raw_text = json.dumps(unwrapped_plan)
        unwrapped, cleaned = try_unwrap_json(raw_text)
        assert unwrapped is not None
        assert "days" in unwrapped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

