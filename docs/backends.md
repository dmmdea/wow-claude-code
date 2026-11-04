# Backends

Every backend is a named entry under `backends` in `~/.wowclaude/config.json`. The window
cycles through the names; a request carries the chosen name; the companion routes it.

## claude-code

Runs the installed Claude Code CLI in print mode inside the chosen project folder:

```
claude -p --output-format stream-json --verbose --permission-mode <mode> --permission-prompts none
       [--max-budget-usd <cap>] [--resume <session id>] [--model <id>] [--allowedTools ...]
       [--append-system-prompt <text>] <extra_args...> "<prompt>"
```

| field | default | notes |
|---|---|---|
| `claude_bin` | `claude` | name on PATH or an absolute path |
| `permission_mode` | `acceptEdits` | `default`, `acceptEdits`, `plan`, `bypassPermissions`, ...; print mode denies anything that would prompt |
| `allowed_tools` | `[]` | permission rules, e.g. `["Bash(git status *)", "Bash(npm test *)"]` |
| `max_budget_usd` | `5.0` | passed only when an API key is in use |
| `model` | the CLI's default | alias or full id |
| `system_prompt` | none | appended to the CLI's system prompt |
| `extra_args` | `[]` | anything else, e.g. `["--bare"]` |
| `api_key_env` / `api_key_file` | none | when set, the key is exported as `ANTHROPIC_API_KEY` to the child only; otherwise the CLI's own login is used |

The companion removes its own session markers from the child environment so the CLI never
believes it is a nested session.

Stop terminates the child process. The in-flight turn ends without a result; the session stays
resumable, so the next prompt on the same session continues from the last completed turn.

The companion never logs you in and never touches your Claude credentials: it runs your own
installed CLI, which authenticates the way you set it up (`claude auth status` shows which).
Anthropic requires API keys for products built on Claude Code; if you build a hosted service
on top of this companion, configure `api_key_env` rather than relying on a subscription login.

## openai

Any OpenAI-compatible chat completions endpoint, streamed with server-sent events. Works with
hosted routers and with local servers (llama.cpp's server, vLLM, Ollama, LM Studio, llama-swap).

```json
{
  "type": "openai",
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "local-model",
  "api_key_env": null,
  "system_prompt": null,
  "max_history_messages": 40,
  "max_tokens": 4096,
  "temperature": null,
  "extra_headers": {},
  "timeout_sec": 600
}
```

`stream_options.include_usage` is requested so token counts show in the result line; a server
that rejects the field with HTTP 400 is retried without it. A `cost` field in `usage` (some
routers send one) becomes the session's cost.

## anthropic

The Anthropic Messages API directly, streamed.

```json
{
  "type": "anthropic",
  "model": "<a Claude model id>",
  "api_key_env": "ANTHROPIC_API_KEY",
  "max_tokens": 4096,
  "max_history_messages": 40
}
```

`base_url` overrides the API host for proxies.

## The system prompt for chat backends

Unless `system_prompt` is set, chat backends get a short default asking for concise plain
text, because the answer is read in a narrow in-game text box.
