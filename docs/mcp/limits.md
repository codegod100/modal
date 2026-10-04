# Limits and testing

## API limits and changes

- The public SDK lists **live apps**, excluding stopped and disabled apps. App
  metadata now includes current lifecycle, functions, and server IDs, but no full
  deployment history. `get_deployment_history` was removed.
- `stop_app`, `list_containers`, and `stop_container` were removed because the
  documented public SDK has no corresponding management methods. Function stats
  still report container counts. Use Modal's dashboard for those management tasks.
- `modal_cli` and the CLI/script adapters were removed entirely.
- `list_sandboxes` filters by live apps in the resolved environment. `sandbox_exec`
  still runs the explicitly requested command inside a remote sandbox via its SDK.
- Storage listings return SDK metadata and default to 100 named objects. Secret
  values are never returned. `create_secret(overwrite=true)` merges supplied keys
  through `Secret.update`; existing unspecified keys remain intact.
- Volume reads return the first `max_bytes` bytes, decoded as UTF-8 with replacement
  for invalid bytes. Limits count bytes, not characters. Billing decimals are
  returned as strings to retain precision.
- A `call_function` timeout stops waiting and does not cancel the remote call.

## Offline verification

From `mcp/`:

```bash
pip install -e '.[dev]'
pytest -q
ruff check .
ruff format --check .
mypy modal_mcp
```

Tests mock SDK calls and assert that no network or local process execution occurs.
They cover the MCP inventory, typed service deployment, SDK call arguments,
read-only enforcement, per-caller client binding, missing hosted credentials, and
OAuth credential propagation. They do not deploy or mutate live Modal resources.
