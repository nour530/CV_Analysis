# JobMatch AI - OpenAI Integration Guide

This guide covers the OpenAI Responses API provider used by JobMatch AI. It does not cover Angular integration, embeddings, model training, or changes to the deterministic matching algorithm.

## 1. Setup

The provider uses the official OpenAI Python SDK. Install the runtime dependencies into the project environment:

```bash
ai-engine/venv/Scripts/python.exe -m pip install -r ai-engine/requirements.txt
```

The application reads configuration from process environment variables. A template is available at the repository root:

```bash
cp .env.example .env
```

Fill in `.env` locally, then load it with your shell, process manager, container secret store, or deployment platform. The application does not commit, print, or expose secret values.

Never commit `.env`. The repository `.gitignore` excludes `.env` while allowing the placeholder-only `.env.example`.

## 2. Environment variables

| Variable | Purpose | Required | Default |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | Select `openai` or `mock` | No | `openai` |
| `OPENAI_API_KEY` | Backend-only OpenAI API credential | Yes for `openai` | none |
| `OPENAI_MODEL` | Model identifier available to the account | Yes for `openai` | none |
| `OPENAI_TIMEOUT_SECONDS` | Per-request timeout, between `0.1` and `600` | No | `60` |
| `OPENAI_MAX_RETRIES` | SDK retries after the first attempt, `0` to `5` | No | `2` |

No default model is assumed. A missing or empty `OPENAI_MODEL` is a configuration error. Choose a model supported by the deployment account and verify its structured-output support before production rollout.

Example:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY="<load-from-secret-manager>"
export OPENAI_MODEL="<model-supported-by-your-account>"
export OPENAI_TIMEOUT_SECONDS=60
export OPENAI_MAX_RETRIES=2
```

Use `LLM_PROVIDER=mock` for local development without external requests.

## 3. API usage

```python
from app.config import LLMSettings
from app.services.llm.factory import create_llm_provider

settings = LLMSettings.from_env()
provider = create_llm_provider(settings)

resume_analysis = provider.analyze_resume(individual_cv_text)
job_analysis = provider.analyze_job_description(individual_job_text)

match_analysis = provider.explain_match(
    resume_analysis,
    job_analysis,
    verified_results=deterministic_match_results,
)
```

The OpenAI provider uses `client.responses.create(...)` with:

- `model` from `OPENAI_MODEL`,
- `instructions` containing the versioned system prompt,
- `input` containing the user prompt and delimited source text,
- `store=False`,
- `max_output_tokens=8192`,
- a strict JSON Schema response format where supported,
- JSON object mode as a one-time fallback when structured outputs are unsupported,
- Pydantic validation of the returned JSON before it reaches application code.

No API key is included in the request payload. The SDK receives the key only when the provider constructs its backend client.

## 4. Structured outputs

`openai_schema.py` converts Pydantic response models into strict JSON Schemas for the Responses API:

- every object gets `additionalProperties: false`,
- every property is required,
- nullability is expressed through schema unions,
- validation-only keywords unsupported by strict structured outputs are removed,
- `const` values are normalized to `enum`,
- response-format names are derived safely from model class names.

Pydantic remains the final validator. If the model returns malformed JSON, an unexpected field, a missing field, an invalid enum, or invalid evidence, the request fails with `LLMResponseValidationError` rather than returning partial data.

Generic structured extraction with a free-form `data` object cannot be represented by strict JSON Schema. It uses JSON object mode, includes the schema in the prompt, and is still validated with Pydantic.

## 5. Prompt-injection defenses

CVs and job descriptions are untrusted input. The prompt layer:

- labels source text with explicit untrusted-input markers,
- states that the content is data, not instructions,
- instructs the model to ignore embedded role changes, formatting requests, requests for secrets, or attempts to override system rules,
- tells the model never to invent skills, experience, education, certifications, or qualifications,
- requires explicit evidence to be distinguished from inference,
- requires unknown information to be represented as `null`, an empty list, or a structured uncertainty,
- forbids returning personal contact details.

Prompt hardening reduces risk but does not eliminate prompt-injection risk. Application code must not grant an LLM tool access to secrets, file systems, databases, or privileged actions.

## 6. Error handling and retries

| Condition | Provider behavior |
| --- | --- |
| Missing key or model | `LLMConfigurationError` before a request is sent |
| Request timeout | `LLMTimeoutError` |
| Rate limit after SDK retries | `LLMRateLimitError` |
| Invalid credential or permission | `LLMAuthenticationError` |
| Rejected request or unsupported schema | `LLMProviderError`; structured-output requests may fall back once to JSON object mode |
| Connection failure | `LLMProviderUnavailableError` |
| Invalid, empty, incomplete, or non-conforming response | `LLMResponseValidationError` or `LLMProviderError` |

The SDK retry count is bounded by `OPENAI_MAX_RETRIES`, which is validated to `0` through `5`. There is no application-level infinite retry loop. The structured-output fallback happens at most once per request.

Error messages and logs do not include the API key, prompt text, CV content, or raw provider response body.

## 7. Privacy and external processing

Sending individual content to OpenAI is an external data-processing event.

- Do not upload complete datasets, database extracts, bulk CV collections, or private data directories.
- Send only the individual CV or job-description text needed for an explicitly requested feature.
- Inform the user that the relevant feature uses an external AI provider before sending content.
- Do not send content for background jobs, analytics, or proactive enrichment without explicit authorization.
- Do not include personal contact details in prompts or response schemas.
- Unit and integration tests use synthetic examples only.

The provider sets `store=False` for Responses API requests. This disables stored response objects for later retrieval through the API, but it does not by itself guarantee zero provider-side retention. OpenAI's current API data-usage, abuse-monitoring, and retention terms still apply. Before production deployment:

1. Review the organization's current OpenAI data-processing and retention terms.
2. Determine whether zero data retention or another enterprise configuration is available and required.
3. Document the legal basis, user notice, retention period, data location, and deletion process.
4. Avoid sending regulated or highly sensitive personal data unless the deployment has an approved legal and security basis.
5. Keep application logs free of prompt and response content.
6. Do not enable OpenAI SDK HTTP debug logging in production; request bodies can contain CV content.

## 8. Costs and rate limits

OpenAI usage is billed by the selected model, input tokens, output tokens, and any retries or fallback requests. Costs vary by model and account tier.

- Only call the provider when a user explicitly requests a feature that needs it.
- Keep individual inputs focused and avoid sending unnecessary CV or job-description material.
- `max_output_tokens` is capped at `8192`; lower values reduce worst-case output cost.
- SDK retries and the one-time structured-output fallback can produce additional billed requests.
- Monitor usage and set organization-level budgets and rate limits.
- The application does not currently implement token counting, cost estimation, streaming, or response caching.
- Rate-limit errors surface as `LLMRateLimitError` after bounded SDK retries; callers should back off or inform the user rather than retrying forever.

## 9. Testing

Run the full suite:

```bash
ai-engine/venv/Scripts/python.exe -m pytest ai-engine/tests -q
```

Unit tests mock all OpenAI responses. They cover request construction, `store=False`, strict schemas, Pydantic validation, malformed outputs, missing configuration, timeout and rate-limit mapping, structured-output fallback, secret redaction, and privacy-safe logging.

The optional live integration test is skipped unless all of these are set:

```bash
export RUN_OPENAI_INTEGRATION_TESTS=1
export OPENAI_API_KEY="<credential>"
export OPENAI_MODEL="<model-supported-by-your-account>"
```

Then run:

```bash
ai-engine/venv/Scripts/python.exe -m pytest ai-engine/tests/test_openai_integration.py -q
```

The live test sends only a synthetic CV and verifies that a validated `ResumeAnalysis` is returned.

## 10. Operational checklist

- [ ] `OPENAI_API_KEY` is stored in a backend secret manager.
- [ ] `OPENAI_MODEL` is explicitly configured and available.
- [ ] `.env` is not tracked by Git.
- [ ] `.env.example` contains placeholders only.
- [ ] User notice and consent cover external AI processing.
- [ ] Retention and deletion requirements are documented.
- [ ] Budgets, usage alerts, and rate limits are configured.
- [ ] Logs are checked for API keys, prompts, CV text, and response bodies.
- [ ] Live integration tests are disabled by default.
- [ ] Human review remains part of consequential decisions.
