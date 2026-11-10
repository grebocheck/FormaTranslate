# Costs and balance

Click **Costs** in the top bar. The panel shows estimated spending today and over the last 30 calendar days, request counts and input/output tokens. It refreshes every five seconds while the window is visible.

Only requests made by **this Forma installation with the current server and API key** are included. Recording begins with version 0.4.0; old translation reports are not imported. Changing a model keeps the account totals; changing a key or server selects a separate account. Rotated keys cannot be identified as belonging to the same provider project.

## What is counted

Office documents, quick text/photo translation, editorial passes, PDF requests through Forma's BabelDOC bridge and connection tests all use the same local ledger. Each actual upstream attempt is recorded once, including retries and responses rejected by local validation. Reusing a local translation cache makes no API request and adds no tokens. Interrupted or failed network requests without usage are shown as unpriced: the provider may have charged for them.

A missing usage field is not treated as zero. Unpriced requests are excluded from the displayed cost subtotal and clearly counted beside it. Rejected HTTP requests such as 401 or 429 without usage count as attempts but add no estimated inference cost. A storage failure does not stop translation; the panel reports unavailable or incomplete statistics. Force-closing the application or persistent storage failures can lose local accounting data.

## Estimates and rates

Automatic rates apply only to the official `https://api.openai.com/v1` endpoint, recognized model names (including dated snapshots), and standard processing. The bundled price snapshot was checked on **2026-11-10** against [OpenAI pricing](https://developers.openai.com/api/docs/pricing). Supported long-context pricing is applied per request. Explicit promotional expiry dates disable an expired rate. Other provider/model combinations and nonstandard service tiers remain unpriced unless you enter a custom rate.

Cached input is discounted separately. Reasoning tokens already included in the reported output count are not charged twice. Gemini's separately reported thought tokens are included in its output total. Rates and estimates exclude taxes, regional surcharges and separate cache-write fees; billed amounts can therefore be higher. Estimates are not invoices, spending limits or a promise of free service. Rates can change after the bundled snapshot.

**Custom model rates** accepts input, cached-input and output prices in USD per million tokens. Enter all three numbers, including an explicit zero when appropriate. They apply only to future requests using the currently configured model, key and server. Clear all three fields to restore automatic pricing. Historical estimates retain their original tariff. A separate editorial model needs its own tariff if it has no automatic price. For custom tariffs, include any service-tier premiums in the prices you enter; cache-write fees still are not included.

## OpenAI balance

A regular OpenAI project key has no documented endpoint for the remaining credit balance. **Open OpenAI billing** opens the official [billing overview](https://platform.openai.com/settings/organization/billing/overview), where you can see the actual balance for the organization selected in your browser. It may differ from the organization associated with your application key.

The OpenAI organization Costs/Usage APIs require a separate Admin Key and report usage/costs, not remaining credits. Forma does not request an Admin Key or use private billing endpoints. See the official [Usage and Costs API example](https://developers.openai.com/cookbook/examples/completions_usage_api) and [credit balance guidance](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

Optionally enter your current balance manually. **Estimated remaining balance** subtracts subsequently recorded Forma costs from that amount. This is a local estimate, not a balance fetched from OpenAI. It excludes other applications, top-ups, credit expiry and unreported charges. Unpriced requests after the baseline hide the numerical remaining estimate. Enter an updated balance to start a new baseline; leave the field blank to remove it. A negative estimate is shown rather than silently clamped to zero.

## Local data

`usage.sqlite3` in Forma's application-data directory contains timestamps, hashed account identifiers, model names, HTTP statuses, token counts, historical price sources, estimated costs, manual tariffs and optional balance baselines. It contains no API keys, server URLs, prompts, translated text, images, filenames or raw provider responses. There is no telemetry or background billing API call. The database is not included in releases or document reports.
