# AGENTS.md — system-integration-tests

## Quick Reference for AI Agents

### Key Commands

- **Quality gates**: `cd system-integration-tests && bash scripts/quality-gates.sh`
- **Source dir**: `system-integration-tests/system_integration_tests/` (underscored)
- **Typecheck**: `run_timeout 120 basedpyright system_integration_tests/`

### Mandatory Rules

Before any action, read:
`unified-trading-pm/cursor-configs/SUB_AGENT_MANDATORY_RULES.md`

### Rules Summary

- `uv pip install` not `pip install`
- Flat deps only — no `[project.optional-dependencies]`
- `basedpyright` not `pyright`
- `UnifiedCloudConfig` not `os.getenv()`
- No `# type: ignore` to hide architectural violations
- No `try/except ImportError` fallbacks

### Workspace

WORKSPACE_ROOT: `/Users/ikennaigboaka/Code/unified-trading-system-repos`
