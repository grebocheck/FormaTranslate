# Costs and balance

Click **Costs** in the top bar. The panel shows estimated spending today and over the last 30 calendar days, request counts and input/output tokens. Expand **Details by model** to see model totals and cache reads/writes. Small spending and balance changes remain visible with up to six decimal places. It refreshes every five seconds while the window is visible.

Only requests made by **this Forma installation with the current server and API key** are included. Recording begins with version 0.4.0; old translation reports are not imported. Changing a model keeps the account totals; changing a key or server selects a separate account. Rotated keys cannot be identified as belonging to the same provider project.

Equivalent address spellings use the same effective connection: for example, Anthropic `/v1` and `/v1/messages` retain their statistics and balance baseline. Protocol families and different keys remain separate. For an older saved profile, Forma registers hashed legacy account aliases without rewriting historical requests; each original request is counted once. Historical records from an unconfigured profile are retained rather than assigned by guessing. If several legacy balance baselines exist, the latest timestamp wins; a tie favors the current account deterministically. A newly entered tariff takes priority. Conflicting old tariffs without timestamps remain unset until the user chooses a rate, so future cost estimates are not invented.

## What is counted

Office documents, quick text/photo translation, editorial passes, PDF requests through Forma's BabelDOC bridge and connection tests all use the same local ledger. Each actual upstream attempt is recorded once, including retries and responses rejected by local validation. Reusing a local translation cache makes no API request and adds no tokens. Interrupted or failed network requests without usage are shown as unpriced: the provider may have charged for them.

A missing usage field is not treated as zero. Unpriced requests are excluded from the displayed cost subtotal and clearly counted beside it. Rejected HTTP requests such as 401 or 429 without usage count as attempts but add no estimated inference cost. A storage failure does not stop translation; the panel reports unavailable or incomplete statistics. Force-closing the application or persistent storage failures can lose local accounting data.

## Estimates and rates

Automatic rates apply only to the official `https://api.openai.com/v1` endpoint, recognized model names (including dated snapshots), and standard processing. The bundled price snapshot was checked on **2026-11-10** against [OpenAI pricing](https://developers.openai.com/api/docs/pricing). Supported long-context pricing is applied per request. Explicit promotional expiry dates disable an expired rate. Other provider/model combinations and nonstandard service tiers remain unpriced unless you enter a custom rate.

Cached reads and writes are counted separately using the API response. Reasoning tokens already included in the reported output count are not charged twice. Gemini's separately reported thought tokens are included in its output total. Rates and estimates exclude taxes and regional surcharges; billed amounts can therefore be higher. Estimates are not invoices, spending limits or a promise of free service. Rates can change after the bundled snapshot.

**Custom model rates** accepts input, cached-input and output prices in USD per million tokens. Enter all three numbers, including an explicit zero when appropriate. They apply only to future requests using the currently configured model, key and server. Clear all three fields to restore automatic pricing. Historical estimates retain their original tariff. A separate editorial model needs its own tariff if it has no automatic price. For custom tariffs, include any service-tier premiums in the prices you enter; recognized OpenAI cache-write models use the documented 30-minute/12-hour multipliers on your input rate. Reported cache writes for other providers remain unpriced: three custom rates do not establish a separate cache-write tariff.

### Cache-write correction in 0.5.0

Version 0.4.0 counted cache writes as ordinary input. GPT-5.6 and GPT-6 models charge a higher rate for those tokens. Forma now uses disjoint token buckets:

`cost = (input - cached_reads - cache_writes) × input_rate + cached_reads × cached_rate + writes_30m × input_rate × 1.25 + writes_12h × input_rate × 2.5 + output × output_rate`

Divide by one million for rates quoted per million tokens. The write rate **replaces** the ordinary input rate. Long-context adjustments apply to the applicable automatic rates. Forma requests the default 30-minute lifetime; explicit API lifetime breakdowns take precedence. See [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching#monitor-cache-performance).

A small live GPT-6.1 Terra check on 2026-11-10 reported 1,502 input tokens, 1,499 writes and 14 output tokens: **$0.00194675**, compared with **$0.001572** under the old formula. Repeating the same request reported 1,499 cache reads and zero writes: **$0.00014795**. These are tariff calculations from actual response metadata, not an invoice reconciliation. Offline regressions cover both responses, Chat/Responses formats, mixed lifetimes, long context, missing data and concurrent database migration.

The database upgrade preserves past totals, tariffs and balance baselines. Old records did not store write counts, so their exact corrected costs cannot be recovered locally. Affected automatic estimates are marked as lower bounds (`≥`); old custom-rate records remain approximate (`≈`). New responses missing required write details are also marked incomplete. Unknown write counts display `—`, not zero. A balance estimate is hidden when any affected request follows its baseline; entering the actual current balance starts a fresh baseline.

## OpenAI balance

A regular OpenAI project key has no documented endpoint for the remaining credit balance. **Open OpenAI billing** opens the official [billing overview](https://platform.openai.com/settings/organization/billing/overview), where you can see the actual balance for the organization selected in your browser. It may differ from the organization associated with your application key.

The OpenAI organization Costs/Usage APIs require a separate Admin Key and report usage/costs, not remaining credits. Forma does not request an Admin Key or use private billing endpoints. See the official [Usage and Costs API example](https://developers.openai.com/cookbook/examples/completions_usage_api) and [credit balance guidance](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

Optionally enter your current balance manually. **Estimated remaining balance** subtracts subsequently recorded Forma costs from that amount. This is a local estimate, not a balance fetched from OpenAI. It excludes other applications, top-ups, credit expiry and unreported charges. Unpriced or incomplete requests after the baseline hide the numerical remaining estimate. Enter an updated balance to start a new baseline; leave the field blank to remove it. A negative estimate is shown rather than silently clamped to zero.

## Local data

`usage.sqlite3` in Forma's application-data directory contains timestamps, hashed account identifiers, model names, HTTP statuses, token counts (including cache writes and lifetime detail), recognized service tiers, completeness flags, historical price sources, estimated costs, manual tariffs and optional balance baselines. It contains no API keys, server URLs, prompts, translated text, images, filenames or raw provider responses. There is no telemetry or background billing API call. The database is not included in releases or document reports.
