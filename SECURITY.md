# Security

Do not post API keys, confidential documents, provider request bodies, `.env` files or unredacted translation reports in public issues.

Report a suspected vulnerability through [GitHub private vulnerability reporting](https://github.com/grebocheck/FormaTranslate/security/advisories/new). Include the affected version, platform, a minimal synthetic reproducer and impact. For ordinary bugs, use a public issue with non-sensitive examples.

Security fixes target the latest published version. FormaTranslate sends document text to the API endpoint selected by the user. Translation caches, reports and review drafts may contain document text; users control their retention and access. API keys are stored in the OS vault when enabled, or remain session-only. No secret belongs in a release artifact.
