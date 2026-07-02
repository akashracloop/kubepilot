# Slack Bot

> Phase 2. Run an incident investigation from Slack: mention `@kubepilot` in an
> incident channel (or use the `/kubepilot` slash command) and the bot triggers
> an investigation through the API gateway and posts back a result card — root
> cause, confidence, and the top recommendations. Built on Slack Bolt in
> **Socket Mode**, so it needs **no public ingress** and no Slack app review for
> internal use.

---

## 1. What it does

```text
@kubepilot why is checkout-service slow in prod?
        │
        ▼  parse → {service, namespace, query}
   Slack bot (Bolt, Socket Mode)
        │  POST /investigations  (X-API-Key)
        ▼
   API gateway → LangGraph investigation
        │  poll GET /investigations/{id} until terminal
        ▼
   result card  ◄── root cause · category · confidence · top 3 recommendations
```

- Handles two triggers: the **`app_mention`** event (`@kubepilot …`) and the
  **`/kubepilot`** slash command. Both run the same flow.
- Acknowledges immediately, posts a ":mag: Investigating `<target>` in
  `<namespace>`…" note, then waits for the investigation to reach a terminal
  status (`completed` / `failed`) and posts a Block Kit result card.
- On timeout or error it posts a friendly message and stays alive — one failed
  investigation never takes the bot down.

The bot is a **thin client over the gateway REST API** (`POST /investigations`,
`GET /investigations/{id}`). It contains no agent logic of its own.

---

## 2. Configuration

All settings use the `KUBEPILOT_SLACK_` env prefix
(`services/slack-bot/src/kubepilot_slack/config.py`):

| Env var | Default | Purpose |
|---|---|---|
| `KUBEPILOT_SLACK_SLACK_BOT_TOKEN` | `""` | Bot token (`xoxb-…`) — Web API calls (posting cards) |
| `KUBEPILOT_SLACK_SLACK_APP_TOKEN` | `""` | App-level token (`xapp-…`) — the Socket Mode connection |
| `KUBEPILOT_SLACK_API_URL` | `http://localhost:8080` | KubePilot API gateway base URL |
| `KUBEPILOT_SLACK_API_KEY` | *(unset)* | Sent as `X-API-Key` to the gateway when set |
| `KUBEPILOT_SLACK_DEFAULT_NAMESPACE` | `prod` | Namespace used when the message doesn't specify one |
| `KUBEPILOT_SLACK_WAIT_TIMEOUT_SECONDS` | `300` | How long to wait for an investigation before giving up |

> The doubled `SLACK_SLACK_` in the token vars is intentional: the `KUBEPILOT_SLACK_`
> prefix is prepended to the `slack_bot_token` / `slack_app_token` field names.

---

## 3. Socket Mode setup

Socket Mode connects outbound to Slack over a WebSocket — no inbound URL, no
ingress. You need **two** tokens: a bot token and an app-level token.

1. Create a Slack app at <https://api.slack.com/apps> → **From an app manifest**
   (paste the manifest in §4 below).
2. Enable **Socket Mode** and generate an **app-level token** (`xapp-…`) with the
   `connections:write` scope → this is `KUBEPILOT_SLACK_SLACK_APP_TOKEN`.
3. Under **OAuth & Permissions**, install the app to your workspace and copy the
   **Bot User OAuth Token** (`xoxb-…`) → this is `KUBEPILOT_SLACK_SLACK_BOT_TOKEN`.
4. Invite the bot to your incident channel: `/invite @kubepilot`.

Run it locally:

```bash
export KUBEPILOT_SLACK_SLACK_BOT_TOKEN=xoxb-...
export KUBEPILOT_SLACK_SLACK_APP_TOKEN=xapp-...
export KUBEPILOT_SLACK_API_URL=http://localhost:8080
export KUBEPILOT_SLACK_API_KEY=...           # the gateway's X-API-Key
uv run --package kubepilot-slack python -m kubepilot_slack.app
```

---

## 4. Slack app manifest sketch

A minimal manifest for the app-mention + slash command + Socket Mode setup:

```yaml
display_information:
  name: KubePilot
features:
  bot_user:
    display_name: kubepilot
    always_online: true
  slash_commands:
    - command: /kubepilot
      description: Investigate a Kubernetes incident
      usage_hint: "why is <service> failing? in <namespace>"
oauth_config:
  scopes:
    bot:
      - app_mentions:read   # receive @kubepilot mentions
      - chat:write          # post the investigating note + result card
      - commands            # register the /kubepilot slash command
settings:
  event_subscriptions:
    bot_events:
      - app_mention
  interactivity:
    is_enabled: true
  socket_mode_enabled: true
```

> Socket Mode delivers events over the WebSocket, so you do **not** set
> `request_url` / `event_subscriptions.request_url` — there is no public
> endpoint to point Slack at.

---

## 5. How the mention is parsed

The parser (`kubepilot_slack/parse.py`) turns free-form text into a
`{query, service, namespace}` request with a small, well-tested heuristic:

- Slack mention tokens (`<@U123>`) and a leading `@kubepilot` are stripped; the
  cleaned text is passed through as the free-form **query**.
- **Service:** the first token matching a k8s-style name — lowercase segments
  joined by at least one hyphen (`payment-service`, `checkout-svc`,
  `api-gateway`). A token ending in `-service` / `-svc` is preferred when several
  qualify.
- **Namespace:** an explicit `namespace <ns>` / `ns <ns>` wins; the looser
  `in <ns>` form is accepted only when the candidate isn't hyphenated (that's
  more likely a service) and isn't a common English word. Otherwise the caller's
  default namespace (`KUBEPILOT_SLACK_DEFAULT_NAMESPACE`) is used.

Examples:

| Message | service | namespace |
|---|---|---|
| `@kubepilot why is payment-service failing?` | `payment-service` | *(default)* |
| `@kubepilot checkout-svc is slow in staging` | `checkout-svc` | `staging` |
| `/kubepilot look at api-gateway namespace payments` | `api-gateway` | `payments` |

---

## 6. Namespace scope & auth

The bot sends the gateway's static `X-API-Key` on every request, so it **inherits
that key's namespace allowlist** (the light multi-tenancy from Phase 2 — a key
scoped to `prod` cannot investigate `staging`, regardless of what the message
asks). Give the bot a key scoped to exactly the namespaces its channel should be
able to investigate.

In the Helm chart the bot reuses the gateway's API-key Secret, so it
automatically shares the gateway's namespace scope:

```yaml
# charts/kubepilot-ai/values.yaml — off by default
slackBot:
  enabled: false
  defaultNamespace: prod
  # Secret with keys `bot_token` (xoxb-...) and `app_token` (xapp-..., Socket Mode)
  tokenSecretRef: kubepilot-slack-tokens
```

Enable it with the token Secret in place:

```bash
kubectl -n kubepilot-system create secret generic kubepilot-slack-tokens \
  --from-literal=bot_token=xoxb-... \
  --from-literal=app_token=xapp-...

helm upgrade --install kubepilot-ai ./charts/kubepilot-ai -n kubepilot-system \
  --set slackBot.enabled=true
```

The bot Deployment runs with the same hardened pod posture as the other services
(`runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities dropped) and, because
of Socket Mode, needs no Service or Ingress.

## Next steps

- [CLI](./cli.md) — the same investigations from a terminal / CI job
- [Install](./install.md) — gateway auth and the `X-API-Key` model
- [Architecture §8](./ARCHITECTURE.md#8-security-model) — auth and namespace scoping
