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
