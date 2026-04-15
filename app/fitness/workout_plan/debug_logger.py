import os

# Enable via env (default off)
DEBUG_ENABLED = os.getenv("DEBUG_GENERATION_LOGS", "false").lower() == "true"

# Absolute log directory
LOG_DIR = "/home/administrator/Documents/projects/heybobo-fitness-ai/logs"
os.makedirs(LOG_DIR, exist_ok=True)


def log_user_input(request_id: str, user_input: str):
    if not DEBUG_ENABLED:
        return
    log_path = os.path.join(LOG_DIR, f"{request_id}.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=== USER INPUT ===\n")
            f.write(f"{user_input}\n\n")
    except Exception:
        pass  # never break pipeline


def log_day_attempt(request_id: str, day_num: int, attempt: int, prompt: str, raw_output: str):
    if not DEBUG_ENABLED:
        return
    log_path = os.path.join(LOG_DIR, f"{request_id}.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"=== DAY {day_num} / ATTEMPT {attempt} ===\n")
            f.write("PROMPT SENT TO LLM:\n")
            f.write(f"{prompt}\n\n")
            f.write("RAW LLM OUTPUT:\n")
            f.write(f"{raw_output}\n\n")
    except Exception:
        pass  # never break pipeline

