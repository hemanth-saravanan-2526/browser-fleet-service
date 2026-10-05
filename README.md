# Browser Fleet Service — Implementation Plan

## What We're Building (In Simple Terms)

Right now, if we need to scrape websites, we'd normally keep a server (VM) running all the time, waiting for scraping jobs. That's wasteful — most of the time it just sits idle doing nothing, but we still pay for it 24/7.

Instead, we're building a system where:

1. We package each scraping tool (Playwright, nodriver, Selenium) into its own **self-contained box** (a container).
2. These boxes are uploaded to AWS, and AWS only **turns them on when a scraping request comes in**.
3. Once the job is done, the box switches off automatically.
4. We only pay for the exact time it was running — nothing when it's idle.

Think of it like calling a taxi instead of owning a car. You don't pay for the taxi when you're not using it, but when you need a ride, one shows up, takes you where you need to go, and leaves. If 200 people need rides at once, 200 taxis show up — no waiting, no fleet sitting idle the rest of the day.

**Goal:** Same scraping capability we have today, but with zero cost when idle and automatic scaling during traffic spikes.

---

## The Full Flow (High Level)

### Step 1: Request comes in
A client (our app/service) sends a request with:
- The target URL to scrape
- Which engine to use (Playwright / nodriver / Selenium)
- Any options (wait conditions, proxy, output format, etc.)

### Step 2: Dispatch layer receives it
A lightweight "traffic controller" (API Gateway + a small dispatcher Lambda) receives the request and decides:
- Which engine's container should handle this job
- Routes the request to that specific Lambda function

### Step 3: The right engine spins up
AWS Lambda automatically starts (or reuses a warm) container for the chosen engine:
- **Playwright container** → for stealth-mode scraping
- **nodriver container** → for its specific bypass capabilities
- **Selenium container** → for standard headless automation

Each container has its own browser binaries and dependencies bundled in — completely isolated from the others.

### Step 4: The scrape happens
Inside the container:
- Headless browser launches
- Navigates to the target URL
- Executes any required actions (waiting for elements, bypassing bot checks, etc.)
- Extracts the HTML or specific data requested

### Step 5: Response goes back
- Small results are returned directly in the response.
- Large HTML/data payloads are stored in S3 and a link is returned instead (to avoid size limits).
- The container then goes idle — and if no new requests come in, it shuts down completely (scales to zero).

### Step 6: Handling traffic spikes
If 200 requests come in at once:
- AWS automatically spins up multiple parallel copies of the needed containers
- Each request gets its own isolated execution
- Once traffic drops, everything scales back down to zero — no manual intervention, no leftover running servers

---

## Why This Matters

| Old Way (VMs) | New Way (Serverless) |
|---|---|
| Running 24/7 regardless of load | Runs only when there's a request |
| Manual scaling for traffic spikes | Auto-scales instantly |
| Pay for idle time | Pay only for actual execution time |
| One server handles all engines | Each engine isolated in its own container |

---

## Prerequisites

Before kicking off Phase 0, the following need to be in place:

### AWS Account & Access
- An AWS account (or a dedicated sub-account) with billing set up, ideally separate dev/staging/prod environments
- IAM permissions to create: Lambda functions, ECR repositories, API Gateway, S3 buckets, DynamoDB tables, SQS queues, CloudWatch dashboards/alarms
- A **Lambda concurrency limit increase request** filed early with AWS support — default account concurrency limits will likely block the 200-concurrent target, and these increases can take a few days to approve

### Tooling & Local Setup
- Docker installed locally (for building/testing container images before pushing to ECR)
- AWS CLI configured with appropriate credentials
- An IaC tool decided and set up — CDK (if the team is comfortable with TypeScript/Python) or Terraform (if already used elsewhere in the org)
- CI/CD pipeline access (GitHub Actions, CodePipeline, etc.) to automate container builds and Lambda deployments

### Team Skills
- At least one person comfortable with Docker/container internals (image size optimization, multi-stage builds)
- Familiarity with the three scraping engines individually — Playwright, nodriver, Selenium — since each has different quirks in a headless/serverless context
- Someone who can own AWS networking basics if a NAT Gateway + Elastic IP is needed for outbound IP reputation (this is a likely follow-on need — see Risks section)

### Design Decisions to Lock In First
- The request/response contract (drafted below) — needs sign-off so all three containers are built against the same interface
- Sync vs. async dispatch pattern for MVP (Option A vs. B, see Dispatch / Routing Layer section)
- Budget ceiling for the AWS spend during the POC/testing phase

### Access to Test Targets
- A representative set of target URLs/sites to scrape during benchmarking — ideally including at least one site with anti-bot protection, so stealth/bypass behavior gets validated early rather than discovered in production

---

## Architecture Diagram (Conceptual)

```
                        ┌─────────────────────┐
Client Request ──────▶  │   API Gateway /      │
                        │   Dispatch Layer      │
                        └──────────┬───────────┘
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
              ┌───────────┐ ┌───────────┐ ┌───────────┐
              │ Lambda:   │ │ Lambda:   │ │ Lambda:   │
              │ Playwright│ │ nodriver  │ │ Selenium  │
              │ (stealth) │ │           │ │           │
              └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
                    │             │             │
                    ▼             ▼             ▼
              ┌───────────────────────────────────────┐
              │   S3 (large payloads) / DynamoDB       │
              │   (job status, results cache)          │
              └───────────────────────────────────────┘
```

**Core design principle:** decouple "dispatch/routing" from "execution." The dispatch layer picks an engine and invokes the right Lambda; each Lambda is a dumb, single-purpose executor. This keeps engine containers swappable and independently deployable.

---

## Detailed Delivery Plan

### Phase 0 — Foundations (Week 1)
- Set up AWS account structure: separate dev/staging/prod, IAM roles with least-privilege (Lambda execution role, ECR pull, S3/DynamoDB access).
- Set up ECR repositories: one per engine (`browser-fleet/playwright`, `browser-fleet/nodriver`, `browser-fleet/selenium`).
- Define the **common request/response contract** (see below) before writing any container — this is what lets the three teams/containers work in parallel without drift.
- Choose IaC tool (recommend **AWS CDK** or **Terraform**) — do not hand-click this infra.

### Phase 1 — Single Engine Proof of Concept (Weeks 2–3)
- Build **one** container end-to-end (starting with Playwright, most mature Lambda tooling) to validate:
  - Cold start time with headless Chromium in Lambda
  - Package size under Lambda's 10GB container image limit
  - Memory/timeout tuning
  - `/tmp` usage for Chromium user-data-dir (Lambda's only writable disk, 512MB–10GB configurable)
- Get this one path fully working: API Gateway → Lambda → Playwright → response, with logging/tracing.
- This de-risks the whole project. Don't parallelize container builds until this works.

### Phase 2 — Remaining Containers (Weeks 3–5)
- Port validated patterns to `nodriver` and `Selenium` containers.
- Normalize all three to the same handler interface and output schema.
- Add per-engine capability flags (e.g., not all engines support the same stealth/proxy options) surfaced in a capabilities manifest.

### Phase 3 — Dispatch Layer (Weeks 4–6, overlaps Phase 2)
- Build the routing service (see below).
- Add engine-selection logic: explicit (`engine: "playwright"` in request) with sane default, plus optional auto-routing heuristics later (e.g., fall back to nodriver if Playwright gets blocked).
- Implement async job pattern for long-running scrapes (see below).

### Phase 4 — Observability, Hardening, Cost Controls (Weeks 6–7)
- Structured logging, per-engine dashboards, alerting on error rate / duration / concurrency throttling.
- Load test to the 200-concurrent-request target; tune reserved/provisioned concurrency.
- Cost guardrails: per-request timeout caps, memory right-sizing, budget alarms.

### Phase 5 — Production Rollout (Week 8)
- Canary deploy, gradual traffic shift from existing VM-based system.
- Runbook + rollback plan.
- Decommission always-on VMs once steady-state validated.

---

## Container Optimization Strategy

Chromium + Lambda cold starts are the classic failure mode for this kind of system. Approach:

| Lever | Recommendation |
|---|---|
| Base image | `public.ecr.aws/lambda/nodejs` or `python` base + [`sparticuz/chromium`](https://github.com/Sparticuz/chromium) (a Chromium build specifically trimmed/patched for Lambda) rather than the full Playwright browser download |
| Image size | Keep image as close to Lambda's soft practical limit as possible (~250–500MB compressed is a reasonable target); avoid bundling multiple browser binaries in one image |
| Cold start mitigation | Provisioned Concurrency for baseline traffic; Lambda SnapStart is not currently available for container-image functions, so don't plan around it |
| `/tmp` usage | Mount Chromium's user-data-dir and cache in `/tmp` (ephemeral storage, configurable up to 10GB); clean up between invocations if reusing execution environments |
| Execution environment reuse | Lambda reuses warm containers — design the handler to **reuse the browser process/context across invocations** in the same execution environment instead of relaunching Chromium every call. This is the single biggest performance lever. |
| Benchmarking metric | Track p50/p95/p99 cold start, warm invocation latency, and memory high-water-mark per engine — build this into CI so a regression is caught before merge |

This benchmarking should happen as a **spike in Phase 1**, not as an afterthought — it determines whether the 200-concurrent-burst target is even achievable within Lambda's account-level concurrency limits (request a limit increase early; default account concurrency is often too low).

---

## Dispatch / Routing Layer

Two viable patterns:

### Option A: Direct synchronous invoke (simple, for fast scrapes)
- API Gateway (HTTP API, cheaper than REST API) → Lambda (dispatcher, lightweight) → `InvokeCommand` (RequestResponse) on the target engine Lambda → return result.
- **Pros:** simple, low latency for short scrapes.
- **Cons:** API Gateway has a 29-second timeout — a hard ceiling for slow pages/anti-bot challenges.

### Option B: Async job queue (recommended for production)
- API Gateway → Dispatcher Lambda → writes job to SQS (one queue per engine, or one queue + `engine` attribute) → engine Lambda triggered by SQS → writes result to S3/DynamoDB → client polls a `GET /jobs/{id}` endpoint or receives a webhook/SNS notification.
- **Pros:** no timeout ceiling, natural backpressure/retry via SQS, smooths bursts (SQS absorbs the 200-concurrent spike and Lambda drains it within its concurrency limits rather than throttling requests outright).
- **Cons:** added complexity, client needs polling/webhook support.

**Recommendation:** build Option A first for a quick win/demo, but design the request contract so Option B can be added without breaking clients (return a `job_id` even in sync mode, wrapping the sync path).

### Routing decision logic (dispatcher pseudocode)
```
engine = request.engine || default_engine_for(request.url_domain)
validate engine against capabilities manifest
invoke/enqueue to arn:...:function:browser-fleet-{engine}
```

---

## Request / Response Contract

Define this before Phase 1 coding begins — it's what lets containers be built in parallel without drift.

**Request**
```json
{
  "url": "https://example.com",
  "engine": "playwright | nodriver | selenium",
  "output": "html | text | screenshot | json",
  "options": {
    "wait_for": "selector or timeout",
    "proxy": "optional proxy config",
    "headers": {},
    "stealth": true,
    "timeout_ms": 30000
  }
}
```

**Response**
```json
{
  "job_id": "uuid",
  "status": "success | error | timeout",
  "engine_used": "playwright",
  "duration_ms": 4210,
  "result": { "html": "...", "or s3_url": "..." },
  "error": null
}
```

Large HTML payloads should go to S3 with a presigned URL in the response, not inline through API Gateway (6MB payload limit).

---

## Handling Long-Running / Anti-Bot Scrapes
- Set per-engine Lambda timeout ceilings (e.g., 60–120s) well under the 15-min Lambda max, since a stuck browser session is a cost/DoS risk.
- Implement a circuit-breaker per domain: if a target site consistently fails/blocks, short-circuit rather than burning Lambda time repeatedly.
- Consider a retry-with-different-engine fallback (e.g., Playwright stealth fails → retry with nodriver) as a Phase 4+ enhancement, not MVP.

---

## Cost Model (for stakeholder buy-in)
- Model expected cost using: avg scrape duration × avg memory allocation × request volume, compared against current VM cost.
- Rough Lambda pricing lever: memory allocation also scales CPU — headless Chromium is CPU-bound, so under-provisioning memory to save cost often backfires via longer duration. Benchmark cost-per-scrape at 1024MB / 2048MB / 3008MB to find the actual optimum, don't assume smaller = cheaper.
- Track this in Phase 1 benchmarking so the cost story is real numbers, not estimates, before finalizing the business case.

---

## Risks & Open Questions to Resolve Early
1. **Account concurrency limits** — request an increase now; 200 concurrent bursts across 3 functions could hit default caps.
2. **IP reputation / proxying** — Lambda's outbound IPs are shared/AWS-owned and may already be flagged by anti-bot systems; may need a NAT Gateway + fixed Elastic IP or a proxy provider. Validate this in Phase 1, not in prod.
3. **Container image size vs. cold start** — may need to trim Selenium's driver + JVM-adjacent dependencies aggressively.
4. **Ephemeral storage limits** — heavy pages with many resources could exceed `/tmp` capacity; needs monitoring.
5. **Vendor lock-in** — confirm this is acceptable vs. a more portable approach (e.g., Fargate for longer jobs) as a hybrid fallback for scrapes that don't fit Lambda's constraints.

---

## Suggested Team Allocation
- **1 engineer:** Lambda/container infra + IaC (Phases 0, 1, 4)
- **1–2 engineers:** engine containers (Phase 2, can parallelize once Phase 1 pattern is proven)
- **1 engineer:** dispatch layer + API (Phase 3)
- **Shared:** observability, load testing (Phase 4)

---

## What's Next
1. **Proof of concept:** Build and benchmark the Playwright container first to validate cold-start speed and cost.
2. **Build remaining containers:** Port the same pattern to nodriver and Selenium.
3. **Build the dispatch layer:** The routing logic that sends jobs to the correct engine.
4. **Test at scale:** Simulate 200 concurrent requests to confirm it holds up.
5. **Roll out gradually:** Shift traffic from existing VMs to the new system, then decommission the VMs.
