"""Named backend presets for ``wowclaude init --backend <preset>`` and
``wowclaude config add-backend``. Each is a plain dict that ``config.from_dict``
understands; the model ids are placeholders the user replaces with the one they
want (the hosted router's catalogue changes often).
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "openrouter": {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "your-model-id:free",
        "api_key_env": "OPENROUTER_API_KEY",
        "extra_headers": {"X-Title": "wow-claude-code"},
    },
}
