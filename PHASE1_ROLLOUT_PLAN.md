# Phase-1 Refactor Rollout Plan

## Overview
This document outlines the rollout plan for the Phase-1 refactored workout plan generation pipeline.

## Prerequisites
- [x] All unit tests pass locally
- [x] Code review completed
- [x] Schema files exist in `templates/schemas/`
- [x] Logging directories created (`logs/llm_raw/`, `logs/failed_raw/`)

## Branch Strategy
1. Create branch: `phase1/refactor-orchestrator`
2. Commit incremental changes with clear messages:
   - `refactor: split helper.py into prompt_builder, repair_agent, diagnostics`
   - `chore: wire service to new orchestrator and validator`
   - `refactor: move plan_replicator to replicator with progression rules`
   - `test: add comprehensive unit tests for new modules`
   - `docs: add rollout plan and developer notes`

## Local Testing
1. Run all tests:
   ```bash
   pytest -q
   ```
2. Fix any test failures
3. Verify no linter errors:
   ```bash
   # Run your linter (ruff, pylint, etc.)
   ```

## Staging Deployment
1. Deploy to staging environment
2. Run pilot batch:
   - Generate 50 weekly plans with `strict=false`
   - Use diverse inputs (different goals, experience levels, equipment)
   - Monitor for 24 hours
3. Capture metrics:
   - `plan_parse_fail_rate` (target: < 5%)
   - `plan_generation_time_seconds` (targets: daily <10s, weekly <30s, monthly <60s)
   - `plan_repair_attempted` count
   - `plan_auto_filled_count` average

## Success Criteria
- ✅ `parse_fail_rate < 5%`
- ✅ Average generation time within targets:
  - Daily: < 10 seconds
  - Weekly: < 30 seconds
  - Monthly: < 60 seconds
- ✅ No critical errors in logs
- ✅ All failure samples saved to `logs/failed_raw/`

## Production Deployment
If staging metrics meet success criteria:
1. Merge `phase1/refactor-orchestrator` to `main`
2. Deploy to production
3. Monitor metrics for 48 hours
4. Gradually increase traffic if metrics remain stable

## Rollback Plan
If `parse_fail_rate >= 5%`:
1. **Stop deployment**
2. Collect top 20 failing raw outputs from `logs/failed_raw/`
3. Analyze common failure patterns:
   - Parse errors
   - Validation failures
   - Timeout issues
4. Return to development for prompt tuning
5. Update prompts in `prompt_builder.py` and `repair_agent.py`
6. Re-run tests and staging pilot

## Monitoring Checklist
- [ ] Parse fail rate dashboard
- [ ] Generation time alerts
- [ ] Error rate alerts
- [ ] LLM API health checks
- [ ] Disk space for logs (`logs/llm_raw/`, `logs/failed_raw/`)

## Post-Deployment
- Review metrics daily for first week
- Collect user feedback
- Document any edge cases discovered
- Update developer notes if needed

