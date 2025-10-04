### PAK: Required .env and API Keys in APKG

Background & Context

- Pixell Agent Runtime (PAR) is multi-tenant and hosts agents from different developers.
- Many agents require API keys (e.g., `OPENAI_API_KEY`) to function. Historically, agents returned mock responses due to missing credentials.
- Root causes addressed here:
  - Agent `.env` files were not included in APKG builds
  - PAR agent subprocesses inherited only PAR's environment (no agent-specific secrets)
  - Each agent needs isolated environment variables for their own credentials

Desired State

- APKG is self-contained with everything needed to run (code + configuration)
- `.env` file is REQUIRED in APKG and provides agent-specific environment variables
- PAR loads `.env` from APKG and injects into the agent subprocess environment

---

What Needs to Happen

Phase 1: PAK Changes (Require .env in APKG)

1.1 Update Builder to Require `.env`

- File: `pixell-agent-kit/pixell/core/builder.py`
- Change: Fail build if `.env` is missing in the project root. Always include `.env` in the APKG root.
- Rationale: Every AI agent needs credentials; builds without a `.env` indicate misconfiguration.

Implementation Notes

- Ensure `.env` is treated as a required artifact (not optional). If missing, raise a clear, actionable error:
  - "Missing required .env file at project root. Create a `.env` with placeholders or real values. See `.env.example`."
- Continue to include other optional items (e.g., `README.md`, `LICENSE`).

1.2 Update CLI Build Command

- File: `pixell-agent-kit/pixell/cli/main.py`
- Change: Remove any flag that excludes `.env` (e.g., `--no-env`). Building without a `.env` should error by default.
- Rationale: Enforces consistent packaging. For production, values can be placeholders and overridden at deploy time (Phase 2).

1.3 Add `.env.example` to Init Template

- File: `pixell-agent-kit/pixell/cli/main.py`
- Change: `pixell init` should create `.env.example` with guidance. Users copy to `.env` and fill in values.
- Suggested template content:

```markdown
# Environment Variables Template
# Copy this file to `.env` and set values.

# SECURITY: The `.env` file is included in your APKG package.
# Use safe defaults or placeholders if you do not want to embed real secrets.

# Example: OpenAI API Key
# OPENAI_API_KEY=your-api-key-here

# Example: Network bindings (use 0.0.0.0 in containers)
# API_HOST=0.0.0.0
# API_PORT=8080

# Example: Database connection (prefer service names in Docker)
# DB_HOST=database
# DB_PORT=5432
```

1.4 Add Security Validation and Presence Checks

- File: `pixell-agent-kit/pixell/core/validator.py`
- Required checks:
  - Presence: Error if `.env` file is missing.
  - Content warnings: Warn if `.env` appears to contain real secrets:
    - Patterns: `sk-` (OpenAI), `AWS_SECRET_ACCESS_KEY`, `PRIVATE_KEY`, `-----BEGIN` (keys/certs)
  - Path hygiene: Warn on suspicious absolute paths (e.g., `/Users/...`) that harm portability.
- Logging: Provide warnings but never log secret values.

Phase 2: PAR Changes (Load .env from APKG)

2.1 Load `.env` When Starting Agent Subprocess

- File: `pixell-agent-runtime/src/pixell_runtime/three_surface/subprocess_runner.py`
- Change: Merge environment variables in this precedence order:
  1) Runtime environment (from DeploymentRequest) [HIGHEST]
  2) `.env` from APKG (required) [MIDDLE]
  3) Base PAR environment [LOWEST]

Implementation Sketch

- Build `env` dict for the agent subprocess as follows:
  - Start from `os.environ`
  - Merge parsed key-values from `AGENT_PACKAGE_PATH/.env`
  - Merge `deployment.environment` (if provided by PAC)
  - Add PAR-managed variables (`AGENT_PACKAGE_PATH`, `AGENT_VENV_PATH`, `REST_PORT`, `A2A_PORT`, `UI_PORT`, `MULTIPLEXED`)
- Parser: simple line-by-line `KEY=VALUE` parser
  - Ignore blank lines and `#` comments
  - Trim whitespace; strip matching single/double quotes from values
  - Do not support multi-line values in Phase 1
- Logging:
  - Info: `.env` loaded with `var_count` and the list of variable keys
  - Never log variable values

---

Critical Things to Look Out For

1) Naming Conflicts

- Agent `.env` may define keys that conflict with PAR defaults (e.g., `PORT`).
- Precedence intentionally allows the agent to override PAR defaults within the agent subprocess.
- Document this behavior clearly.

2) Network Address Variables

- Common pitfalls:
  - `HOST=localhost` breaks in Docker/ECS; prefer `0.0.0.0` for bind addresses
  - Hard-coded IPs break across environments; prefer service names in containerized deployments

3) File Path Variables

- Avoid absolute paths tied to developer machines (e.g., `/Users/<name>/...`).
- Prefer relative paths inside the package or standard locations like `/tmp`.

4) Secret Exposure

- `.env` is required and included in APKG; treat it as sensitive.
- Best practices:
  - Development: you may place real secrets in `.env` locally
  - Shared artifacts: use placeholders in `.env` and override with runtime environment (Phase 2)
  - Never log values; validators should only warn, not print contents

5) Compatibility

- Old APKGs (no `.env`) will now fail build under new PAK if `.env` is missing.
- PAR should continue to run older packages that already contain `.env` if present.

Variable Substitution Edge Cases (Parser Limits)

- Quotes in values: support `'single'` and `"double"` quotes
- Special characters: preserve `@`, `!`, `?`, `&`, `=` inside values
- Multi-line values: not supported in Phase 1

---

What to Test After Implementation

Test Suite 1: PAK Build Tests

- `test_env_required_for_build()`
  - Given an agent project without `.env`, building should raise a clear error
- `test_env_included_in_apkg()`
  - Given a project with `.env`, APKG contains `.env` with exact contents
- `test_env_security_validation()`
  - `.env` containing `sk-`, `AWS_SECRET_ACCESS_KEY`, `-----BEGIN` triggers warnings
- `test_env_path_hygiene()`
  - `.env` with suspicious absolute paths triggers warnings

Test Suite 2: PAR Runtime Tests

- `test_load_env_from_apkg()`
  - Ensure agent subprocess environment contains keys from `.env`
- `test_env_overrides_par_environment()`
  - If PAR has `API_KEY=par`, and `.env` has `API_KEY=agent`, subprocess sees `agent`
- `test_runtime_env_overrides_dotenv()`
  - If DeploymentRequest provides `API_KEY=runtime`, it overrides `.env`
- `test_env_parsing_edge_cases()`
  - Verify parsing for quotes, special characters, empty values, and spacing

Test Suite 3: Integration Tests

- `test_full_workflow_with_env()`
  - `init` → create `.env` → `build` → `deploy` → invoke → agent uses API key

Test Suite 4: Security Tests

- `test_secret_detection()`
  - Ensure validator detects common secret patterns
- `test_env_not_logged()`
  - Ensure logs list keys but never values

---

Best Long-Term Solution

Current (Phase 1): Required `.env` in APKG

- Pros: Simple, self-contained, immediate functionality
- Cons: Secrets may be baked into packages; rotation requires rebuild

Better (Phase 2): Environment Injection at Deploy Time (PAC → PAR)

- Add `environment: Dict[str, str]` to `DeploymentRequest`/`DeploymentRecord`
- Precedence: runtime env > `.env` > base PAR
- Benefits: Secrets not stored in APKG; can rotate without rebuild; centralized secret management

Ultimate (Phase 3): Service-Bound Secrets

- Agents fetch secrets at startup using IAM roles and services like AWS Secrets Manager
- Benefits: No secrets leave the cloud provider; audit trails; rotation support

---

Rollout Plan

Week 1: Foundation (Current Priority)

- Require `.env` in PAK builds; include in APKG
- Implement `.env` loading in PAR subprocess
- Add tests for both
- Test with a representative agent (e.g., vivid-commenter)

Week 2: Security & Validation

- Add/strengthen secret detection in validator
- Add `.env.example` to init template
- Document best practices for networks, paths, and secrets

Week 3: Runtime Environment Injection (PAC)

- Add environment injection fields and precedence
- Update PAC to fetch/forward secrets; update PAR to merge
- Verify secret rotation workflow

---

Summary

- `.env` is REQUIRED in every APKG. Builds fail if it is missing.
- PAR loads `.env` into the agent subprocess. Runtime-provided environment (if present) overrides `.env`.
- This ensures agents have credentials to run while providing a path to stronger secret hygiene via runtime injection and service-bound secrets.


