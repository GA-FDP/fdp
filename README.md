# fdp

## Authentication

`fdp login` mints a SciToken via the pelican GitHub-OAuth flow and caches it
under `~/.fdp/cache/<device>.token`. `fdp run ...` auto-acquires a token when
none is valid and the session is interactive; set `FDP_NO_AUTO_LOGIN=1` to
disable that (e.g. in batch jobs). `fdp logout` deletes the cached token.
`fdp env` never launches the flow. Resolution order: `-t/--bearer-token`,
then `$BEARER_TOKEN`, then the managed cache, then the legacy `~/.fdp/token`.
