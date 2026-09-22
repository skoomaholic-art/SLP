# OpenRouter

SLP has an optional reusable OpenRouter client in `services/openrouter_client.py`.

## Configuration

Keep the real API key in the deployment secret store, not in Git:

```bash
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=~openai/gpt-latest
OPENROUTER_SITE_URL=
OPENROUTER_APP_TITLE=SLP
```

Example:

```python
from services.openrouter_client import OpenRouterClient

client = OpenRouterClient.from_env()
text = await client.ask(
    "Summarize parser incidents.",
    system="Use only the supplied parser evidence.",
)
```

The parser/QA pipeline remains deterministic. OpenRouter is deliberately not used to decide whether a broadcast is LIVE, change event times, or bypass existing QA rules.
