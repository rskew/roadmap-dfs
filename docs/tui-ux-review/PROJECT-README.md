# gateway

A small API gateway: auth -> rate limit -> cache -> upstream.

Run the tests with `python3 -m pytest -q`.
Config is `gateway.json` (see `gateway.example.json`), overridable by `GATEWAY_<KEY>` env vars.
Three customers still run configs from the 1.x days; ask before changing what a config file may contain.
