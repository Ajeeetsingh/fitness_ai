# Phase-1 Developer Notes & Expectations

## Architecture Principles

### 1. Separation of Concerns
- **Business logic** is separate from **prompt strings**
- Prompts belong in `prompt_builder.py` only
- Do not embed prompt strings in service or orchestrator modules

### 2. Module Structure
All new modules should be:
- **Small**: Single responsibility per module
- **Well-documented**: Docstrings for all public functions
- **Unit-tested**: Tests in `tests/` directory

### 3. Module Responsibilities

#### `prompt_builder.py`
- Builds SYSTEM and USER prompts
- Contains sport profile hints
- **No business logic**, only string assembly

#### `orchestrator.py`
- Handles LLM call orchestration
- Manages chunking for weekly plans
- Merges chunk results
- **No prompt strings** (calls `prompt_builder`)

#### `validator.py`
- JSON Schema validation using `jsonschema`
- Conservative auto-fill (max 6 fields)
- Structured error reporting

#### `repair_agent.py`
- Single-purpose LLM repair call
- Uses smaller/cheaper model
- Returns repaired JSON or `{"error":"repair_failed"}`

#### `replicator.py`
- Deterministic monthly replication
- Applies progression rules
- Records rules in metadata

#### `diagnostics.py`
- Metrics collection and emission
- Failure sample saving
- No business logic

## Code Quality Standards

### Commits
- Every commit must include tests OR a brief explanation why tests aren't included
- Example: "chore: update config file (no tests needed for config-only change)"
- Use clear, descriptive commit messages

### Testing
- All tests must be runnable via `pytest -q` from repo root
- Tests should not require network access (mock LLM calls)
- Tests should be fast (< 1 second per test)

### Logging
- **Do not commit raw LLM outputs to git**
- Save raw outputs to `logs/llm_raw/` and `logs/failed_raw/`
- These directories are in `.gitignore`

### Dependencies
- If adding third-party packages, add to `requirements.txt` with exact versions
- Current required packages:
  - `pytest==8.3.4`
  - `jsonschema==4.23.0`

## Common Patterns

### Prompt Building
```python
# ✅ Good: Prompt in prompt_builder.py
from app.fitness.workout_plan import prompt_builder
system_prompt = prompt_builder.build_system_prompt(mode)
user_prompt = prompt_builder.build_user_prompt(info, template_path)

# ❌ Bad: Prompt embedded in orchestrator
system_prompt = "You are a plan-generation assistant..."
```

### Error Handling
```python
# ✅ Good: Structured error with context
raise HTTPException(
    status_code=502,
    detail={
        "error_code": "CHUNK_REPAIR_FAILED_STRICT",
        "message": f"Chunk {chunk_id} failed",
        "chunk_id": chunk_id,
        "request_id": request_id
    }
)

# ❌ Bad: Generic error
raise Exception("Something went wrong")
```

### Validation
```python
# ✅ Good: Validate then auto-fill if needed
is_valid, result, errors, auto_filled = validator.validate_and_auto_fill(
    plan_data,
    schema_type,
    strict=strict
)

# ❌ Bad: Skip validation
plan_data["missing_field"] = "default"  # No validation
```

## File Organization

```
app/fitness/workout_plan/
├── prompt_builder.py      # Prompt strings only
├── orchestrator.py        # LLM orchestration
├── validator.py          # Schema validation
├── repair_agent.py       # JSON repair
├── replicator.py         # Monthly replication
├── diagnostics.py        # Metrics & logging
├── service_refactored.py # High-level service API
└── templates/
    ├── schemas/          # JSON Schema files
    └── *.json            # Template files

tests/
├── test_prompt_builder.py
├── test_orchestrator_chunk_merge.py
├── test_validator.py
└── test_repair_agent.py

logs/                     # Gitignored
├── llm_raw/             # Raw LLM responses
└── failed_raw/          # Failure samples
```

## Troubleshooting

### If Tests Fail
1. Check that schema files exist in `templates/schemas/`
2. Verify `pytest` and `jsonschema` are installed
3. Run tests individually: `pytest tests/test_validator.py -v`

### If Parse Fail Rate High
1. Check `logs/failed_raw/` for common patterns
2. Review prompts in `prompt_builder.py`
3. Check repair agent success rate in diagnostics
4. Consider prompt tuning if pattern is clear

### If Generation Time Slow
1. Check LLM API response times
2. Review chunking strategy (may need adjustment)
3. Check for unnecessary retries
4. Monitor token usage

## Questions or Issues?

If anything fails or is ambiguous:
1. **Stop immediately**
2. Output structured JSON to console:
   ```json
   {
     "error": "short message",
     "file": "file path causing issue",
     "snippet": "3-line code excerpt around problem (lines & content)"
   }
   ```
3. Return that JSON in PR comment
4. Do not continue until issue is resolved

