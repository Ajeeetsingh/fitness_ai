"""
Exercise Database for Workout Plan Generation
This database contains predefined exercises with all fields hardcoded.
The LLM will select from this list instead of generating exercises freely.
"""

# Exercise categories
EXERCISE_DATABASE = {
    "warmup": [
        {
            "id": "warmup_001",
            "name": "Jumping Jacks",
            "sets": [1, 2],
            "reps": [10, 15, 20, 30],
            "work_seconds": [30, 45, 60],
            "rest_seconds": [10, 15, 30],
            "RPE_RIR": ["Light", "Very Light"]
        },
        {
            "id": "warmup_002",
            "name": "Arm Circles",
            "sets": [1, 2],
            "reps": [10, 15, 20],
            "work_seconds": None,
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Light", "Very Light"]
        },
        {
            "id": "warmup_003",
            "name": "Leg Swings",
            "sets": [1, 2],
            "reps": [10, 15, 20],
            "work_seconds": None,
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Light"]
        },
        {
            "id": "warmup_004",
            "name": "High Knees",
            "sets": [1, 2],
            "reps": [15, 20, 30],
            "work_seconds": [30, 45],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Light"]
        },
        {
            "id": "warmup_005",
            "name": "Shoulder Rotations",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": None,
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Very Light"]
        },
        {
            "id": "warmup_006",
            "name": "Neck Rolls",
            "sets": [1, 2],
            "reps": [8, 10],
            "work_seconds": None,
            "rest_seconds": [10],
            "RPE_RIR": ["Very Light"]
        },
        {
            "id": "warmup_007",
            "name": "Torso Twists",
            "sets": [1, 2],
            "reps": [10, 15, 20],
            "work_seconds": None,
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Light"]
        },
        {
            "id": "warmup_008",
            "name": "Marching in Place",
            "sets": [1, 2],
            "reps": [20, 30],
            "work_seconds": [30, 45],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Very Light"]
        }
    ],
    "main_session": [
        # Upper Body - Dumbbell
        {
            "id": "main_001",
            "name": "Dumbbell Bicep Curls",
            "sets": [2, 3, 4],
            "reps": [8, 10, 12, 15],
            "work_seconds": None,
            "rest_seconds": [30, 45, 60],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7", "8"]
        },
        {
            "id": "main_002",
            "name": "Dumbbell Shoulder Press",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45, 60],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_003",
            "name": "Dumbbell Rows",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_004",
            "name": "Dumbbell Tricep Extensions",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "7"]
        },
        {
            "id": "main_005",
            "name": "Dumbbell Lateral Raises",
            "sets": [2, 3],
            "reps": [10, 12, 15],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6", "7"]
        },
        {
            "id": "main_006",
            "name": "Dumbbell Front Raises",
            "sets": [2, 3],
            "reps": [10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6"]
        },
        # Lower Body - Dumbbell
        {
            "id": "main_007",
            "name": "Dumbbell Squats",
            "sets": [2, 3, 4],
            "reps": [8, 10, 12, 15],
            "work_seconds": [30, 45, 60],
            "rest_seconds": [30, 45, 60],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7", "8"]
        },
        {
            "id": "main_008",
            "name": "Dumbbell Goblet Squats",
            "sets": [2, 3],
            "reps": [10, 12, 15],
            "work_seconds": [30, 45],
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_009",
            "name": "Dumbbell Lunges",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45, 60],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_010",
            "name": "Dumbbell Romanian Deadlifts",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_011",
            "name": "Dumbbell Calf Raises",
            "sets": [2, 3],
            "reps": [12, 15, 20],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6"]
        },
        # Bodyweight
        {
            "id": "main_012",
            "name": "Push-Ups",
            "sets": [2, 3],
            "reps": [8, 10, 12, 15],
            "work_seconds": None,
            "rest_seconds": [30, 45, 60],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7", "8"]
        },
        {
            "id": "main_013",
            "name": "Push-Ups (Knee Supported)",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6", "7"]
        },
        {
            "id": "main_014",
            "name": "Plank Hold",
            "sets": [2, 3],
            "reps": None,
            "work_seconds": [20, 30, 45, 60],
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_015",
            "name": "Bodyweight Squats",
            "sets": [2, 3, 4],
            "reps": [10, 12, 15, 20],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6", "7"]
        },
        {
            "id": "main_016",
            "name": "Lunges",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_017",
            "name": "Mountain Climbers",
            "sets": [2, 3],
            "reps": [10, 15, 20],
            "work_seconds": [30, 45],
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "7"]
        },
        {
            "id": "main_018",
            "name": "Burpees",
            "sets": [2, 3],
            "reps": [5, 8, 10],
            "work_seconds": None,
            "rest_seconds": [45, 60],
            "RPE_RIR": ["Challenging", "Hard", "7", "8"]
        },
        # Core
        {
            "id": "main_019",
            "name": "Crunches",
            "sets": [2, 3],
            "reps": [10, 15, 20],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6"]
        },
        {
            "id": "main_020",
            "name": "Russian Twists",
            "sets": [2, 3],
            "reps": [10, 15, 20],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6"]
        },
        {
            "id": "main_021",
            "name": "Leg Raises",
            "sets": [2, 3],
            "reps": [8, 10, 12],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "Challenging", "6", "7"]
        },
        {
            "id": "main_022",
            "name": "Bicycle Crunches",
            "sets": [2, 3],
            "reps": [10, 15, 20],
            "work_seconds": None,
            "rest_seconds": [30, 45],
            "RPE_RIR": ["Moderate", "6"]
        }
    ],
    "cooldown": [
        {
            "id": "cooldown_001",
            "name": "Shoulder Stretch",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": [20, 30],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Relaxing", "Gentle"]
        },
        {
            "id": "cooldown_002",
            "name": "Hamstring Stretch",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": [20, 30],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Gentle", "Relaxing"]
        },
        {
            "id": "cooldown_003",
            "name": "Quad Stretch",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": [20, 30],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Gentle"]
        },
        {
            "id": "cooldown_004",
            "name": "Chest Opener Stretch",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": [20, 30],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Relaxing"]
        },
        {
            "id": "cooldown_005",
            "name": "Triceps Stretch",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": [20, 30],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Gentle"]
        },
        {
            "id": "cooldown_006",
            "name": "Child's Pose",
            "sets": [1, 2],
            "reps": None,
            "work_seconds": [30, 45, 60],
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Relaxing", "Gentle"]
        },
        {
            "id": "cooldown_007",
            "name": "Deep Breathing",
            "sets": [1, 2],
            "reps": None,
            "work_seconds": [30, 45, 60],
            "rest_seconds": [10],
            "RPE_RIR": ["Relaxing"]
        },
        {
            "id": "cooldown_008",
            "name": "Cat-Cow Stretch",
            "sets": [1, 2],
            "reps": [10, 15],
            "work_seconds": None,
            "rest_seconds": [10, 15],
            "RPE_RIR": ["Gentle", "Relaxing"]
        }
    ]
}


def get_exercises_by_category(category: str) -> list:
    """Get all exercises for a specific category (warmup, main_session, cooldown)"""
    return EXERCISE_DATABASE.get(category, [])


def get_exercise_by_id(exercise_id: str) -> dict:
    """Get a specific exercise by its ID"""
    for category in EXERCISE_DATABASE.values():
        for exercise in category:
            if exercise["id"] == exercise_id:
                return exercise
    return None


def format_exercises_for_prompt(category: str, equipment_filter: str = None) -> str:
    """
    Format exercises for inclusion in LLM prompt.
    Returns a formatted string listing all available exercises with their options.
    """
    exercises = get_exercises_by_category(category)
    
    # Filter by equipment if specified
    if equipment_filter:
        equipment_lower = equipment_filter.lower()
        if "dumbbell" in equipment_lower or "weight" in equipment_lower:
            # Include both dumbbell and bodyweight exercises
            filtered = [e for e in exercises if "Dumbbell" in e["name"] or "Bodyweight" in e["name"] or "Push" in e["name"] or "Plank" in e["name"] or "Squat" in e["name"] or "Lunge" in e["name"] or "Crunches" in e["name"] or "Russian" in e["name"] or "Leg" in e["name"] or "Mountain" in e["name"] or "Burpee" in e["name"]]
        elif "bodyweight" in equipment_lower or "no equipment" in equipment_lower:
            # Only bodyweight exercises
            filtered = [e for e in exercises if "Dumbbell" not in e["name"]]
        else:
            filtered = exercises
    else:
        filtered = exercises
    
    if not filtered:
        return f"No exercises available for category: {category}"
    
    formatted = []
    for exercise in filtered:
        parts = [f"ID: {exercise['id']}", f"Name: {exercise['name']}"]
        
        if exercise.get("sets"):
            parts.append(f"Sets options: {exercise['sets']}")
        if exercise.get("reps"):
            parts.append(f"Reps options: {exercise['reps']}")
        if exercise.get("work_seconds"):
            parts.append(f"Work seconds options: {exercise['work_seconds']}")
        if exercise.get("rest_seconds"):
            parts.append(f"Rest seconds options: {exercise['rest_seconds']}")
        if exercise.get("RPE_RIR"):
            parts.append(f"RPE/RIR options: {exercise['RPE_RIR']}")
        
        formatted.append(" | ".join(parts))
    
    return "\n".join(formatted)

