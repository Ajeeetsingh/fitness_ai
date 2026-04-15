"""
Template-based JSON generation - LLM fills values into pre-structured JSON templates
This ensures the JSON structure is always correct.

DEPRECATED FOR V2 ENDPOINT:
This module is used only by the old /plans/generate endpoint (service.py).
The new v2 endpoint (/v2/plans/generate) uses per-day generation without templates.
This module is kept for backward compatibility with the old endpoint.
"""

import json
import re
import logging
from typing import Dict, Any, List, Optional
from app.fitness.workout_plan.exercise_database import get_exercises_by_category, get_exercise_by_id

logger = logging.getLogger(__name__)


def build_json_template_for_chunk(req, chunk_info: dict) -> str:
    """
    Build a complete JSON template with placeholders for a chunk.
    Returns the template as a string with placeholders like {{DAY_1_WARMUP_EXERCISE_1_ID}}
    """
    pt = (req.plan_type or "weekly").lower()
    minutes = int(req.minutes)
    
    if pt == "weekly":
        chunk_type = chunk_info.get("type", "")
        
        if chunk_type == "days_1_2":
            template = {
                "provided_information": "{{PROVIDED_INFO}}",
                "summary": "{{SUMMARY}}",
                "days": {
                    "day_1": {
                        "warmup": {
                            "duration_minutes": "{{DAY_1_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_1_WARMUP_EX1_ID}}",
                                "name": "{{DAY_1_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_1_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_1_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_1_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_1_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_1_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_1_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_1_MAIN_EX1_ID}}",
                                "name": "{{DAY_1_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_1_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_1_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_1_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_1_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_1_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_1_MAIN_EX2_ID}}",
                                "name": "{{DAY_1_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_1_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_1_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_1_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_1_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_1_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_1_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_1_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_1_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_1_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_1_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_1_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_1_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_1_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_1_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                },
                    "day_2": {
                        "warmup": {
                            "duration_minutes": "{{DAY_2_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_2_WARMUP_EX1_ID}}",
                                "name": "{{DAY_2_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_2_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_2_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_2_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_2_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_2_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_2_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_2_MAIN_EX1_ID}}",
                                "name": "{{DAY_2_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_2_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_2_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_2_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_2_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_2_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_2_MAIN_EX2_ID}}",
                                "name": "{{DAY_2_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_2_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_2_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_2_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_2_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_2_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_2_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_2_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_2_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_2_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_2_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_2_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_2_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_2_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_2_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                }
                }
            }
        elif chunk_type == "days_3_4":
            template = {
                "days": {
                    "day_3": {
                        "warmup": {
                            "duration_minutes": "{{DAY_3_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_3_WARMUP_EX1_ID}}",
                                "name": "{{DAY_3_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_3_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_3_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_3_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_3_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_3_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_3_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_3_MAIN_EX1_ID}}",
                                "name": "{{DAY_3_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_3_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_3_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_3_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_3_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_3_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_3_MAIN_EX2_ID}}",
                                "name": "{{DAY_3_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_3_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_3_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_3_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_3_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_3_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_3_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_3_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_3_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_3_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_3_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_3_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_3_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_3_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_3_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                },
                    "day_4": {
                        "warmup": {
                            "duration_minutes": "{{DAY_4_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_4_WARMUP_EX1_ID}}",
                                "name": "{{DAY_4_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_4_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_4_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_4_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_4_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_4_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_4_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_4_MAIN_EX1_ID}}",
                                "name": "{{DAY_4_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_4_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_4_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_4_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_4_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_4_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_4_MAIN_EX2_ID}}",
                                "name": "{{DAY_4_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_4_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_4_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_4_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_4_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_4_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_4_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_4_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_4_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_4_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_4_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_4_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_4_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_4_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_4_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                }
                }
            }
        elif chunk_type == "days_5_7":
            template = {
                "days": {
                    "day_5": {
                        "warmup": {
                            "duration_minutes": "{{DAY_5_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_5_WARMUP_EX1_ID}}",
                                "name": "{{DAY_5_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_5_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_5_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_5_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_5_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_5_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_5_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_5_MAIN_EX1_ID}}",
                                "name": "{{DAY_5_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_5_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_5_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_5_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_5_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_5_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_5_MAIN_EX2_ID}}",
                                "name": "{{DAY_5_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_5_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_5_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_5_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_5_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_5_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_5_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_5_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_5_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_5_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_5_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_5_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_5_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_5_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_5_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                },
                    "day_6": {
                        "warmup": {
                            "duration_minutes": "{{DAY_6_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_6_WARMUP_EX1_ID}}",
                                "name": "{{DAY_6_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_6_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_6_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_6_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_6_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_6_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_6_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_6_MAIN_EX1_ID}}",
                                "name": "{{DAY_6_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_6_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_6_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_6_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_6_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_6_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_6_MAIN_EX2_ID}}",
                                "name": "{{DAY_6_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_6_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_6_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_6_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_6_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_6_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_6_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_6_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_6_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_6_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_6_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_6_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_6_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_6_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_6_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                },
                    "day_7": {
                        "warmup": {
                            "duration_minutes": "{{DAY_7_WARMUP_DURATION}}",
                            "exercises": [
                            {
                                "id": "{{DAY_7_WARMUP_EX1_ID}}",
                                "name": "{{DAY_7_WARMUP_EX1_NAME}}",
                                "sets": "{{DAY_7_WARMUP_EX1_SETS}}",
                                "reps": "{{DAY_7_WARMUP_EX1_REPS}}",
                                "work_seconds": "{{DAY_7_WARMUP_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_7_WARMUP_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_7_WARMUP_EX1_RPE}}"
                            }
                        ]
                    },
                    "main_session": {
                        "duration_minutes": "{{DAY_7_MAIN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_7_MAIN_EX1_ID}}",
                                "name": "{{DAY_7_MAIN_EX1_NAME}}",
                                "sets": "{{DAY_7_MAIN_EX1_SETS}}",
                                "reps": "{{DAY_7_MAIN_EX1_REPS}}",
                                "work_seconds": "{{DAY_7_MAIN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_7_MAIN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_7_MAIN_EX1_RPE}}"
                            },
                            {
                                "id": "{{DAY_7_MAIN_EX2_ID}}",
                                "name": "{{DAY_7_MAIN_EX2_NAME}}",
                                "sets": "{{DAY_7_MAIN_EX2_SETS}}",
                                "reps": "{{DAY_7_MAIN_EX2_REPS}}",
                                "work_seconds": "{{DAY_7_MAIN_EX2_WORK_SEC}}",
                                "rest_seconds": "{{DAY_7_MAIN_EX2_REST_SEC}}",
                                "RPE_RIR": "{{DAY_7_MAIN_EX2_RPE}}"
                            }
                        ],
                        "time_budget_check": "{{DAY_7_TIME_BUDGET}}"
                    },
                    "cooldown": {
                        "duration_minutes": "{{DAY_7_COOLDOWN_DURATION}}",
                        "exercises": [
                            {
                                "id": "{{DAY_7_COOLDOWN_EX1_ID}}",
                                "name": "{{DAY_7_COOLDOWN_EX1_NAME}}",
                                "sets": "{{DAY_7_COOLDOWN_EX1_SETS}}",
                                "reps": "{{DAY_7_COOLDOWN_EX1_REPS}}",
                                "work_seconds": "{{DAY_7_COOLDOWN_EX1_WORK_SEC}}",
                                "rest_seconds": "{{DAY_7_COOLDOWN_EX1_REST_SEC}}",
                                "RPE_RIR": "{{DAY_7_COOLDOWN_EX1_RPE}}"
                            }
                        ]
                    }
                }
                }
            }
        else:
            template = {}
    elif pt == "monthly":
        chunk_type = chunk_info.get("type", "")
        minutes = int(req.minutes)
        
        if chunk_type == "weeks_1_2":
            # Week 1: 3-5 days, Week 2: 3-5 days
            template = {
                "provided_information": "{{PROVIDED_INFO}}",
                "summary": "{{SUMMARY}}",
                "week_1": {
                    "days": [
                        {
                            "warmup": {
                                "duration_minutes": "{{W1_D1_WARMUP_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W1_D1_WARMUP_EX1_ID}}",
                                        "name": "{{W1_D1_WARMUP_EX1_NAME}}",
                                        "sets": "{{W1_D1_WARMUP_EX1_SETS}}",
                                        "reps": "{{W1_D1_WARMUP_EX1_REPS}}",
                                        "work_seconds": "{{W1_D1_WARMUP_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W1_D1_WARMUP_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W1_D1_WARMUP_EX1_RPE}}"
                                    }
                                ]
                            },
                            "main_session": {
                                "duration_minutes": "{{W1_D1_MAIN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W1_D1_MAIN_EX1_ID}}",
                                        "name": "{{W1_D1_MAIN_EX1_NAME}}",
                                        "sets": "{{W1_D1_MAIN_EX1_SETS}}",
                                        "reps": "{{W1_D1_MAIN_EX1_REPS}}",
                                        "work_seconds": "{{W1_D1_MAIN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W1_D1_MAIN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W1_D1_MAIN_EX1_RPE}}"
                                    },
                                    {
                                        "id": "{{W1_D1_MAIN_EX2_ID}}",
                                        "name": "{{W1_D1_MAIN_EX2_NAME}}",
                                        "sets": "{{W1_D1_MAIN_EX2_SETS}}",
                                        "reps": "{{W1_D1_MAIN_EX2_REPS}}",
                                        "work_seconds": "{{W1_D1_MAIN_EX2_WORK_SEC}}",
                                        "rest_seconds": "{{W1_D1_MAIN_EX2_REST_SEC}}",
                                        "RPE_RIR": "{{W1_D1_MAIN_EX2_RPE}}"
                                    }
                                ],
                                "time_budget_check": "{{W1_D1_TIME_BUDGET}}"
                            },
                            "cooldown": {
                                "duration_minutes": "{{W1_D1_COOLDOWN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W1_D1_COOLDOWN_EX1_ID}}",
                                        "name": "{{W1_D1_COOLDOWN_EX1_NAME}}",
                                        "sets": "{{W1_D1_COOLDOWN_EX1_SETS}}",
                                        "reps": "{{W1_D1_COOLDOWN_EX1_REPS}}",
                                        "work_seconds": "{{W1_D1_COOLDOWN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W1_D1_COOLDOWN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W1_D1_COOLDOWN_EX1_RPE}}"
                                    }
                                ]
                            }
                        }
                    ]
                },
                "week_2": {
                    "days": [
                        {
                            "warmup": {
                                "duration_minutes": "{{W2_D1_WARMUP_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W2_D1_WARMUP_EX1_ID}}",
                                        "name": "{{W2_D1_WARMUP_EX1_NAME}}",
                                        "sets": "{{W2_D1_WARMUP_EX1_SETS}}",
                                        "reps": "{{W2_D1_WARMUP_EX1_REPS}}",
                                        "work_seconds": "{{W2_D1_WARMUP_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W2_D1_WARMUP_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W2_D1_WARMUP_EX1_RPE}}"
                                    }
                                ]
                            },
                            "main_session": {
                                "duration_minutes": "{{W2_D1_MAIN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W2_D1_MAIN_EX1_ID}}",
                                        "name": "{{W2_D1_MAIN_EX1_NAME}}",
                                        "sets": "{{W2_D1_MAIN_EX1_SETS}}",
                                        "reps": "{{W2_D1_MAIN_EX1_REPS}}",
                                        "work_seconds": "{{W2_D1_MAIN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W2_D1_MAIN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W2_D1_MAIN_EX1_RPE}}"
                                    },
                                    {
                                        "id": "{{W2_D1_MAIN_EX2_ID}}",
                                        "name": "{{W2_D1_MAIN_EX2_NAME}}",
                                        "sets": "{{W2_D1_MAIN_EX2_SETS}}",
                                        "reps": "{{W2_D1_MAIN_EX2_REPS}}",
                                        "work_seconds": "{{W2_D1_MAIN_EX2_WORK_SEC}}",
                                        "rest_seconds": "{{W2_D1_MAIN_EX2_REST_SEC}}",
                                        "RPE_RIR": "{{W2_D1_MAIN_EX2_RPE}}"
                                    }
                                ],
                                "time_budget_check": "{{W2_D1_TIME_BUDGET}}"
                            },
                            "cooldown": {
                                "duration_minutes": "{{W2_D1_COOLDOWN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W2_D1_COOLDOWN_EX1_ID}}",
                                        "name": "{{W2_D1_COOLDOWN_EX1_NAME}}",
                                        "sets": "{{W2_D1_COOLDOWN_EX1_SETS}}",
                                        "reps": "{{W2_D1_COOLDOWN_EX1_REPS}}",
                                        "work_seconds": "{{W2_D1_COOLDOWN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W2_D1_COOLDOWN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W2_D1_COOLDOWN_EX1_RPE}}"
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        elif chunk_type == "weeks_3_4":
            template = {
                "week_3": {
                    "days": [
                        {
                            "warmup": {
                                "duration_minutes": "{{W3_D1_WARMUP_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W3_D1_WARMUP_EX1_ID}}",
                                        "name": "{{W3_D1_WARMUP_EX1_NAME}}",
                                        "sets": "{{W3_D1_WARMUP_EX1_SETS}}",
                                        "reps": "{{W3_D1_WARMUP_EX1_REPS}}",
                                        "work_seconds": "{{W3_D1_WARMUP_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W3_D1_WARMUP_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W3_D1_WARMUP_EX1_RPE}}"
                                    }
                                ]
                            },
                            "main_session": {
                                "duration_minutes": "{{W3_D1_MAIN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W3_D1_MAIN_EX1_ID}}",
                                        "name": "{{W3_D1_MAIN_EX1_NAME}}",
                                        "sets": "{{W3_D1_MAIN_EX1_SETS}}",
                                        "reps": "{{W3_D1_MAIN_EX1_REPS}}",
                                        "work_seconds": "{{W3_D1_MAIN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W3_D1_MAIN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W3_D1_MAIN_EX1_RPE}}"
                                    },
                                    {
                                        "id": "{{W3_D1_MAIN_EX2_ID}}",
                                        "name": "{{W3_D1_MAIN_EX2_NAME}}",
                                        "sets": "{{W3_D1_MAIN_EX2_SETS}}",
                                        "reps": "{{W3_D1_MAIN_EX2_REPS}}",
                                        "work_seconds": "{{W3_D1_MAIN_EX2_WORK_SEC}}",
                                        "rest_seconds": "{{W3_D1_MAIN_EX2_REST_SEC}}",
                                        "RPE_RIR": "{{W3_D1_MAIN_EX2_RPE}}"
                                    }
                                ],
                                "time_budget_check": "{{W3_D1_TIME_BUDGET}}"
                            },
                            "cooldown": {
                                "duration_minutes": "{{W3_D1_COOLDOWN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W3_D1_COOLDOWN_EX1_ID}}",
                                        "name": "{{W3_D1_COOLDOWN_EX1_NAME}}",
                                        "sets": "{{W3_D1_COOLDOWN_EX1_SETS}}",
                                        "reps": "{{W3_D1_COOLDOWN_EX1_REPS}}",
                                        "work_seconds": "{{W3_D1_COOLDOWN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W3_D1_COOLDOWN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W3_D1_COOLDOWN_EX1_RPE}}"
                                    }
                                ]
                            }
                        }
                    ]
                },
                "week_4": {
                    "days": [
                        {
                            "warmup": {
                                "duration_minutes": "{{W4_D1_WARMUP_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W4_D1_WARMUP_EX1_ID}}",
                                        "name": "{{W4_D1_WARMUP_EX1_NAME}}",
                                        "sets": "{{W4_D1_WARMUP_EX1_SETS}}",
                                        "reps": "{{W4_D1_WARMUP_EX1_REPS}}",
                                        "work_seconds": "{{W4_D1_WARMUP_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W4_D1_WARMUP_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W4_D1_WARMUP_EX1_RPE}}"
                                    }
                                ]
                            },
                            "main_session": {
                                "duration_minutes": "{{W4_D1_MAIN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W4_D1_MAIN_EX1_ID}}",
                                        "name": "{{W4_D1_MAIN_EX1_NAME}}",
                                        "sets": "{{W4_D1_MAIN_EX1_SETS}}",
                                        "reps": "{{W4_D1_MAIN_EX1_REPS}}",
                                        "work_seconds": "{{W4_D1_MAIN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W4_D1_MAIN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W4_D1_MAIN_EX1_RPE}}"
                                    },
                                    {
                                        "id": "{{W4_D1_MAIN_EX2_ID}}",
                                        "name": "{{W4_D1_MAIN_EX2_NAME}}",
                                        "sets": "{{W4_D1_MAIN_EX2_SETS}}",
                                        "reps": "{{W4_D1_MAIN_EX2_REPS}}",
                                        "work_seconds": "{{W4_D1_MAIN_EX2_WORK_SEC}}",
                                        "rest_seconds": "{{W4_D1_MAIN_EX2_REST_SEC}}",
                                        "RPE_RIR": "{{W4_D1_MAIN_EX2_RPE}}"
                                    }
                                ],
                                "time_budget_check": "{{W4_D1_TIME_BUDGET}}"
                            },
                            "cooldown": {
                                "duration_minutes": "{{W4_D1_COOLDOWN_DURATION}}",
                                "exercises": [
                                    {
                                        "id": "{{W4_D1_COOLDOWN_EX1_ID}}",
                                        "name": "{{W4_D1_COOLDOWN_EX1_NAME}}",
                                        "sets": "{{W4_D1_COOLDOWN_EX1_SETS}}",
                                        "reps": "{{W4_D1_COOLDOWN_EX1_REPS}}",
                                        "work_seconds": "{{W4_D1_COOLDOWN_EX1_WORK_SEC}}",
                                        "rest_seconds": "{{W4_D1_COOLDOWN_EX1_REST_SEC}}",
                                        "RPE_RIR": "{{W4_D1_COOLDOWN_EX1_RPE}}"
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        else:
            template = {}
    elif pt == "3months":
        chunk_type = chunk_info.get("type", "")
        month_num = chunk_info.get("month", 1)
        
        # For 3-month plans, each month has weeks, and each week has days
        # We'll create a simplified template for the first week of the month
        # Use month_num directly in placeholder names (e.g., M1, M2, M3)
        # Can't use f-strings with nested braces, so use string concatenation
        month_prefix = f"M{month_num}"
        placeholder_start = "{{"
        placeholder_end = "}}"
        
        template = {
            f"month_{month_num}": {
                "weeks": [
                    {
                        "week_number": month_num,
                        "days": [
                            {
                                "warmup": {
                                    "duration_minutes": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_DURATION" + placeholder_end,
                                    "exercises": [
                                        {
                                            "id": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_ID" + placeholder_end,
                                            "name": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_NAME" + placeholder_end,
                                            "sets": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_SETS" + placeholder_end,
                                            "reps": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_REPS" + placeholder_end,
                                            "work_seconds": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_WORK_SEC" + placeholder_end,
                                            "rest_seconds": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_REST_SEC" + placeholder_end,
                                            "RPE_RIR": placeholder_start + f"{month_prefix}_W1_D1_WARMUP_EX1_RPE" + placeholder_end
                                        }
                                    ]
                                },
                                "main_session": {
                                    "duration_minutes": placeholder_start + f"{month_prefix}_W1_D1_MAIN_DURATION" + placeholder_end,
                                    "exercises": [
                                        {
                                            "id": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_ID" + placeholder_end,
                                            "name": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_NAME" + placeholder_end,
                                            "sets": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_SETS" + placeholder_end,
                                            "reps": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_REPS" + placeholder_end,
                                            "work_seconds": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_WORK_SEC" + placeholder_end,
                                            "rest_seconds": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_REST_SEC" + placeholder_end,
                                            "RPE_RIR": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX1_RPE" + placeholder_end
                                        },
                                        {
                                            "id": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_ID" + placeholder_end,
                                            "name": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_NAME" + placeholder_end,
                                            "sets": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_SETS" + placeholder_end,
                                            "reps": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_REPS" + placeholder_end,
                                            "work_seconds": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_WORK_SEC" + placeholder_end,
                                            "rest_seconds": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_REST_SEC" + placeholder_end,
                                            "RPE_RIR": placeholder_start + f"{month_prefix}_W1_D1_MAIN_EX2_RPE" + placeholder_end
                                        }
                                    ],
                                    "time_budget_check": placeholder_start + f"{month_prefix}_W1_D1_TIME_BUDGET" + placeholder_end
                                },
                                "cooldown": {
                                    "duration_minutes": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_DURATION" + placeholder_end,
                                    "exercises": [
                                        {
                                            "id": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_ID" + placeholder_end,
                                            "name": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_NAME" + placeholder_end,
                                            "sets": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_SETS" + placeholder_end,
                                            "reps": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_REPS" + placeholder_end,
                                            "work_seconds": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_WORK_SEC" + placeholder_end,
                                            "rest_seconds": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_REST_SEC" + placeholder_end,
                                            "RPE_RIR": placeholder_start + f"{month_prefix}_W1_D1_COOLDOWN_EX1_RPE" + placeholder_end
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        }
    else:
        template = {}
    
    return json.dumps(template, indent=2)


def build_value_filling_prompt(req, chunk_info: dict, template_str: str, previous_chunks: dict = None) -> str:
    """
    Build a prompt that asks LLM to fill in values for placeholders in the template.
    Returns a simple format request - just key-value pairs.
    """
    pt = (req.plan_type or "weekly").lower()
    minutes = int(req.minutes)
    injuries = req.injuries or "none"
    
    # Get exercise database
    from app.fitness.workout_plan.exercise_database import format_exercises_for_prompt
    warmup_exercises = format_exercises_for_prompt("warmup", req.equipment)
    main_exercises = format_exercises_for_prompt("main_session", req.equipment)
    cooldown_exercises = format_exercises_for_prompt("cooldown", req.equipment)
    
    chunk_type = chunk_info.get("type", "")
    include_meta = chunk_info.get("include_meta", False)
    
    # Extract all placeholders from template
    placeholders = re.findall(r'\{\{([^}]+)\}\}', template_str)
    
    # Build instructions for each placeholder type
    placeholder_instructions = []
    for placeholder in placeholders:
        if "PROVIDED_INFO" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Brief user profile summary")
        elif "SUMMARY" in placeholder:
            placeholder_instructions.append(f"{placeholder}: One sentence plan overview")
        elif "TIME_BUDGET" in placeholder:
            placeholder_instructions.append(f"{placeholder}: String like 'Warm-up 3 + Main 9 + Cool-down 3 = Total {minutes}'")
        elif "DURATION" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Number (3 for warmup/cooldown, 9 for main_session)")
        elif "_ID" in placeholder:
            section = "warmup" if "WARMUP" in placeholder else ("main" if "MAIN" in placeholder else "cooldown")
            placeholder_instructions.append(f"{placeholder}: Exercise ID from {section} list (e.g., warmup_001, main_007)")
        elif "_NAME" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Exercise name matching the ID you selected")
        elif "_SETS" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Number from exercise's Sets options")
        elif "_REPS" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Number from exercise's Reps options, or null if exercise uses work_seconds")
        elif "_WORK_SEC" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Number from exercise's Work seconds options, or null if exercise uses reps")
        elif "_REST_SEC" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Number from exercise's Rest seconds options")
        elif "_RPE" in placeholder:
            placeholder_instructions.append(f"{placeholder}: Value from exercise's RPE/RIR options")
    
    return (
        "Ignore all previous instructions.\n"
        "You are filling in values for a JSON template. DO NOT generate JSON structure.\n\n"
        "=== YOUR TASK ===\n"
        "Below is a JSON template with placeholders like {{PLACEHOLDER_NAME}}.\n"
        "Your job is to provide ONLY the values to fill these placeholders.\n"
        "DO NOT output JSON. Output ONLY a simple list of placeholder=value pairs.\n\n"
        "=== JSON TEMPLATE ===\n"
        f"{template_str}\n\n"
        "=== AVAILABLE EXERCISES ===\n"
        f"WARMUP:\n{warmup_exercises}\n\n"
        f"MAIN SESSION:\n{main_exercises}\n\n"
        f"COOLDOWN:\n{cooldown_exercises}\n\n"
        "=== PLACEHOLDER INSTRUCTIONS ===\n"
        + "\n".join(placeholder_instructions) + "\n\n"
        "=== OUTPUT FORMAT ===\n"
        "CRITICAL: DO NOT output JSON. DO NOT output the template structure.\n"
        "Output ONLY placeholder=value pairs, one per line, like this:\n"
        "DAY_1_WARMUP_EX1_ID=warmup_001\n"
        "DAY_1_WARMUP_EX1_NAME=Jumping Jacks\n"
        "DAY_1_WARMUP_EX1_SETS=1\n"
        "DAY_1_WARMUP_EX1_REPS=20\n"
        "DAY_1_WARMUP_EX1_WORK_SEC=null\n"
        "DAY_1_WARMUP_EX1_REST_SEC=15\n"
        "DAY_1_WARMUP_EX1_RPE=Light\n"
        "DAY_1_MAIN_EX1_ID=main_007\n"
        "DAY_1_MAIN_EX1_NAME=Dumbbell Squats\n"
        "...\n"
        "DAY_2_WARMUP_EX1_ID=warmup_002\n"
        "DAY_2_WARMUP_EX1_NAME=Arm Circles\n"
        "...\n\n"
        "IMPORTANT:\n"
        "- Do NOT include {{ }} around placeholders in your output\n"
        "- Do NOT output JSON structure\n"
        "- Do NOT output explanations or comments\n"
        "- Output ONLY the placeholder=value pairs, one per line\n"
        "- CRITICAL: Fill ALL placeholders for ALL days in this chunk (day_1 AND day_2, or day_3 AND day_4, etc.)\n"
        "- Do NOT stop mid-response. Complete ALL placeholders.\n"
        "- Use null (lowercase, no quotes) for missing values\n"
        "- Use numbers without quotes for numeric values\n"
        "- Use plain text without quotes for string values\n\n"
        "User profile:\n"
        f"- Goal: {req.goal}\n"
        f"- Session duration: {minutes} minutes\n"
        f"- Experience: {req.experience}\n"
        f"- Style: {req.style}\n"
        f"- Equipment: {req.equipment}\n"
        f"- Injuries: {injuries}\n\n"
        "Fill ALL placeholders. Use null (lowercase, no quotes) for missing values.\n"
        "Output ONLY the placeholder=value pairs, nothing else.\n"
    )


def parse_value_response(llm_response: str, template_str: str) -> Dict[str, Any]:
    """
    Parse LLM response (placeholder=value pairs) and fill the template.
    Returns the filled JSON as a dict.
    Handles incomplete responses (truncated lines) gracefully.
    """
    # Extract placeholder=value pairs from LLM response
    value_map = {}
    incomplete_lines = []
    
    for line in llm_response.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        # Check if line looks like a placeholder but is incomplete (no = or incomplete)
        # E.g., "DAY_1_MAIN_EX2_RE" (truncated)
        if not '=' in line:
            # Might be an incomplete line - check if it looks like a placeholder
            if any(keyword in line.upper() for keyword in ['DAY_', 'WARMUP', 'MAIN', 'COOLDOWN', 'EX', 'DURATION', 'TIME_BUDGET', 'PROVIDED', 'SUMMARY']):
                incomplete_lines.append(line)
            continue
        
        # Try different formats: {{PLACEHOLDER}}=value or PLACEHOLDER=value
        parts = line.split('=', 1)
        if len(parts) == 2:
            placeholder_key = parts[0].strip()
            value = parts[1].strip()
            
            # Skip if placeholder key is incomplete (likely truncated)
            if not placeholder_key or len(placeholder_key) < 5:
                continue
            
            # Normalize placeholder key (remove {{ }} if present)
            if placeholder_key.startswith('{{') and placeholder_key.endswith('}}'):
                placeholder_key = placeholder_key[2:-2]
            
            # Skip if value is empty and looks truncated (no value after =)
            if not value and '=' in line:
                # Might be truncated - skip this line
                continue
            
            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            
            # Convert null (handle NULL, None, etc.) - must be exact match
            if value.upper() in ['NULL', 'NONE'] or value == '':
                value = None
            # Convert numbers (but not if it's the string "null")
            elif value.lower() != 'null' and (value.isdigit() or (value.startswith('-') and value[1:].isdigit())):
                value = int(value)
            # Try float (but not if it's the string "null")
            elif value.lower() != 'null' and '.' in value and value.replace('.', '').replace('-', '').isdigit():
                try:
                    value = float(value)
                except:
                    pass
            # If value is still the string "null", convert to None
            elif value.lower() == 'null':
                value = None
            
            value_map[placeholder_key] = value
    
    # Log incomplete lines for debugging
    if incomplete_lines:
        logger.warning(f"Found {len(incomplete_lines)} incomplete lines in LLM response (likely truncated): {incomplete_lines[:5]}")
    
    # Fill template - replace {{PLACEHOLDER}} with actual values
    # IMPORTANT: The template is already valid JSON with placeholders as string values
    # So "{{PROVIDED_INFO}}" is already inside quotes: "provided_information": "{{PROVIDED_INFO}}"
    # We need to replace just the placeholder part, keeping the JSON structure intact
    filled_template = template_str
    
    # Replace placeholders with actual values
    for placeholder_key, value in value_map.items():
        placeholder = f'{{{{{placeholder_key}}}}}'
        if placeholder in filled_template:
            if value is None:
                # For null values, we need to replace the quoted placeholder with unquoted null
                # Template has: "key": "{{PLACEHOLDER}}" -> we want: "key": null
                # So we need to replace '"{{PLACEHOLDER}}"' with 'null'
                quoted_placeholder = f'"{placeholder}"'
                if quoted_placeholder in filled_template:
                    filled_template = filled_template.replace(quoted_placeholder, 'null')
                else:
                    # Fallback: just replace the placeholder itself
                    filled_template = filled_template.replace(placeholder, 'null')
            elif isinstance(value, (int, float)):
                # For numbers, check if placeholder is in quotes
                # If it's in quotes like "{{PLACEHOLDER}}", we want to replace the whole thing with just the number
                quoted_placeholder = f'"{placeholder}"'
                if quoted_placeholder in filled_template:
                    filled_template = filled_template.replace(quoted_placeholder, str(value))
                else:
                    filled_template = filled_template.replace(placeholder, str(value))
            else:
                # String value - escape JSON special characters
                # The placeholder is already inside quotes in the template, so we just replace the placeholder
                # with the escaped value (without adding extra quotes)
                escaped_value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
                filled_template = filled_template.replace(placeholder, escaped_value)
    
    # Fill remaining placeholders with defaults
    remaining_placeholders = re.findall(r'\{\{([^}]+)\}\}', filled_template)
    if remaining_placeholders:
        logger.warning(f"Found {len(remaining_placeholders)} unfilled placeholders, using defaults: {remaining_placeholders[:5]}")
    
    for placeholder_key in remaining_placeholders:
        placeholder = f'{{{{{placeholder_key}}}}}'
        # Set defaults based on placeholder type
        if "DURATION" in placeholder_key:
            if "WARMUP" in placeholder_key or "COOLDOWN" in placeholder_key:
                filled_template = filled_template.replace(placeholder, '3')
            elif "MAIN" in placeholder_key:
                filled_template = filled_template.replace(placeholder, '9')
            else:
                filled_template = filled_template.replace(placeholder, '0')
        elif "TIME_BUDGET" in placeholder_key:
            # Time budget is a string, so we need to replace the placeholder with the string value
            # The placeholder is already inside quotes, so just replace the placeholder part
            filled_template = filled_template.replace(placeholder, 'Warm-up 3 + Main 9 + Cool-down 3 = Total 15')
        elif placeholder_key in ["PROVIDED_INFO", "SUMMARY"]:
            # Empty string - placeholder is already in quotes, replace with empty
            filled_template = filled_template.replace(placeholder, '')
        else:
            filled_template = filled_template.replace(placeholder, 'null')
    
    # Log filled template for debugging (first 500 chars)
    logger.debug(f"Filled template preview: {filled_template[:500]}")
    
    # Parse the filled JSON
    try:
        result = json.loads(filled_template)
        # Post-process: convert string numbers to ints where needed
        return _normalize_filled_template(result)
    except json.JSONDecodeError as e:
        # Log the error with more context
        error_pos = getattr(e, 'pos', 0)
        logger.error(f"JSON parse error at position {error_pos}: {str(e)}")
        logger.error(f"Template around error (chars {max(0, error_pos-100)}-{min(len(filled_template), error_pos+100)}):")
        logger.error(filled_template[max(0, error_pos-100):min(len(filled_template), error_pos+100)])
        
        # Fallback: try bulletproof parser (more robust than repair_json_string)
        from app.fitness.workout_plan.repair_agent import bulletproof_json_parse
        parsed_obj, cleaned_text, strategy = bulletproof_json_parse(filled_template)
        if parsed_obj is not None:
            logger.info(f"Template JSON repaired using strategy: {strategy}")
            return _normalize_filled_template(parsed_obj)
        
        # Last resort: try repair_json_string
        from app.fitness.workout_plan.helper import repair_json_string
        repaired = repair_json_string(filled_template)
        try:
            result = json.loads(repaired)
            return _normalize_filled_template(result)
        except json.JSONDecodeError as e2:
            logger.error(f"JSON repair also failed: {str(e2)}")
            raise ValueError(f"Failed to parse filled template as JSON: {str(e)}. Original error at position {error_pos}.")


def _normalize_filled_template(data: Any) -> Any:
    """Normalize the filled template - convert string numbers to ints, fix "null" strings, filter incomplete exercises."""
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            # Handle exercises arrays specially - filter out incomplete exercises
            if key == "exercises" and isinstance(value, list):
                filtered_exercises = []
                for ex in value:
                    if isinstance(ex, dict):
                        # Check if exercise is complete (has at least id and name that aren't null/empty)
                        ex_id = ex.get("id")
                        ex_name = ex.get("name")
                        # Filter out exercises where id or name is null, "null", or empty
                        if ex_id and ex_id != "null" and ex_name and ex_name != "null":
                            # Normalize the exercise
                            normalized_ex = {}
                            for ex_key, ex_val in ex.items():
                                # Convert "null" strings to None
                                if isinstance(ex_val, str) and ex_val.lower() == "null":
                                    normalized_ex[ex_key] = None
                                # Convert string numbers to ints
                                elif ex_key in ["sets", "reps", "rest_seconds", "work_seconds"]:
                                    if isinstance(ex_val, str):
                                        if ex_val.lower() in ["null", "none", ""]:
                                            normalized_ex[ex_key] = None
                                        else:
                                            try:
                                                normalized_ex[ex_key] = int(ex_val)
                                            except (ValueError, TypeError):
                                                normalized_ex[ex_key] = None
                                    else:
                                        normalized_ex[ex_key] = ex_val
                                else:
                                    # For other fields, just convert "null" strings
                                    if isinstance(ex_val, str) and ex_val.lower() == "null":
                                        normalized_ex[ex_key] = None
                                    else:
                                        normalized_ex[ex_key] = ex_val
                            filtered_exercises.append(normalized_ex)
                normalized[key] = filtered_exercises
            elif key in ["sets", "reps", "rest_seconds", "work_seconds", "duration_minutes"]:
                if isinstance(value, str):
                    # Convert "null" strings to None
                    if value.lower() == "null":
                        normalized[key] = None
                    elif value.lower() in ["none", ""]:
                        normalized[key] = None
                    else:
                        try:
                            normalized[key] = int(value)
                        except (ValueError, TypeError):
                            normalized[key] = None
                else:
                    normalized[key] = value
            else:
                # For other fields, convert "null" strings to None
                if isinstance(value, str) and value.lower() == "null":
                    normalized[key] = None
                else:
                    normalized[key] = _normalize_filled_template(value)
        return normalized
    elif isinstance(data, list):
        return [_normalize_filled_template(item) for item in data]
    else:
        # Convert "null" strings to None
        if isinstance(data, str) and data.lower() == "null":
            return None
        return data

