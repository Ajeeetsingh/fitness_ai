"""Tests for high-level block autofill."""
import pytest
from app.fitness.workout_plan.pipeline_utils import fill_high_level_blocks


class TestHighLevelBlockAutofill:
    """Test high-level block autofill functionality."""
    
    def test_phase_objectives_autofilled(self):
        """Missing phase_objectives should be autofilled"""
        provided_info = {
            "sport": "marathon",
            "phase": "build",
            "focus": "endurance",
            "weekly_sessions": 5
        }
        
        blocks = fill_high_level_blocks(provided_info)
        assert "phase_objectives" in blocks
        assert isinstance(blocks["phase_objectives"], list)
        assert len(blocks["phase_objectives"]) >= 3
    
    def test_microcycle_overview_autofilled(self):
        """Missing microcycle_overview should be autofilled"""
        provided_info = {
            "sport": "marathon",
            "phase": "build",
            "weekly_sessions": 5
        }
        
        blocks = fill_high_level_blocks(provided_info)
        assert "microcycle_overview" in blocks
        assert isinstance(blocks["microcycle_overview"], str)
        assert "5" in blocks["microcycle_overview"]  # Should mention weekly_sessions
    
    def test_strength_conditioning_autofilled(self):
        """Missing strength_conditioning should be autofilled"""
        provided_info = {
            "sport": "marathon",
            "phase": "build"
        }
        
        blocks = fill_high_level_blocks(provided_info)
        assert "strength_conditioning" in blocks
        assert isinstance(blocks["strength_conditioning"], str)
    
    def test_mobility_prehab_autofilled(self):
        """Missing mobility_prehab should be autofilled"""
        provided_info = {
            "sport": "marathon",
            "phase": "build"
        }
        
        blocks = fill_high_level_blocks(provided_info)
        assert "mobility_prehab" in blocks
        assert isinstance(blocks["mobility_prehab"], list)
        assert len(blocks["mobility_prehab"]) >= 2
    
    def test_recovery_nutrition_autofilled(self):
        """Missing recovery_nutrition should be autofilled"""
        provided_info = {
            "sport": "marathon",
            "phase": "build"
        }
        
        blocks = fill_high_level_blocks(provided_info)
        assert "recovery_nutrition" in blocks
        assert isinstance(blocks["recovery_nutrition"], list)
        assert len(blocks["recovery_nutrition"]) >= 2
    
    def test_safety_notes_autofilled(self):
        """Missing safety_notes should be autofilled"""
        provided_info = {
            "sport": "marathon",
            "phase": "build"
        }
        
        blocks = fill_high_level_blocks(provided_info)
        assert "safety_notes" in blocks
        assert isinstance(blocks["safety_notes"], list)
        assert len(blocks["safety_notes"]) >= 2
    
    def test_sport_specific_content(self):
        """Autofilled content should be sport-specific"""
        provided_info = {
            "sport": "sprinter",
            "phase": "build",
            "weekly_sessions": 4
        }
        
        blocks = fill_high_level_blocks(provided_info)
        # microcycle_overview should mention sprinter
        assert "sprinter" in blocks["microcycle_overview"].lower() or "sprint" in blocks["microcycle_overview"].lower()
        # strength_conditioning should mention sprinter
        assert "sprinter" in blocks["strength_conditioning"].lower() or "sprint" in blocks["strength_conditioning"].lower()
    
    def test_phase_specific_content(self):
        """Autofilled content should be phase-specific"""
        provided_info = {
            "sport": "marathon",
            "phase": "taper",
            "weekly_sessions": 5
        }
        
        blocks = fill_high_level_blocks(provided_info)
        # phase_objectives should mention taper
        phase_obj_str = " ".join(blocks["phase_objectives"]).lower()
        assert "taper" in phase_obj_str

