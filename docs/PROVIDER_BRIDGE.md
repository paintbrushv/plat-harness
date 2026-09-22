# Provider Bridge (Task 5.3)

`plat_harness.adapters.provider_bridge` — one internal request/response
contract with transport adapters for Anthropic-compatible cloud,
OpenAI-compatible cloud and an explicitly configured local loopback
(vLLM/Ollama) endpoint. `NullModel` remains the offline default; the bridge
never replaces native inference plumbing and never starts a local server.

---

## Contract

**Routing.** Model IDs are canonical `vendor/model` identifiers
(lowercase alphanumeric segments, `-`/`.`/`_` in the model part, exactly one
slash). A `RoutingTable` binds each canonical id to a registered
`ProviderSpec` plus that provider's exact wire model id. Registry and
routing lookups return isolated deep copies — mutating one result never
leaks into later loads.

**Providers.** `ProviderSpec` kinds:

| kind | origin policy | credentials |
| --- | --- | --- |
| `anthropic` | pinned `https://api.anthropic.com` only | required, from a named environment variable |
| `openai` | pinned `https://api.openai.com` only | required, from a named environment variable |
| `local-openai` | literal loopback `http://IP:port` only — plain http, explicit port, no path/query/credentials | none |

Arbitrary base URLs are refused (`ENDPOINT_NOT_ALLOWED` /
`ENDPOINT_NOT_LOCAL`); cloud metadata IPs, hostnames like `localhost`, and
private non-loopback addresses are all refused — private IP alone is not
proof of ownership, matching the frozen `local_model` policy.

**Capabilities.** `tool_calls` and `structured_output` are declared per
provider. Requests needing an undeclared capability fail
`PROVIDER_UNSUPPORTED_CAPABILITY` *before any bytes are sent*. Streaming is
not part of the contract (`PROVIDER_STREAM_UNSUPPORTED`); a live smoke test
is separately authorized and starts from synthetic data.

**Single internal contract.** `ProviderRequest` (internal messages/tools
tuple, optional `response_format`, bounded `max_tokens`) → wire payload via
`AnthropicTranslator` or `OpenAITranslator` → canonical `ModelTurn`
(`models.py` shape: content, validated tool-call tuples, canonical model id,
finish reason, usage). Tool-call responses reuse the frozen
`local_model.validate_call`/`strict_json` guards: duplicate JSON keys,
non-finite values and malformed wire JSON fail `INVALID_MODEL_JSON`;
malformed tool calls fail `INVALID_TOOL_CALL`; truncated responses
(`finish_reason` not `stop`/`tool_calls`) fail `MODEL_INCOMPLETE`.

**Wire shapes.**

- Anthropic: `POST /v1/messages`, `system` hoisted out of messages, tools as
  `{name, description, input_schema}`, `tool_calls` → `tool_use` blocks,
  `tool` role → `user` + `tool_result` blocks, `x-api-key` +
  `anthropic-version` headers.
- OpenAI (cloud and local): `POST /v1/chat/completions`, standard function
  tool objects (`parameters`, never `input_schema`), `Authorization:
  Bearer` header for cloud only.

**Lifecycle.** All bytes move through an injected transport — the bridge
performs no network I/O itself, so tests run on recorded synthetic
responses with no credentials and no live providers.

- Missing cloud credentials fail `PROVIDER_CREDENTIAL_MISSING` before send.
- `BoundedRetryPolicy` (≤5 retries, exponential backoff with cap) applies
  only to transient statuses (408/429/5xx) and transport errors. 401/403 →
  `PROVIDER_AUTH`; other 4xx → `PROVIDER_BAD_REQUEST`; exhaustion of the
  rate-limit retry bound → `PROVIDER_RATE_LIMIT`; timeouts →
  `PROVIDER_TIMEOUT`; cooperative `CancelFlag` → `PROVIDER_CANCELLED`.
- Oversized requests fail `CONTEXT_OVERFLOW` before send; a provider-reported
  context-length error maps to the same code.
- **No implicit local-to-cloud fallback.** A local endpoint failure raises
  `LOCAL_ENDPOINT_ERROR` (and local transport failures never retry past the
  policy); no cloud provider is ever contacted as a result. A bridge with
  no routing keeps the offline refusal (`NO_MODEL_CONFIGURED`).

**Discovery, not history.** `discover_models(transport, spec)` lists the
ids a provider *currently serves* (`GET /v1/models`); never hardcode
historical vendor model names as available. `verify_model_available`
refuses a route whose wire model is not in the live listing
(`MODEL_NOT_SERVED`).

**Log hygiene.** Bridge events record bounded metadata only —
`provider_request` (provider id, canonical model id, path, request byte
count, request SHA-256) and `provider_response` (status, finish reason,
usage). No raw source text, response bodies, headers or credentials ever
enter events or exception messages/details; status errors are mapped by
numeric code, and transport errors record only the exception type name.

## Boundaries

- Cloud SDKs are optional extras; the core contract is SDK-free.
- The local adapter does not start a server and adds no loopback beyond the
  explicit, validated origin.
- Provider/model labels never alter canonical economics (see
  `docs/INGEST_CANONICAL_INTAKE.md`); the bridge is a transport, not an
  authority.
- Live smoke tests require separate authorization and start from synthetic
  data.

## Test target

`tests/test_provider_bridge.py` — synthetic fakes and recorded responses
only, no live credentials.