# Backends

Every backend is a named entry under `backends` in `~/.wowclaude/config.json`. The window
cycles through the names; a request carries the chosen name; the companion routes it.

## claude-code

Runs the installed Claude Code CLI in print mode inside the chosen project folder:

```
claude -p --output-format stream-json --verbose --permission-mode <mode> --permission-prompts none
       [--max-budget-usd <cap>] [--resume <session id>] [--model <id>] [--allowedTools ...]
       [--append-system-prompt <text>] <extra_args...>
```

The prompt is written to the CLI's standard input, never to its command line.

| field | default | notes |
|---|---|---|
| `claude_bin` | `claude` | name on PATH or an absolute path |
| `permission_mode` | `acceptEdits` | `default`, `acceptEdits`, `plan`, `bypassPermissions`, ...; print mode denies anything that would prompt |
| `allowed_tools` | `[]` | permission rules, e.g. `["Bash(git status *)", "Bash(npm test *)"]` |
| `max_budget_usd` | `5.0` | passed only when an API key is in use |
| `model` | the CLI's default | alias or full id |
| `system_prompt` | none | appended to the CLI's system prompt |
| `extra_args` | `[]` | anything else. `["--bare"]` makes a leaner session, but the CLI then reads no login (API key only) |
| `env` | `{}` | environment variables set for the CLI process; see below |
| `api_key_env` / `api_key_file` | none | when set, the key is exported as `ANTHROPIC_API_KEY` to the child only; otherwise the CLI's own login is used |

The companion removes its own session markers from the child environment so the CLI never
believes it is a nested session.

### Where the CLI finds its login: `env`

A Claude Code login that lives outside the default folder is selected by an environment variable,
`CLAUDE_CONFIG_DIR` or `CLAUDE_SECURESTORAGE_CONFIG_DIR`. Your terminal sets it, so `claude` works
there. A companion started from a different shell (another terminal, a shortcut, a startup item)
does not have it, so the CLI does not find the login and the turn fails with an authentication
error. The `env` option closes that gap: it is a map of environment variables that the companion
sets for the CLI process, on top of its own environment. It applies to the turns and to the
`claude --version` and `claude auth status` calls that `doctor` makes.

```
python -m wowclaude config set backends.claude-code.env.CLAUDE_CONFIG_DIR "~/path/to/claude-login-folder"
```

- Set one variable per command, as above: a plain quoted value survives every shell's quoting,
  which a JSON object on the command line does not.
- Values may use `~` and environment variables; the companion expands them when it starts the CLI.
- `init` saves `CLAUDE_CONFIG_DIR` and `CLAUDE_SECURESTORAGE_CONFIG_DIR` into every `claude-code`
  backend when the shell it runs in has them, and prints which ones. It does not overwrite a value
  already in `env`.
- `doctor` lists the names set in `env`. When its shell sets one of the two variables and the
  config does not have it, it prints the note and one `config set` command per variable to save it.
- Names that look like credentials (they contain `auth`, `key`, `token`, `secret` or `cookie`,
  in any case) are refused, and so are values that carry a credential whatever the name: a
  password inside a URL (`http://user:password@host`) or authorization header text. `config set`
  saves nothing and says why without repeating the value. `doctor` reports such an entry in a
  hand-edited file as a config problem, and `config show` and `config get` mask it. Set a variable
  like that in the shell that starts the companion instead. Keys still go in `api_key_env` or
  `api_key_file` only.
- `env` must be a map of names to text. Anything else in a hand-edited file is reported by
  `doctor` and ignored when the CLI is started.

### Checking that the CLI answers: `doctor --live`

`python -m wowclaude doctor` shows how the CLI will authenticate, but `claude auth status` can
report a login whose session has expired. `python -m wowclaude doctor --live` also runs one tiny
real turn ("Reply with the single word: ok") through each `claude-code` backend, built exactly as
the companion builds a turn, with the same `env`. The turn runs in your default project folder, or
in an empty temporary folder when there is none, never in whatever folder the terminal is in. It
prints `live turn: ok, answered ...` or `live turn: FAILED: <error>`, and a failure counts as a
problem, so `doctor` exits with a non-zero code. The turn uses a little of your Claude usage; with
an API key in use it costs a very small amount.

Stop terminates the child process. The in-flight turn ends without a result; the session stays
resumable, so the next prompt on the same session continues from the last completed turn.

A turn is finished when the CLI prints its result. The CLI process can keep running after that:
end-of-session hooks, for example a memory sync, can take half a minute. The companion does not
hold the answer back for them. The process finishes on its own, Stop no longer touches it, the
next turn starts only when it has exited (or after two minutes), and when the companion exits it
waits up to 90 seconds for it.

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
