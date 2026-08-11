# fdp

## Authentication

`fdp login` mints a SciToken via the pelican GitHub-OAuth flow and caches it
under `~/.fdp/cache/<device>.token`. `fdp run ...` auto-acquires a token when
none is valid and the session is interactive; set `FDP_NO_AUTO_LOGIN=1` to
disable that (e.g. in batch jobs). `fdp logout` deletes the cached token.
`fdp env` never launches the flow. Resolution order: `-t/--bearer-token`,
then `$BEARER_TOKEN`, then the managed cache, then the legacy `~/.fdp/token`.

## Devices

`fdp` discovers devices (tokamaks) from installed packages: `toksearch_d3d`
contributes `d3d`, `toksearch_mast` contributes `mast`.

Most commands need no device selection:

- `fdp env` / `fdp run` compose the environments of **all** installed devices.
  Their variables are disjoint, so the union is well-defined. If two devices
  ever set the same variable to different values, `fdp` reports the conflicting
  variable and asks you to choose.
- `fdp ls` uses the device that has an origin server.
- `fdp login` / `fdp logout` use the device that requires a bearer token.

To choose explicitly, in increasing order of persistence:

```bash
fdp --device d3d ls /archives       # one invocation (also: fdp ls -D d3d)
export FDP_DEFAULT_DEVICE=d3d       # one shell
fdp device use d3d                  # recorded in ~/.fdp/config.toml
fdp device use --clear              # undo
```

`fdp device list` shows each installed device and its capabilities.

## AI assistant

`fdp` fronts the TokSearch LLM agent (`toksearch.llm`), which turns
plain-language requests into executable TokSearch pipelines and runs them
against the FDP stack:

```bash
fdp query "Fetch ip for shot 200000 and report the peak current in MA."
fdp chat                # interactive terminal session
fdp chat --gui          # local Gradio GUI (--no-browser to skip the tab)
fdp backends            # list available LLM backends/presets
```

Both accept `--backend`, `--model`, and `-n/--max-iterations`. Backend
resolution: `--backend` → `$FDP_LLM_BACKEND` → `~/.fdp/config.toml
[llm].backend` → built-in `anthropic` (needs `ANTHROPIC_API_KEY`). Other
backends: `openai` (`OPENAI_API_KEY`), `claude-max` (Claude Max plan via
`claude login`, no API key), and site presets like `amsc` (key in
`~/amsc_api_key`), contributed by device packages.

The agent's documentation library also serves your coding assistant:

```bash
fdp skills list                       # available skills + install status
fdp skills install                    # → ~/.claude/skills (Claude Code)
fdp skills install --backend cursor   # or codex; --force to overwrite
```

MCP-capable agents can consume the same skills from the standalone server
(toksearch >= 2.8.2): `claude mcp add toksearch-skills -- fdp run python -m
toksearch.llm.mcp`.

Full reference: <https://ga-fdp.github.io/toksearch/latest/llm/> and the
tutorial at <https://ga-fdp.github.io/toksearch/latest/LLM_Tutorial/>.
