"""Model choices for an unconfigured official OpenAI connection only."""

from forma.core.models import FormaError
from forma.settings.connection import connection_identity

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_TRANSLATION_MODEL = "gpt-6-luna"
OPENAI_REVIEW_MODEL = "gpt-6.1-terra"


def official_openai_profile(base_url: str, api_mode: str) -> bool:
    if api_mode not in {"chat", "responses"}:
        return False
    try:
        return connection_identity(base_url, api_mode).root == OPENAI_BASE_URL
    except FormaError:
        # An incomplete saved connection must remain editable at startup.
        return False
