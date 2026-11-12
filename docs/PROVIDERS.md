# API providers

Forma supports OpenAI-compatible Chat Completions, OpenAI Responses, native Gemini `generateContent` and native Anthropic Claude Messages. These connections work for DOCX/XLSX, quick text and photo translation. PDF uses the same native adapters through Forma's local bridge to a separately installed BabelDOC; Responses mode is still unsupported for PDF. No provider SDK or hosted relay is required.

An external **LM Studio** or **Ollama** server can supply a local model through an OpenAI-compatible `chat` connection. Select the matching local preset, click **Refresh models** and choose an installed model. The presets only fill editable settings: neither server nor translation model is bundled or started by Forma. See the [Ukrainian local AI guide](LOCAL-AI.uk.md) for setup, authentication, structured-output requirements and validation limits.

## Configure in the application

1. Open **Settings** and select a provider preset.
2. Enter an API key if the provider requires one. Local servers without authentication can leave it empty. For a required key, choose whether to store it in the operating system's credential vault or keep it for the session.
3. Check the model, then save. **Refresh models** reads the provider's catalog; **Test connection** makes a small generation request that consumes quota and may be billed.
4. Translate as usual. Photos need a model supporting image input. An optional editor model must belong to the same provider connection.

Presets are editable starting points, not separate accounts or subscriptions. Selecting a preset does not save settings, send requests, create a key or change billing. Changing providers clears a typed key; an already saved key is only reused for the matching endpoint. Switching between Gemini presets keeps access to the same endpoint's saved key.

Connections use the effective API root and protocol family. Chat Completions and Responses share their OpenAI-compatible credential scope; Gemini and Anthropic have separate scopes even on the same host. Native address forms such as Anthropic's root, `/v1` and `/v1/messages`, or Gemini's root and full `models/...:generateContent` address, keep the same saved key. A custom Chat Completions base ending in `/messages` remains a distinct custom path.

Older saved profiles migrate their OS-vault key only when the stored protocol and address prove the matching connection. New protocol selections do not guess the owner of an old untyped vault entry. Existing legacy records are retained; conflicting old keys require the user to enter the intended key rather than choosing one silently.

The desktop reads and writes the OS vault in background tasks. Translating waits for the saved connection to finish loading or saving. A failed save keeps the settings draft and compensates any vault changes if the preference file could not be committed. Concurrent theme and language changes remain saved. Keys never enter preferences, usage reports or Qt signal payloads; an unavailable vault leaves account-specific balance controls disabled until the connection is resolved.

| Preset | Default model | Initial pacing |
| --- | --- | --- |
| OpenAI | Translation: `gpt-6-luna`; editor: `gpt-6.1-terra` | 8 simultaneous requests, 2 documents, no added RPM limit |
| Google Gemini — Free Tier | `gemini-3.5-flash-lite` | 1 simultaneous request, 1 document, 5 requests/minute |
| Google Gemini | `gemini-3.8-flash` | 4 simultaneous requests, 2 documents, no added RPM limit |
| Anthropic Claude | `claude-sonnet-5-5` | 4 simultaneous requests, 2 documents, no added RPM limit |
| LM Studio (local) | Empty: select an installed model; empty editor uses that same model | 1 simultaneous request, 1 document, no added RPM limit |
| Ollama (local) | Empty: select an installed model; empty editor uses that same model | 1 simultaneous request, 1 document, no added RPM limit |
| Custom connection | Your existing/manual configuration | Your existing/manual configuration |

Gemini and Claude default model IDs were checked against official documentation on 2026-11-10. Access can differ by account and change over time. Load models or enter an available model manually; Forma never silently switches to another model or a paid tier.

The OpenAI model IDs were checked against [official GPT-6 guidance](https://developers.openai.com/api/docs/guides/latest-model) on 2026-11-12. Forma uses these defaults for a fresh official OpenAI profile; existing saved model choices are preserved. Explicitly choosing the OpenAI preset reapplies its editable defaults. Account access to those models is not guaranteed: load your model catalog or choose an available model manually.

The local presets use `http://localhost:1234/v1` for LM Studio and `http://localhost:11434/v1` for Ollama, with `chat`, `auto` and both model fields initially empty. They clear stale cloud model names. Start the external server yourself, choose a local model before saving, and provide its token only if authentication is enabled. Host, port and other fields remain editable; **Custom connection** is also available for manual setup.

## Gemini Free Tier

Get a key from [Google AI Studio](https://aistudio.google.com/apikey). Free Tier is a property of your Google project and available models, not a special key format. The [pricing page](https://ai.google.dev/gemini-api/docs/pricing) currently lists free text/image input and output for the preset's Flash-Lite model. A key from a paid project follows that project's billing even if you select the Free Tier preset.

The local 5 RPM setting is a conservative starting point, **not Google's guaranteed quota**. [Google rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) apply per project and model, with independent request-per-minute, token-per-minute and daily limits. Other apps using the project also consume those limits. Check the live quota in AI Studio and lower the setting if needed. Editorial and terminology passes, retries and connection tests also consume requests. Large documents can exhaust Free Tier quickly.

Forma spaces generation requests, honors retry delays and stops on identifiable exhausted daily/zero quota responses. It cannot reserve Google's quota or bypass limits. You can cancel while waiting. A quota error never causes automatic paid fallback.

Google says free-service content may be used to improve its products; paid-service treatment differs. Review the [Gemini API terms](https://ai.google.dev/gemini-api/terms) for your project's applicable data handling before sending confidential documents or images.

## Anthropic Claude

Use a key from the [Claude Console](https://platform.claude.com/). The preset uses the native Messages endpoint at `https://api.anthropic.com/v1`, with API version `2026-10-21`. Available models can be read from the native catalog. Your API account controls access, credits and billing.

Forma handles native text/image blocks and structured JSON output, and rejects truncated/refused replies. Where schema constraints are unsupported by the service, the request retains them as instructions and Forma's translation validators still check the returned content. See the [Messages reference](https://platform.claude.com/docs/en/api/http/messages), [model overview](https://platform.claude.com/docs/en/models/overview) and [API migration guide](https://platform.claude.com/docs/en/api/migration-2026-10-21).

## CLI and environment variables

The packaged GUI needs no `.env`. Existing `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` names remain supported for all modes. Set `FORMA_API_MODE=gemini` or `anthropic` for native protocols. `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and `ANTHROPIC_API_KEY` are also recognized for the corresponding mode. If `OPENAI_BASE_URL` is omitted, native modes use their official default endpoint. Saved GUI settings take precedence unless you explicitly select a CLI `--env` file.

`FORMA_REQUESTS_PER_MINUTE=5` enables local pacing; `0` leaves rate scheduling to the provider. It is independent of `FORMA_MAX_PARALLEL_REQUESTS` and `FORMA_PARALLEL_DOCUMENTS`.

## Validation limits

Offline tests use synthetic content and fake/local HTTP servers to verify both native protocols, images, model pagination, token accounting, quota handling, cancellation, key separation and the PDF bridge. They cannot establish a real account's model availability or translation quality. No Gemini or Anthropic generation was performed for this change without a configured key for that provider.
