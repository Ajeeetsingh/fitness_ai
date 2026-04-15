"""Tests for sport and style normalization."""
import pytest
from app.fitness.workout_plan.schemas import AthletePlanRequest


class TestSportNormalization:
    """Test sport field normalization."""
    
    def test_marathon_running_normalizes_to_marathon(self):
        """marathon_running should normalize to marathon"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="marathon_running",
            phase="build",
            weekly_sessions=5,
            minutes=60,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="performance"
        )
        assert req.sport.value == "marathon"
    
    def test_sprinting_100m_200m_normalizes_to_sprinter(self):
        """sprinting_100m_200m should normalize to sprinter"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="sprinting_100m_200m",
            phase="build",
            weekly_sessions=4,
            minutes=70,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="performance"
        )
        assert req.sport.value == "sprinter"
    
    def test_football_normalizes_to_soccer(self):
        """football should normalize to soccer"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="football",
            phase="build",
            weekly_sessions=5,
            minutes=60,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="performance"
        )
        assert req.sport.value == "soccer"
    
    def test_batminton_normalizes_to_generic(self):
        """batminton (typo) should normalize to generic"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="batminton",
            phase="build",
            weekly_sessions=4,
            minutes=50,
            experience="intermediate",
            plan_type="weekly",
            equipment="gym",
            style="mixed"
        )
        assert req.sport.value == "generic"
    
    def test_swim_normalizes_to_generic(self):
        """swim should normalize to generic"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="swim",
            phase="build",
            weekly_sessions=5,
            minutes=60,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="performance"
        )
        assert req.sport.value == "generic"
    
    def test_100m_normalizes_to_sprinter(self):
        """100m should normalize to sprinter"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="100m",
            phase="build",
            weekly_sessions=4,
            minutes=70,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="performance"
        )
        assert req.sport.value == "sprinter"


class TestStyleNormalization:
    """Test style field normalization."""
    
    def test_endurance_focused_normalizes_to_performance(self):
        """endurance_focused should normalize to performance"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="marathon",
            phase="build",
            weekly_sessions=5,
            minutes=60,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="endurance_focused"
        )
        assert req.style == "performance"
    
    def test_power_speed_normalizes_to_performance(self):
        """power_speed should normalize to performance"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="sprinter",
            phase="build",
            weekly_sessions=4,
            minutes=70,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="power_speed"
        )
        assert req.style == "performance"
    
    def test_cardio_focused_normalizes_to_performance(self):
        """cardio_focused should normalize to performance"""
        req = AthletePlanRequest(
            population="competitive_athlete",
            sport="runner_5k",
            phase="build",
            weekly_sessions=5,
            minutes=50,
            experience="intermediate",
            plan_type="weekly",
            equipment="gym",
            style="cardio_focused"
        )
        assert req.style == "performance"

