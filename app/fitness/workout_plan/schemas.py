import datetime
import re
from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field, conint, field_validator, model_validator


class PlanRequest(BaseModel):
    # Core fields: must be provided explicitly by the user (no defaults)
    goal: Optional[str] = None
    minutes: Optional[int] = None  # Will be normalized to minutes_per_session internally
    experience: Optional[str] = None  # beginner|intermediate|advanced
    equipment: Optional[str] = None  # bodyweight|dumbbells|gym
    style: Optional[str] = None  # hiit|strength|hypertrophy|yoga|mobility|mixed

    # Non-core / optional
    plan_type: Optional[str] = "weekly"  # daily|weekly|monthly|3months
    injuries: Optional[str] = None
    text: Optional[str] = None
    age: Optional[str] = None
    body_type: Optional[str] = None
    location: Optional[str] = None
    language: Optional[str] = "en"
    weekly_sessions: Optional[int] = 5  # Phase 1: Default to 5 (was None)
    sport: Optional[str] = "general_fitness"  # Phase 1: Add sport field with default
    strict: Optional[bool] = False  # Phase 1: If true, don't auto-fill missing content, return needs_manual_review
    
    def model_post_init(self, __context):
        """Normalize minutes to minutes_per_session after model initialization."""
        # This will be handled in service layer normalization
        pass


class Sport(str, Enum):
    # Endurance
    runner_5k = "runner_5k"
    runner_10k = "runner_10k"
    marathon = "marathon"
    triathlon = "triathlon"
    cyclist = "cyclist"
    # Field/court
    soccer = "soccer"
    basketball = "basketball"
    tennis = "tennis"
    # Combat
    boxer = "boxer"
    mma = "mma"
    # Strength/combo
    powerlifting = "powerlifting"
    weightlifting = "weightlifting"
    bodybuilding = "bodybuilding"
    crossfit = "crossfit"
    # Performance arts
    gymnastics = "gymnastics"
    dance = "dance"
    performance_arts = "performance_arts"
    # Other
    sprinter = "sprinter"
    hybrid = "hybrid"
    generic = "generic"


class Phase(str, Enum):
    off_season = "off_season"
    base = "base"
    build = "build"
    intensification = "intensification"
    pre_competition = "pre_competition"
    competition = "competition"
    peak = "peak"  # Keep for backward compatibility
    in_season = "in_season"  # Keep for backward compatibility
    taper = "taper"
    transition = "transition"
    deload = "deload"  # Keep for backward compatibility


class IntensityMethod(str, Enum):
    """Canonical intensity measurement methods."""
    RPE = "RPE"                    # Rate of Perceived Exertion (1-10)
    RIR = "RIR"                    # Reps in Reserve (0-5)
    PERCENT_1RM = "PERCENT_1RM"    # Percentage of 1RM (0-100)
    PERCENT_FTP = "PERCENT_FTP"    # Percentage of Functional Threshold Power (0-200)
    POWER_WATTS = "POWER_WATTS"    # Absolute power in watts
    PACE_PER_KM = "PACE_PER_KM"    # Running pace (seconds per km)
    PACE_PER_MILE = "PACE_PER_MILE" # Running pace (seconds per mile)
    HR_PERCENT_MAX = "HR_PERCENT_MAX" # Heart rate % of max (0-100)
    HR_ZONE = "HR_ZONE"            # Heart rate zone (1-5)


class SessionType(str, Enum):
    """Canonical session type values."""
    # General
    REST = "rest"
    ACTIVE_RECOVERY = "active_recovery"
    STRENGTH = "strength"
    POWER = "power"
    HYPERTROPHY = "hypertrophy"
    CONDITIONING = "conditioning"
    CARDIO = "cardio"
    MOBILITY = "mobility"
    FLEXIBILITY = "flexibility"
    
    # Running
    EASY_RUN = "easy_run"
    TEMPO = "tempo"
    INTERVALS = "intervals"
    LONG_RUN = "long_run"
    RECOVERY_RUN = "recovery_run"
    
    # Sprinting
    MAX_VELOCITY = "max_velocity"
    ACCELERATION = "acceleration"
    PLYOMETRICS = "plyometrics"
    SPEED_ENDURANCE = "speed_endurance"
    
    # Cycling
    BIKE_THRESHOLD = "bike_threshold"
    BIKE_INTERVALS = "bike_intervals"
    BIKE_ENDURANCE = "bike_endurance"
    
    # Swimming
    SWIM_SPEED = "swim_speed"
    SWIM_THRESHOLD = "swim_threshold"
    SWIM_ENDURANCE = "swim_endurance"
    
    # Strength sports
    SQUAT_FOCUS = "squat_focus"
    BENCH_FOCUS = "bench_focus"
    DEADLIFT_FOCUS = "deadlift_focus"
    SNATCH_FOCUS = "snatch_focus"
    CLEAN_JERK_FOCUS = "clean_jerk_focus"
    
    # Sport-specific
    SKILL = "skill"
    ANAEROBIC_INTERVALS = "anaerobic_intervals"
    MAIN_WOD = "main_wod"
    METCON = "metcon"
    ACCESSORY = "accessory"
    LIGHT_STRENGTH = "light_strength"
    DRYLAND_STRENGTH = "dryland_strength"


class AthletePlanRequest(BaseModel):
    population: Literal["competitive_athlete", "serious_trainee"] = Field(
        default="competitive_athlete",
        description=(
            "User type for plan customization: "
            "'competitive_athlete' = Competing athletes needing periodized, competition-focused training with higher intensity. "
            "'serious_trainee' = Fitness enthusiasts wanting structured, progressive training without competition focus."
        )
    )
    sport: Sport = Sport.generic
    phase: Phase = Phase.build
    weekly_sessions: Optional[conint(ge=1, le=12)] = 5
    competition_date: Optional[str] = None  # YYYY-MM-DD
    focus: Optional[str] = None

    # inherit general knobs (kept defaulted for athlete path)
    text: Optional[str] = None
    goal: Optional[str] = None
    minutes: conint(ge=20, le=120) = 60  # Will be normalized to minutes_per_session internally
    experience: Literal["beginner", "intermediate", "advanced", "elite"] = "advanced"
    plan_type: Literal["daily", "weekly", "monthly", "3months"] = "weekly"
    equipment: Literal["bodyweight", "dumbbells", "gym"] = "gym"
    style: Literal["hiit", "strength", "hypertrophy", "yoga", "mobility", "mixed", "performance"] = "mixed"
    injuries: Optional[str] = None
    age: Optional[str] = "25"
    body_type: Optional[str] = "athletic"
    location: Optional[str] = "gym"
    language: Literal["en", "hi", "ta", "te", "bn", "mr", "pa", "gu", "kn", "ml"] = "en"

    @field_validator("population", mode="before")
    @classmethod
    def _normalize_population(cls, v):
        """Normalize population field - map old values to new values for backward compatibility."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Map old values to new values for backward compatibility
        population_mapping = {
            "athlete": "competitive_athlete",
            "competitive_athlete": "competitive_athlete",
            "competitive": "competitive_athlete",
            "competing": "competitive_athlete",
            "pro": "competitive_athlete",
            "professional": "competitive_athlete",
            "enthusiast": "serious_trainee",
            "serious_trainee": "serious_trainee",
            "serious": "serious_trainee",
            "trainee": "serious_trainee",
            "fitness_enthusiast": "serious_trainee",
            "fitness enthusiast": "serious_trainee",
            "recreational": "serious_trainee",
            "recreational_athlete": "serious_trainee",
        }
        return population_mapping.get(v_lower, v)

    @field_validator("sport", mode="before")
    @classmethod
    def _normalize_sport(cls, v):
        """Normalize common sport name variations."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Normalize underscores and spaces
        v_lower = re.sub(r'[_\s]+', ' ', v_lower)
        
        # Map common variations to valid sport values
        sport_mapping = {
            # Runners
            "marathon_running": "marathon",
            "marathon running": "marathon",
            "marathoner": "marathon",
            "long distance running": "marathon",
            "long distance": "marathon",
            "5k": "runner_5k",
            "5km": "runner_5k",
            "10k": "runner_10k",
            "10km": "runner_10k",
            "sprinting_100m_200m": "sprinter",
            "sprinting 100m 200m": "sprinter",
            "sprinting": "sprinter",
            "100m": "sprinter",
            "200m": "sprinter",
            "100m 200m": "sprinter",
            "short distance": "sprinter",
            "runner": "runner_5k",
            "running": "runner_5k",
            # Common sports
            "football": "soccer",
            "soccer": "soccer",
            "footy": "soccer",
            "futbol": "soccer",
            "basketball": "basketball",
            "bball": "basketball",
            "tennis": "tennis",
            "cricket": "generic",
            "table tennis": "generic",
            "ping pong": "generic",
            "badminton": "generic",
            "batminton": "generic",  # Common typo
            "swim": "generic",
            "swimming": "generic",
            "gym": "generic",
            # Strength & others
            "crossfit": "crossfit",
            "cf": "crossfit",
            "powerlifting": "powerlifting",
            "powerlift": "powerlifting",
            "weightlifting": "weightlifting",
            "olympic": "weightlifting",
            "bodybuilding": "bodybuilding",
            "bodybuild": "bodybuilding",
            "mma": "mma",
            "ufc": "mma",
            "boxing": "boxer",
            "boxer": "boxer",
            "gymnastics": "gymnastics",
            "gymnast": "gymnastics",
            "dance": "dance",
            "dancing": "dance",
            "performance": "performance_arts",
            "arts": "performance_arts",
            "sprinter": "sprinter",
            "sprint": "sprinter",
            "triathlon": "triathlon",
            "tri": "triathlon",
            "cycling": "cyclist",
            "bike": "cyclist",
            "rugby": "generic",
            "hockey": "generic",
            "volleyball": "generic",
            "hybrid": "hybrid",
            "generic": "generic",
        }
        
        # Exact match first
        if v_lower in sport_mapping:
            return sport_mapping[v_lower]
        
        # Substring match (check if any key is contained in the input)
        for key in sport_mapping:
            if key in v_lower:
                return sport_mapping[key]
        
        return v  # Allow Pydantic to fail after this if still invalid

    @field_validator("equipment", mode="before")
    @classmethod
    def _normalize_equipment(cls, v):
        """Normalize common equipment name variations."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Map common variations to valid equipment values
        equipment_mapping = {
            "bodyweight": "bodyweight",
            "body weight": "bodyweight",
            "no equipment": "bodyweight",
            "none": "bodyweight",
            "dumbbells": "dumbbells",
            "dumbbell": "dumbbells",
            "db": "dumbbells",
            "dbs": "dumbbells",
            "gym": "gym",
            "gymnasium": "gym",
            "fitness center": "gym",
            "fitness centre": "gym",
            "stadium": "gym",  # Stadium typically has gym equipment
            "field": "gym",  # Field training often uses gym equipment
            "ground": "gym",
            "ground field": "gym",
        }
        return equipment_mapping.get(v_lower, v)

    @field_validator("language", mode="before")
    @classmethod
    def _normalize_language(cls, v):
        """Normalize common language name variations to language codes."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Map common language names to language codes
        language_mapping = {
            "english": "en",
            "en": "en",
            "hindi": "hi",
            "hi": "hi",
            "tamil": "ta",
            "ta": "ta",
            "telugu": "te",
            "te": "te",
            "bengali": "bn",
            "bn": "bn",
            "bangla": "bn",
            "marathi": "mr",
            "mr": "mr",
            "punjabi": "pa",
            "pa": "pa",
            "gujarati": "gu",
            "gu": "gu",
            "kannada": "kn",
            "kn": "kn",
            "malayalam": "ml",
            "ml": "ml",
        }
        return language_mapping.get(v_lower, v)

    @field_validator("competition_date", mode="before")
    @classmethod
    def _normalize_competition_date(cls, v):
        """Normalize competition date - handle 'string' placeholder and validate format."""
        if not v:
            return None
        if not isinstance(v, str):
            return v
        v = v.strip()
        # If user sends placeholder "string", return None
        if v.lower() in ["string", "none", "null", ""]:
            return None
        # Validate date format
        try:
            datetime.datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            # Try to provide helpful error message
            raise ValueError(
                f"competition_date must be in format 'YYYY-MM-DD' (e.g., '2025-12-15') or null. "
                f"Received: '{v}'"
            )

    @field_validator("competition_date")
    @classmethod
    def _date_format(cls, v):
        """Validate competition date format."""
        if v:
            datetime.datetime.strptime(v, "%Y-%m-%d")
        return v

    @field_validator("phase", mode="before")
    @classmethod
    def _normalize_phase(cls, v):
        """Normalize phase field - map common variations to valid enum values."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Map common variations to valid phase values
        phase_mapping = {
            # New phase values
            "intensification": "intensification",
            "intensity": "intensification",
            "intense": "intensification",
            "intensify": "intensification",
            "pre-competition": "pre_competition",
            "pre_competition": "pre_competition",
            "pre competition": "pre_competition",
            "precomp": "pre_competition",
            "competition": "competition",
            "comp": "competition",
            "transition": "transition",
            "trans": "transition",
            # Existing phase values (keep for backward compatibility)
            "off_season": "off_season",
            "offseason": "off_season",
            "off season": "off_season",
            "base": "base",
            "base phase": "base",
            "foundation": "base",
            "build": "build",
            "building": "build",
            "build phase": "build",
            "peak": "peak",
            "peaking": "peak",
            "peak phase": "peak",
            "in_season": "in_season",
            "in season": "in_season",
            "season": "in_season",
            "taper": "taper",
            "tapering": "taper",
            "taper phase": "taper",
            "deload": "deload",
            "deload week": "deload",
            "recovery week": "deload",
        }
        return phase_mapping.get(v_lower, v)

    @field_validator("style", mode="before")
    @classmethod
    def _normalize_style(cls, v):
        """Normalize style field - map common variations to valid enum values."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Normalize underscores and spaces
        v_lower = re.sub(r'[_\s]+', ' ', v_lower)
        
        # Map common variations to valid style values
        style_mapping = {
            "strength_hypertrophy": "mixed",
            "strength and hypertrophy": "mixed",
            "strength+hypertrophy": "mixed",
            "strength & hypertrophy": "mixed",
            "strength/hypertrophy": "mixed",
            "strength-hypertrophy": "mixed",
            "hiit": "hiit",
            "high intensity interval training": "hiit",
            "high intensity": "hiit",
            "strength": "strength",
            "strength training": "strength",
            "hypertrophy": "hypertrophy",
            "muscle building": "hypertrophy",
            "muscle growth": "hypertrophy",
            "yoga": "yoga",
            "mobility": "mobility",
            "flexibility": "mobility",
            "mixed": "mixed",
            "combination": "mixed",
            "combo": "mixed",
            "performance": "performance",
            "performance training": "performance",
            "athletic performance": "performance",
            "athletic": "performance",
            "sport performance": "performance",
            "sports performance": "performance",
            "endurance_focused": "performance",
            "endurance focused": "performance",
            "endurance": "performance",
            "endurance training": "performance",
            "cardio focused": "performance",
            "cardio_focused": "performance",
            "cardio": "performance",
            "power_speed": "performance",
            "power speed": "performance",
            "power": "performance",
            "speed": "performance",
            "speed training": "performance",
            "power training": "performance",
            "explosive": "performance",
            "explosive power": "performance",
            "plyometrics": "performance",
            "plyometric": "performance",
        }
        
        # Exact match first
        if v_lower in style_mapping:
            return style_mapping[v_lower]
        
        # Substring match
        for key in style_mapping:
            if key in v_lower:
                return style_mapping[key]
        
        return v

    @field_validator("experience", mode="before")
    @classmethod
    def _normalize_experience(cls, v):
        """Normalize experience field - map common variations to valid enum values."""
        if not v or not isinstance(v, str):
            return v
        v_lower = v.lower().strip()
        # Map common variations to valid experience values
        experience_mapping = {
            "beginner": "beginner",
            "beginning": "beginner",
            "novice": "beginner",
            "new": "beginner",
            "starter": "beginner",
            "intermediate": "intermediate",
            "inter": "intermediate",
            "moderate": "intermediate",
            "advanced": "advanced",
            "adv": "advanced",
            "experienced": "advanced",
            "elite": "elite",
            "expert": "elite",
            "professional": "elite",
            "pro": "elite",
            "world-class": "elite",
            "world class": "elite",
            "top-level": "elite",
            "top level": "elite",
        }
        return experience_mapping.get(v_lower, v)

    @model_validator(mode="after")
    def _validate_weekly_sessions_for_plan_type(self):
        """Validate weekly_sessions based on plan_type: allow 1 for daily, require >= 2 for others."""
        plan_type = self.plan_type
        weekly_sessions = self.weekly_sessions
        
        if plan_type != "daily":
            if weekly_sessions is None or weekly_sessions < 2:
                raise ValueError("weekly_sessions must be >= 2 for weekly, monthly, or 3months plans")
        
        return self
