# Provider setup (fork branch `deploy/robweb-20261023`)

Nerve's plugin manifest is `0.3.2.dev0` on the `v0.3.1` release tree. The provider client keeps one decision contract across the supported transports.

## OpenRouter

- Credential: `OPENROUTER_API_KEY`
- Endpoint: `https://openrouter.ai/api/alpha/decisions`
- Default model: `typesafe/jev-1.13`
- Provenance transport: `openrouter-decisions`

```bash
export OPENROUTER_API_KEY='...'
hermes config set plugins.entries.nerve.settings.jev_provider openrouter --force
```

## TypeSafe direct

- Credential: `TYPESAFE_API_KEY`
- Endpoint: `https://api.typesafe.ai/v1/systemone`
- Default model: `jev-latest`
- Provenance transport: `typesafe-system-one`

```bash
export TYPESAFE_API_KEY='...'
hermes config set plugins.entries.nerve.settings.jev_provider typesafe --force
hermes config set plugins.entries.nerve.settings.typesafe_model jev-latest --force
```

When `jev_provider` is unset, TypeSafe is selected automatically if `TYPESAFE_API_KEY` is the only configured provider key. If both OpenRouter and TypeSafe keys are available, OpenRouter remains the default; an explicit `jev_provider` setting always wins. If OpenRouter returns HTTP 402 and a TypeSafe key is available, the error includes a hint to set `jev_provider: typesafe` for direct TypeSafe billing.

Credentials resolve through the Hermes per-profile secret scope first and the process environment second. Keep the key in Hermes' secret store or a profile `.env`; never place it in `config.yaml`, prompts, or documentation.

## OpenCode Zen

- Credential: `OPENCODE_API_KEY`
- Endpoint: `https://opencode.ai/zen/v1/systemone`
- Supported model: `jev-1.13` (paid only)
- Provenance transport: `opencode-zen-system-one`

```bash
export OPENCODE_API_KEY='...'
hermes config set plugins.entries.nerve.settings.jev_provider opencode --force
hermes config set plugins.entries.nerve.settings.opencode_model jev-1.13 --force
```

Nerve rejects `jev-1.13-free` for the OpenCode provider because that tier does not work with Hermes.

Only the selected provider's credential is required. OpenCode is implemented as a native System One transport, not an OpenAI-compatible chat endpoint.

## Timeouts, circuit breaker, and failure categories

All transports share one client with these `plugins.entries.nerve.settings` keys:

| Setting | Default | Meaning |
|---|---:|---|
| `timeout_seconds` | `10.0` | Per-request provider timeout. A timed-out request counts as a connection failure. |
| `breaker_failure_threshold` | `2` | Consecutive transport failures (DNS/connect, timeout, TLS/reset, truncated response, OS/socket errors) before the breaker opens. |
| `breaker_cooldown_seconds` | `60.0` | While open, provider calls fail fast with `circuit-open` instead of waiting on the network. The breaker resets after the cooldown elapses and a call succeeds. |

HTTP status errors (for example `402` or `429`) and malformed provider responses do **not** trip the breaker: the provider answered, so retrying immediately is not harmful. Unexpected plugin exceptions are reported as `jev-error` and never count as outages.

Every provider failure is reduced to one fixed, privacy-safe category before it reaches events, metrics, or `nervous.report()`:

| Category | Meaning |
|---|---|
| `circuit-open` | Breaker open; call skipped. |
| `timeout` | Request exceeded `timeout_seconds`. |
| `connection` | DNS, connect, TLS, reset, or truncated transport. |
| `http-<status>` | Provider returned an HTTP error status (e.g. `http-402`). |
| `transport-error` | Other `urllib`/socket/OS failure. |
| `invalid-response` | Provider answered, but the body was not a valid decision. |
| `no-provider` | No usable credential/provider configured. |
| `jev-error` | Client/plugin error unrelated to the network. |
| `other` | Anything else. |

Categories never carry response bodies, prompts, request data, URLs, or exception text. In every nervous-system mode a provider failure falls back to normal Hermes behaviour; in `shadow` and `advisory` surfaces nothing is blocked. See [`NERVOUS_SYSTEM.md`](NERVOUS_SYSTEM.md) for the admission floor that keeps risky turns at `WATCH` during an outage, and the gate rule `nerve:provider-unavailable` that applies only in `enforce`/`ask-only` gate modes.

This is a source-repository development build. `packaging/hermes-catalog/jev.yaml` intentionally remains on v0.2.1.2 until a later reviewed stable release.
