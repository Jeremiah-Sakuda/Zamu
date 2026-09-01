# Deploying Zamu

Zamu runs in three shapes, and none of them is a special case: the same code, with
different adapters, chosen from the environment.

| | Store | Delivery | Model |
|---|---|---|---|
| **Laptop** | SQLite | outbox you can read in the console | deterministic planner |
| **Small server** | SQLite | SES | Bedrock |
| **AWS** | DynamoDB | SES | Bedrock, on AgentCore |

The defaults are the laptop ones on purpose. A system whose default configuration
requires credentials cannot be run by somebody evaluating it.

---

## Configuration

Everything is read once, at the edge, by `zamu/config.py`.

| Variable | Default | What it does |
|---|---|---|
| `ZAMU_STORE` | `sqlite` | `sqlite`, `dynamodb`, or `memory`. All three implement one protocol and are tested against each other. |
| `ZAMU_DB` | `.zamu/zamu.sqlite` | SQLite file path. |
| `ZAMU_DYNAMO_TABLE` | `zamu` | Table name. Created on first use if absent. |
| `ZAMU_BASE_URL` | `http://localhost:8000` | Public URL of the service. The accept and decline links in emails are built from it, so getting this wrong is the most likely cause of a dead link. |
| `ZAMU_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated origins allowed to call the API. |
| `ZAMU_SES_SENDER` | *(unset)* | A verified SES identity. Setting it switches delivery from the outbox to real email. |
| `ZAMU_SES_CONFIGURATION_SET` | *(unset)* | Optional SES configuration set, for bounce and complaint tracking. |
| `ZAMU_MODEL_ID` | Haiku 4.5 | Bedrock model for routine interpretation. |
| `ZAMU_FORCE_PLANNER` | *(unset)* | `1` to use the deterministic planner even where Bedrock is available. Useful for a reproducible demo. |
| `ZAMU_COORDINATOR_EMAIL` | *(unset)* | Where the daily brief goes. Unset means it is skipped. |
| `ZAMU_NO_SEED` | *(unset)* | `1` to stop Zamu seeding the demonstration organization into an empty store. |
| `AWS_REGION` | `us-east-1` | Region for DynamoDB, SES and Bedrock. |

Zamu never fails because AWS is absent. No credentials means the deterministic planner
and the outbox; a missing SES sender means messages are captured rather than sent. A
coordinator part-way through setup should see exactly what Zamu *would* have sent
rather than a stack trace.

---

## 1 · Local

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[api,agent,dev]"
.venv/bin/zamu demo --reset
.venv/bin/uvicorn zamu.api.app:app --port 8000
```

```bash
cd console && npm install && npm run dev
```

Or both in containers:

```bash
docker compose -f deploy/compose.yaml up --build
```

---

## 2 · AWS: the service and the loop

One DynamoDB table, one Lambda behind a Function URL serving the API and the
volunteer's pages, and one scheduled Lambda running the loop. Deployed with SAM.

```bash
sam build --template deploy/cloudformation.yaml
```

```bash
sam deploy --guided --stack-name zamu --capabilities CAPABILITY_IAM
```

The Function URL is only known after the first deploy, and the one-tap links are built
from it, so deploy twice:

```bash
sam deploy --stack-name zamu --capabilities CAPABILITY_IAM \
  --parameter-overrides BaseUrl=https://<your-function-url> ConsoleOrigin=https://<your-console>
```

What that creates:

- **`RosterTable`** — single-table design with two indexes: `token-index` resolves a
  one-tap link to an ask, `idempotency-index` backs the conditional put that makes the
  ledger genuinely idempotent under concurrency. Retained on stack deletion, because
  it holds other people's commitments to each other.
- **`ApiFunction`** — the FastAPI app via Mangum, on a Function URL.
- **`ScheduledFunction`** — the loop, on three EventBridge schedules:

  | Job | Default | What it does |
  |---|---|---|
  | `sweep` | every 15 minutes | Expire lapsed asks, advance every open gap by one ask. |
  | `risk` | hourly | Notice duties drifting into at-risk before the day of. |
  | `brief` | daily at 08:00 UTC | The handover — sent only when there is a decision to make or work Zamu completed. Most days it sends nothing. |

Run a job on demand:

```bash
aws lambda invoke --function-name <ScheduledFunctionName> --payload '{"job":"sweep"}' /dev/stdout
```

### Email

SES starts in the sandbox, which only delivers to verified addresses — fine for a
pilot, and the reason the outbox path exists at all.

```bash
aws sesv2 create-email-identity --email-identity zamu@your-domain.org
```

Verify the domain and request production access before a real rollout, then set
`ZAMU_SES_SENDER`. Warm the domain gently: an ask that lands in spam is worse than an
ask that was never sent, because the coordinator believes it arrived.

---

## 3 · AWS: the agent on Bedrock AgentCore

AgentCore gives Zamu a managed runtime with session isolation, memory that survives
across the days a single fill can take, and traces you can follow end to end. The
container contract is small: `POST /invocations`, `GET /ping`, port 8080, `linux/arm64`.

`zamu/agentcore/app.py` implements it with `BedrockAgentCoreApp`. Build and push:

```bash
docker buildx build --platform linux/arm64 -f deploy/Dockerfile.agentcore -t zamu-agent .
```

```bash
aws ecr create-repository --repository-name zamu-agent
```

Tag and push to the repository URI it returns, then create the runtime pointing at
that image. The execution role needs `bedrock:InvokeModel`, read and write on the
DynamoDB table, and `ses:SendEmail` if email is enabled.

Invocation payloads:

```json
{"action": "agent",  "org_id": "org_...", "prompt": "Handle whatever needs doing."}
{"action": "sweep",  "org_id": "org_..."}
{"action": "brief",  "org_id": "org_..."}
{"action": "status", "org_id": "org_..."}
```

`action` defaults to `agent`, and a bare `{"prompt": "..."}` works, because that is
what the AgentCore console sends from its test panel.

Ping reports healthy only when the store is actually reachable. A runtime that reports
healthy while its database is down is worse than one that reports unhealthy.

---

## 4 · The console

A static Next.js build. `NEXT_PUBLIC_*` values are inlined at build time, so the API
URL is a build argument rather than a runtime setting.

On Vercel, set:

```
NEXT_PUBLIC_ZAMU_API=https://<your-api>
NEXT_PUBLIC_ZAMU_ORG=org_demo_riverside
```

Then add that origin to `ZAMU_CORS_ORIGINS` on the service and redeploy it.

Anywhere else:

```bash
docker build -t zamu-console --build-arg NEXT_PUBLIC_ZAMU_API=https://<your-api> console
```

---

## Costs

Sized for organizations that currently pay nothing for anything, because that is who
this is for.

| | Roughly |
|---|---|
| DynamoDB | pay-per-request; a 50-person roster is pennies a month |
| Lambda | ~3,000 scheduled invocations a month, well inside the free tier |
| Bedrock | a few thousand small-model calls a month |
| SES | $0.10 per thousand messages |

The largest cost is attention, and the point of the product is to spend less of it.

---

## Operating it

- **Every action leaves a receipt.** `GET /api/orgs/{org}/receipts` and the console's
  Receipts screen show what was intended, what was observed on re-read, and the rule
  that permitted or refused it. Start there when something looks wrong.
- **Refusals are data, not errors.** A `BLOCKED` receipt with rule `R3-no-grant` means
  somebody needs to grant a permission, not that Zamu is broken.
- **Open receipts are reconciliation jobs.** An entry with no result was opened and
  never closed, which means a process died mid-flight. The intended state is recorded,
  so it can be checked by hand.
- **`GET /health`** reports which adapters are wired, and carries no secrets.
