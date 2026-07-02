# Command-Line Interface (`kubepilot`)

> Phase 2. Run investigations from a terminal or a CI job. The `kubepilot` CLI is
> a thin [Typer](https://typer.tiangolo.com/) client over the same gateway REST
> API the Web UI and Slack bot use (`POST /investigations`,
> `GET /investigations/{id}`). `--output json` and meaningful exit codes make it
> scriptable.

---

## 1. Install

The CLI is the `kubepilot-cli` package (`services/cli`), exposing a `kubepilot`
console script (`[project.scripts]` → `kubepilot_cli.main:app`).

```bash
# From the repo (dev): install the CLI package into the workspace venv
uv pip install ./services/cli
# or run it without installing:
uv run --package kubepilot-cli kubepilot --help

# Standalone (once published):
pip install kubepilot-cli
```

After install, `kubepilot --help` lists the commands.

---

## 2. Configuration

The CLI reads two settings; resolution order is **highest first**: `KUBEPILOT_*`
env vars → `.env` → `~/.kubepilot/config.toml`.

| Setting | Env var | TOML key | Default |
|---|---|---|---|
| API gateway URL | `KUBEPILOT_API_URL` | `api_url` | `http://localhost:8080` |
| API key | `KUBEPILOT_API_KEY` | `api_key` | *(unset — sent as `X-API-Key` only when set)* |

`~/.kubepilot/config.toml`:

```toml
api_url = "https://kubepilot.internal.example.com"
api_key = "..."
```

Or per-shell via env:

```bash
export KUBEPILOT_API_URL=http://localhost:8080
export KUBEPILOT_API_KEY=...
```

The key is sent as the `X-API-Key` header, so the CLI inherits that key's role
and namespace allowlist (Phase 2 light multi-tenancy) — a `viewer` key can `get`
and `list` but is denied `investigate`.

---

## 3. Commands

```text
kubepilot investigate <service> -n <namespace> [-q "..."] [--time-window N]
                                 [--wait/--no-wait] [-o table|json]
kubepilot get <incident_id> [-o table|json]
kubepilot list [--limit N] [-o table|json]
```

### `investigate`

Start an investigation for a service.

| Option | Alias | Default | Meaning |
|---|---|---|---|
| `--namespace` | `-n` | *(required)* | Kubernetes namespace |
| `--query` | `-q` | `why is <service> failing?` | The investigation question |
| `--time-window` | | `30` | Look-back window in minutes |
| `--output` | `-o` | `table` | `table` (RCA report) or `json` |
| `--wait / --no-wait` | | `--wait` | Poll until terminal, then render the report. `--no-wait` returns immediately |

With `--wait` (default) the CLI polls `GET /investigations/{id}` until the status
is terminal, then renders the full RCA report. With `--no-wait` it prints just
the new incident id (or the raw create response with `-o json`) and exits.

### `get`

Fetch and render a single investigation by id — the RCA report (`-o table`) or
the raw snapshot (`-o json`).

### `list`

List recent investigations (`--limit`, default 20) as a table or JSON array.

---

## 4. Examples

Start and wait for a report:

```console
$ kubepilot investigate payment-service -n prod -q "5xx spike after 14:00"
╭─ RCA Report ─────────────────────────────────────────────────────────────────╮
│ Incident 3f9a2c1b  status=completed                                          │
│ Query:     5xx spike after 14:00                                             │
│ Namespace: prod    Service: payment-service                                  │
│                                                                              │
│ Root cause                                                                   │
│   payment-service OOMKilled after the v2.3.1 deploy raised heap usage past   │
│   its 512Mi limit; restarts drove the 5xx spike.                            │
│                                                                              │
│ Category:   resource_exhaustion                                              │
│ Confidence: 88%                                                              │
│                                                                              │
│ Evidence                                                                     │
│   - [critical] kubernetes: 3 OOMKilled restarts in the last 15m             │
│   - [warning]  deployment: v2.3.1 deployed 8m before the incident window    │
│                                                                              │
│ Recommendations                                                              │
│   1. Raise the memory limit or roll back v2.3.1                             │
│      $ kubectl -n prod rollout undo deploy/payment-service                   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Fire-and-forget (just the id):

```console
$ kubepilot investigate checkout-service -n prod --no-wait
3f9a2c1b-7d4e-4a10-9c2f-8b1e6a0f5d21
```

List recent investigations:

```console
$ kubepilot list --limit 5
                              Investigations
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ ID       ┃ Status    ┃ Query                 ┃ Namespace ┃ Created           ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ 3f9a2c1b │ completed │ 5xx spike after 14:00 │ prod      │ 2026-07-02T14:07Z │
└──────────┴───────────┴───────────────────────┴───────────┴───────────────────┘
```

---

## 5. Use in CI

For pipelines, use `--output json` and rely on the exit code.

```bash
kubepilot investigate payment-service -n prod --output json > rca.json
```

**Exit codes:**

| Code | When |
|---|---|
| `0` | The command succeeded (investigation reached `completed`; `get`/`list` returned) |
| `1` | An error: the API was unreachable / returned non-2xx, **or** a waited-on investigation ended in `failed` (the failure message is printed to stderr) |

Because a `failed` investigation exits non-zero, a gating step is just:

```bash
# Fail the pipeline if KubePilot can't clear the service
if ! kubepilot investigate payment-service -n prod -o json > rca.json; then
  echo "KubePilot flagged an unresolved issue:" >&2
  jq -r '.state.rca.root_cause // .error' rca.json >&2
  exit 1
fi
```

Errors always go to **stderr**; JSON output goes to **stdout**, so redirecting
stdout keeps machine-readable output clean.

## Next steps

- [Slack bot](./slack.md) — the same investigations from an incident channel
- [Install](./install.md) — running the gateway and creating the `X-API-Key`
- [Architecture §5](./ARCHITECTURE.md#5-end-to-end-data-flow) — what happens after you hit `investigate`
