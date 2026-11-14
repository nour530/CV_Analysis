# JobMatch AI - LLM Integration

This documentation describes the provider-independent LLM architecture and the working OpenAI Responses API provider. It does not describe Angular integration, model training, embeddings, or changes to the deterministic matching algorithm.

## 1. Architecture

```text
app/
  config.py                       Environment-backed LLM settings
  core/
    logging.py                    Structured, privacy-safe LLM telemetry
  schemas/
    common.py                     Shared evidence, confidence, uncertainty, metadata models
    resume_analysis.py            Résumé skills, education, experience, evidence
    job_analysis.py               Job skills, requirements, responsibilities, evidence
    match_analysis.py             Match score, matched/missing requirements, explanation
  services/
    llm/
      exceptions.py               Typed LLM errors
      prompt_templates.py         Versioned prompts and untrusted-input boundaries
      base.py                     Provider interface, response validation, input checks
      mock_provider.py            Deterministic provider for tests and local development
      openai_provider.py          OpenAI Responses API provider
      openai_schema.py            Strict JSON Schema generation for structured outputs
      factory.py                  Provider selection from settings
```

The service layer depends on `LLMProvider`, not on a specific vendor. Provider responses are parsed as JSON and validated with Pydantic before being returned. Invalid responses become `LLMResponseValidationError`; provider code never returns loosely typed payloads.

## 2. Provider abstraction

`app.services.llm.base.LLMProvider` defines the contract:

- `analyze_resume(text) -> ResumeAnalysis`
- `analyze_job(text) -> JobAnalysis`
- `analyze_job_description(text) -> JobAnalysis`
- `extract_structured(text, schema_name=..., fields=..., response_model=...) -> BaseModel`
- `explain_match(resume_analysis, job_analysis, verified_results=...) -> MatchAnalysis`

`BaseLLMProvider` implements the common workflow:

1. Validate input length and reject empty text.
2. Build a versioned prompt with a JSON schema and untrusted-input markers.
3. Delegate completion to `_complete_json(...)`.
4. Parse JSON and validate against the requested Pydantic schema.
5. Attach provider/model/prompt metadata.
6. Log only safe operational metadata.

`explain_match` accepts optional `verified_results` from the deterministic matching layer. The LLM is instructed to explain those results, not to replace or recompute the matching algorithm. If no verified results are supplied, the explanation is limited to what the supplied analyses support.

## 3. Configuration

Configuration is read from environment variables. Secrets must never be committed.

| Variable | Purpose | Required |
| --- | --- | --- |
| `LLM_PROVIDER` | `openai` or `mock` | No; defaults to `openai` |
| `OPENAI_API_KEY` | Backend-only OpenAI credential | Yes for `openai` |
| `OPENAI_MODEL` | Model identifier selected for the account | Yes for `openai` |
| `OPENAI_TIMEOUT_SECONDS` | Per-request timeout, `0.1` to `600` | No; defaults to `60` |
| `OPENAI_MAX_RETRIES` | SDK retry count, `0` to `5` | No; defaults to `2` |

No model identifier is assumed. The deployment must supply a model that its OpenAI account can access.

Example:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY="<set-in-your-local-environment>"
export OPENAI_MODEL="<model-supported-by-your-account>"
```

Load and validate settings with:

```python
from app.config import LLMSettings
from app.services.llm.factory import create_llm_provider

settings = LLMSettings.from_env()
provider = create_llm_provider(settings)
```

`OPENAI_API_KEY` is stored as a Pydantic `SecretStr`. `public_config()` exposes only provider, model, and whether a key is configured. The key is never placed in request payloads, logs, API responses, or frontend-facing configuration.

## 4. Security and privacy

- The API key is read only from the backend environment.
- `.env` is ignored by Git; `.env.example` contains placeholders only.
- `.env.example` is explicitly unignored so the template can be committed.
- OpenAI requests use `store=False`.
- API keys, prompts, CV text, provider response bodies, and personal content are never logged.
- Logs contain operation names, provider, model, input character count, duration, and error type.
- Prompt instructions treat CV and job-description content as untrusted data and forbid following embedded instructions.
- Prompt instructions forbid inventing skills, experience, education, certifications, or qualifications.
- Skills and requirements record whether evidence is explicit or inferred, plus source evidence.
- Unknown values use `null`, empty lists, or structured uncertainty.
- Schemas contain no contact-detail fields.
- Only individual CV or job text is sent when a caller explicitly invokes an LLM operation. Complete datasets must not be uploaded.
- Optional live tests use synthetic text only.

See `openai_integration.md` for external processing, retention, cost, and integration-test details.

## 5. Response validation

The base provider accepts a JSON-compatible object or JSON string. It rejects:

- malformed JSON,
- non-object responses,
- empty or incomplete responses,
- missing required fields,
- invalid enum values,
- confidence values outside `0.0` to `1.0`,
- unknown fields,
- invalid year ranges and experience ranges,
- invalid grounded evidence.

Validation errors include only field locations, error types, and messages. Input values are not included in raised error text.

## 6. Providers

`MockLLMProvider` returns deterministic valid payloads for all supported operations and is the default choice for tests and local development.

```python
from app.services.llm.mock_provider import MockLLMProvider

provider = MockLLMProvider()
resume = provider.analyze_resume("Python developer with evidence.")
job = provider.analyze_job_description("Python is required.")
match = provider.explain_match(resume, job)
```

`OpenAIProvider` uses the official OpenAI Python SDK and the Responses API. See `openai_integration.md`.

## 7. Adding another provider

1. Create a subclass of `BaseLLMProvider` in `app/services/llm/`.
2. Set `provider_name` and `model_name`.
3. Implement `_complete_json(operation, prompt, response_model)`.
4. Return a JSON-compatible dict or JSON string that conforms to the requested schema.
5. Add the provider name to `ProviderName` in `app/config.py` and configuration validation.
6. Register it in `create_llm_provider()` in `factory.py`.
7. Add tests for valid responses, invalid responses, configuration failures, and logging safety.
8. Document provider-specific rate limits, retries, timeouts, and data-retention behavior.

## 8. Tests

Run:

```bash
ai-engine/venv/Scripts/python.exe -m pytest ai-engine/tests -q
```

The test suite covers:

- abstract provider interface and schema validation,
- mock provider behavior,
- OpenAI request construction and structured-output schemas,
- Responses API `store=False`,
- timeout, rate-limit, authentication, and malformed-response mapping,
- structured-output fallback to JSON object mode,
- secret redaction and privacy-safe logging,
- prompt injection defenses,
- optional live OpenAI integration disabled by default.

The optional live test runs only when `RUN_OPENAI_INTEGRATION_TESTS=1`, `OPENAI_API_KEY`, and `OPENAI_MODEL` are set.

## 9. Current limitations

- LLM explanations are advisory; deterministic matching results remain authoritative.
- Token counting, cost accounting, streaming, provider fallback, and response caching are not implemented.
- Structured outputs are used where the selected model supports them; unsupported models fall back once to JSON object mode.
- Generic structured extraction with free-form objects cannot use strict JSON Schema and therefore uses JSON object mode.
- LLM outputs are not a substitute for human review.
- No embeddings, vector store, model training, or Angular integration is included.
