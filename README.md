# ERP AI — Assisted HCM Configuration

ERP AI is an independent system that connects to **HCM Lab** through its integration API and configures HCM modules on behalf of a consultant. The consultant describes what they want in plain language; a configurable language model interprets the request, the system builds a validated configuration plan, a human approves the exact plan, and ERP AI applies it automatically to HCM — always in the **staging (homologação)** environment. Configuration covers every company registered in HCM. **Production is never published by the integration.**

The application's user interface is in Portuguese. Throughout this document, screen labels are given in English with the original Portuguese label in parentheses, e.g. **Analyze request** (*Analisar solicitação*).

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Build rules (non-negotiable)](#2-build-rules-non-negotiable)
3. [Architecture](#3-architecture)
4. [Installation and updates](#4-installation-and-updates)
5. [Configuration](#5-configuration)
6. [How to use](#6-how-to-use)
7. [Simplified flow](#7-simplified-flow)
8. [Orchestration and intent memory](#8-orchestration-and-intent-memory)
9. [Integration contract v2](#9-integration-contract-v2)
10. [Requests that span more than one module](#10-requests-that-span-more-than-one-module)
11. [Memory and learning](#11-memory-and-learning)
12. [Execution, testing and rollback](#12-execution-testing-and-rollback)
13. [Companies and the Unhandled queue](#13-companies-and-the-unhandled-queue)
14. [Existing HR module parameters](#14-existing-hr-module-parameters)
15. [Scope and inputs unrelated to HCM](#15-scope-and-inputs-unrelated-to-hcm)
16. [Model and costs](#16-model-and-costs)
17. [HTTP API reference](#17-http-api-reference)
18. [Tests](#18-tests)
19. [Operations](#19-operations)
20. [Security](#20-security)
21. [Limits and known constraints](#21-limits-and-known-constraints)
22. [Glossary of UI labels](#22-glossary-of-ui-labels)

---

## 1. What it does

| Step | Who | What happens |
|---|---|---|
| Request | Consultant | Describes the goal and the effective date in natural language. |
| Understanding | Agents | Identify the company and the HCM module, read the module catalog and the current configuration, and produce **three interpretations**. |
| Choice | Consultant | Picks the correct interpretation, or rejects all of them with a correction. |
| Plan | Agents | Build a configuration plan restricted to the catalog, validated by JSON Schema and by HCM's official validator. |
| Approval | Consultant | Approves the exact plan. The approval is bound to a hash of the plan and of the context. |
| Application | ERP AI → HCM | HCM re-checks the context, applies the plan atomically, runs tests and records the operation. |
| Result | Consultant | Confirms the result is what was requested; the case becomes reusable experience. |
| Rollback | Consultant | Optional; reverts exactly the versions created by the operation, if nothing changed since. |

Anything that HCM's API cannot do is not improvised: it is recorded in the **Unhandled** (*Não tratados*) queue for a consultant, with a short explanation for the user.

---

## 2. Build rules (non-negotiable)

**HCM is never changed.** HCM is a finished, working system that offers ERP AI only a communication API (`/api/integration/v2`). It is treated exactly like a vendor's product: ERP AI uses what the API publishes, the way the API publishes it.

- Every limitation is solved on the ERP side: in the HCM client, the agents, validation, the interface or the documentation.
- Whatever the API does not offer becomes an entry in **Unhandled**, never a change to HCM.
- If a change in HCM ever seems indispensable, work stops and the gap in the API is explained to the product owner; it is never done by default.

Other rules:

- Agents carry **no HR business knowledge** in code or prompts. Modules, fields, schemas, scopes, precedence and rules all come from the catalog published by the API.
- **No write to HCM** without human approval of the exact plan, an approval hash and a context check. Everything runs in staging; production never.
- **Secrets** (API keys, tokens, passwords) never enter prompts, checkpoints, logs or responses.
- If the requested configuration is possible with the HCM catalog — even by combining two or more modules — ERP AI prepares and applies it in a **single plan**. If it is not possible, nothing is applied and the reason is given to the user.
- **User-facing messages** are short and plain, written for non-specialists: what was understood, what is possible, what prevented it, what to do now. Technical details stay in dedicated fields (`missing_capabilities`, events, metrics).

---

## 3. Architecture

### 3.1 Components

```
 Browser ──► frontend (Next.js, :3001) ──proxy──► backend (FastAPI, :8001) ──► HCM Lab /api/integration/v2 (:8000)
                                                     │
                                                     ├──► PostgreSQL 17 (own database and volume, no public port)
                                                     └──► LLM provider (OpenAI-compatible Chat Completions, strict JSON Schema)
```

| Service | Technology | Port | Notes |
|---|---|---|---|
| `frontend` | Next.js 16, React 19, TypeScript, lucide-react | `127.0.0.1:3001` | Proxies API calls to the backend (`API_PROXY_URL`). |
| `backend` | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, LangGraph, jsonschema, httpx | `127.0.0.1:8001` | Health check at `/health`. Mounts `./secrets` for model settings. |
| `database` | PostgreSQL 17 (alpine) | none published | Volume `erp_ai_data`. Also stores LangGraph checkpoints. |
| HCM Lab | External system | `8000` on the host | Reached from Docker via `host.docker.internal`. |

Both published ports are bound to localhost only.

### 3.2 Repository layout

```
backend/
  app/
    main.py               Auth, model settings, cases, analysis, review, validation endpoints
    orchestration.py      LangGraph graph: agents, routing, pauses, multi-module handling
    orchestration_api.py  Orchestrate / workflow endpoints, companies, Unhandled queue, intent memories
    execution_api.py      Execute, verify, confirm result, rollback
    insights_api.py       Knowledge settings, usage metrics, missing capabilities
    hcm.py                HCM integration v2 client
    contracts.py          Catalog contract handling and contract_hash
    schema_tools.py       Strict model-output schemas generated from the catalog
    provider.py           LLM adapter (Chat Completions, strict JSON Schema)
    model_catalog.py      Supported models, prices, retirement dates
    model_settings.py     Model configuration (secrets/model.json, .env)
    knowledge.py          Retrieval of documentation, approved cases, anonymized experience, lessons
    input_guard.py        Scope check and instruction-override detection
    workflow.py           Case state machine
    auth.py, database.py, models.py
  migrations/versions/    0001_cases … 0004_learning
  tests/                  Automated test suite (pytest)
frontend/app/             page.tsx, OrchestrationPanel, ExecutionPanel, ModelSettings, HelpPanel
scripts/                  Bootstrap, evaluation and live acceptance scripts (all reversible)
evaluation/               Functional evaluation scenarios and contract v2 example
docs/fluxo-agentes-bpmn.svg  BPMN diagram of the agent flow and the LangGraph graph
compose.yaml, .env.example
```

---

## 4. Installation and updates

### 4.1 Requirements

- Docker Desktop / Docker Compose
- Python 3.12 (to prepare access credentials)
- A running HCM Lab exposing the integration API v2 (including the integration adapter this work relies on)

### 4.2 First installation

```powershell
python scripts/bootstrap.py --hcm-path C:\projects\HCM
docker compose -f C:\projects\HCM\compose.yaml up -d --no-deps backend
docker compose up -d --build
```

What each step does:

1. **`bootstrap.py`** provisions local secrets without printing them and without overwriting existing ones: database password, session secret, consultant login (stored as a password hash), and two separate random HCM credentials — one for **query** and one for **execution**. HCM receives only their hashes. Login details for the UI are written to `.local-access.txt`.
2. **HCM backend** is started; on startup HCM applies its own migration `0009`.
3. **ERP AI** is built and started: database, backend (runs Alembic migrations and prepares the LangGraph checkpoint schema before accepting requests), then frontend.

Open http://localhost:3001 and log in with the credentials in `.local-access.txt`.

### 4.3 Updating

```powershell
git pull
docker compose up -d --build
```

The bootstrap preserves existing access. ERP AI uses its own database and volume; never remove volumes to update.

---

## 5. Configuration

### 5.1 Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_PASSWORD` | PostgreSQL password (random secret). |
| `SESSION_SECRET` | Session signing secret (≥ 32 random characters). Rotating it ends all sessions. |
| `CONSULTANT_USER` | Consultant login name (default `consultor`). |
| `CONSULTANT_PASSWORD_HASH` | Generated by `scripts/bootstrap.py`. |
| `HCM_INTEGRATION_TOKEN` | Generated by `scripts/bootstrap.py`. |
| `HCM_BASE_URL` | HCM address (`http://localhost:8000`; Compose overrides it with `http://host.docker.internal:8000`). |
| `APP_ORIGIN` | Allowed origin for the UI (`http://localhost:3001`). |
| `COOKIE_SECURE` | `false` locally; must be `true` behind TLS. |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Optional OpenAI-compatible provider with strict JSON Schema support. The base URL includes `/v1`, without `/chat/completions`. |

### 5.2 Model settings from the UI

Model configuration done in the UI is stored in `secrets/model.json`, which is git-ignored and mounted into the backend (`LLM_CONFIG_FILE`). It **takes precedence over `.env`**. There is no custom disk encryption: the HCM token and passwords live in local, git-ignored files. Protect these files and do not share them.

---

## 6. How to use

1. Go to http://localhost:3001 and log in with the access in `.local-access.txt`.
2. In **AI model** (*Modelo de IA*), choose a model, save your API key and test the connection. Never send the key through the chat. Prices, availability and estimates are shown on screen.
3. In **Cases** (*Atendimentos*), describe the goal and the effective date. In **Agent models** (*Modelos dos agentes*), check the model for the first round (**Luna** by default) and for re-analysis (**Sol** by default), with prices. The saved key is reused; the previous global model is preserved.
4. Click **Analyze request** (*Analisar solicitação*). The system identifies the company and the module and goes straight to **three interpretations**. Choose the correct one; **None of these options** (*Nenhuma dessas opções*) redoes the identification using your correction. Then review the configuration proposal.
5. Click **Approve and apply in staging** (*Aprovar e aplicar em homologação*). HCM applies the exact plan, tests it and records the operation. There is no need to open HCM to configure anything.
6. After the "configuration applied" message, click **Approve and save experience** (*Aprovar e guardar experiência*) if the result meets the request. The automatic report is in an optional details section; there is no evidence questionnaire.
7. To undo, use **Revert this application in staging** (*Reverter esta aplicação em homologação*). Later changes block the rollback, to preserve other people's work.

The **How to use** (*Como usar*) menu explains the flow inside the application. If an old case was created before automatic execution existed and HCM has changed since, run a new analysis — do not try to apply an old proposal on top of new data.

---

## 7. Simplified flow

- The analysis identifies company and module automatically and pauses directly on the three interpretations.
- A clarification is requested **only** when the company is missing, ambiguous, not found or not authorized. There is no separate confirmation of company or module.
- A **first rejection** redoes classification, company lookup and interpretation using the correction. A **second rejection** forwards the case to the consultant.
- Redoing the proposal starts a new entry and deactivates the previous intent memory of that case.
- After application, the main screen shows a confirmation and the **Approve and save experience** button. Test report, context and history remain available in collapsed details.

A BPMN diagram of the whole flow is in [`docs/fluxo-agentes-bpmn.svg`](docs/fluxo-agentes-bpmn.svg).

---

## 8. Orchestration and intent memory

### 8.1 The graph

LangGraph organizes classification, catalog lookup, guided presentation, generation of three interpretations, the pause for the user's choice, intent memory, the validated plan and hand-off to the consultant.

| Node | Role | Next |
|---|---|---|
| `guard_input` | Scope check of the latest message. | `classify`, or end if out of scope |
| `classify` | Picks company and module (module list comes from HCM). | `preview`, `context_expert` (consultant re-plan), or end |
| `preview` | Company lookup and guided presentation. | `clarify` or `context` |
| `clarify` | Pause for a missing/ambiguous company. | `guard_input` or `consultant` |
| `context` | Loads catalog(s) and current snapshot(s), retrieves knowledge. | `alternatives` or `consultant` |
| `alternatives` | Generates three interpretations. May **redirect** or **extend** modules. | `choice`, `context` (redirect/extend) or `consultant` |
| `choice` | Pause (`interrupt`) for the user's choice. | `remember`, `guard_input` (rejection) or `consultant` |
| `remember` | Stores the confirmed intent. | `plan` or `consultant` |
| `plan` | Builds and validates the plan; one retry with the validator's reason. | end or `consultant` |
| `consultant` | Hand-off to the Unhandled queue. | end, or `guard_input` when the consultant asks for a new plan |
| `context_expert` | Context for a consultant-guided plan. | `plan` |

### 8.2 Durability

- Pauses use `interrupt` / `Command` with a stable `thread_id`.
- Checkpoints live in ERP AI's PostgreSQL; local tests use a file-based SQLite.
- The server prepares the checkpoint schema before accepting requests.
- Recompiling the graph or restarting the backend does not lose choices and does not repeat completed steps.
- Creation and resumption use optimistic revisions and PostgreSQL row locks. An error can be resumed from the same checkpoint.

### 8.3 Escalation

- After the **first rejection**, a more capable re-analysis model runs (Sol by default).
- The **second rejection** creates a case in **Unhandled**.
- A choice that depends on a **missing capability** is also forwarded to the consultant, without application.
- The consultant can guide a new plan, use the validated editor, or close the hand-off with a conclusion.

### 8.4 Modules and interpretations

- The list of modules the classifier knows comes **from HCM on every flow**, with label and description; there is no fixed list in ERP AI. Classification covers Vacation, Payroll/payslip, Timekeeping, Hiring, Benefits and Other.
- Existing parameters of Vacation, Timekeeping, Hiring, Payslip and Benefits have connectors. Requirements not present in the catalogs are recorded in Unhandled — no fictitious parameters.
- Each interpretation states the level it intends to change and describes capability gaps with module and description; these show up in the Unhandled queue.
- A plan rejected by the validator goes back to the model **once**, with the reason, before going to the consultant.
- Each agent records its own prompt version in the metrics.

### 8.5 Requirements already in place

The interpreter compares each requirement with the current effective rule and records in `already_in_place` what is already in force. The option shows it as "Already like this today" (*Já está assim hoje*); it does not count as a gap, and the plan does not repeat values already in place.

### 8.6 Message to the consultant

When no interpretation is possible, the hand-off carries four short sentences (`handoff_summary`): what was understood, what is possible, what prevented it, what to do now. Technical gaps remain recorded in `missing_capabilities`, outside the main text.

### 8.7 Intent memory

`interpretation_memories` stores the intent confirmed by the consultant, per company, module and catalog version.

- It is used as **context only**, requires confirmation on every new request and does **not** prove a working solution.
- It can be disabled in **Knowledge** (*Conhecimento*) and individual memories can be forgotten.
- Applied, tested and approved cases are searched separately.
- API keys and HCM tokens never enter the checkpoint or the prompt.
- Confirming an interpretation does **not** authorize a write: execution still requires approval of the exact plan and a context check in HCM.

---

## 9. Integration contract v2

ERP AI talks to HCM through `/api/integration/v2`.

### 9.1 Catalog and `contract_hash`

Each module's catalog includes its fields with JSON Schema, the available scopes, rule precedence and a `contract_hash`. The hash is stored on every case and every interpretation memory. If HCM changes the meaning of a field or the precedence — even without changing the catalog version — ERP AI **blocks acceptance and execution** of the old case and keeps its memories out of automatic retrieval. Cases analyzed under an earlier contract show a warning and need a new analysis before any acceptance.

### 9.2 Scopes in Vacation

A Vacation change can target:

- the **general rule**;
- a group's **own rule** (`group_id`);
- a **named configuration** (`config_code`).

New named configurations are declared in `named_configurations`. The list of **Configuration Assignments** can be replaced in the same plan (`assignments`) and is included in the approval hash. When a group's effective rule comes from an assignment, ERP AI refuses to change the group's rule and directs the user to change the named configuration or the assignments instead.

### 9.3 Other modules

The other HCM modules use company scope. Their structured parameters, such as approval steps, are validated against the catalog schema before reaching HCM's official validator.

### 9.4 Module list and model output

- The module list and labels come from HCM (`GET /v2/modules`); ERP AI only keeps the alias `time` for `timekeeping`.
- The model's output is a **strict schema generated from the catalog**, with one variant per parameter.
- Catalog maps become key/value lists for the model and are turned back into maps before validation.

An example of the contract is in [`evaluation/contract-v2-example.json`](evaluation/contract-v2-example.json).

---

## 10. Requests that span more than one module

- The classifier picks the module of the **main requirement**.
- If the interpreter concludes that part of the request belongs to another module, it returns `additional_modules`: the flow loads that module's catalog and snapshot and redoes the analysis with all catalogs (event `module_extended`, up to two additions per round).
- When the current catalog covers nothing in the request, `suggested_module` switches modules (event `module_redirected`).
- The final plan has **one part per module** (`proposal.plans`, each with its own payloads, snapshot and catalog). Every change carries `module`; each part is validated with its own module's catalog and validator; **a single approval hash covers all parts**.
- If the configuration is possible, even by combining modules, ERP AI does it. If it is not, nothing is applied and the reason is given in four sentences.

---

## 11. Memory and learning

### 11.1 Retrieval sources

Documentation and approved cases are retrieved by lexical search with light Portuguese stemming, filtered by company, module, version and contract hash. Rejected, reverted and obsolete cases are excluded from the memory of approved solutions.

Two extra sources, **on by default** and switchable in **Knowledge** (*Conhecimento*):

- **Anonymized experience** from other companies in the same module and contract, with company and group names redacted — used as analogy, never as values to copy.
- **Lessons**: counter-examples collected from the flow itself — interpretations rejected with a comment, plans rejected by the validator, plans with open issues, and consultant conclusions — also redacted.

The interpreter and the planner receive these sources **marked as data, not instructions**. This improves the external knowledge available to the AI; it does not update the model's weights. Fine-tuning remains a future decision that depends on reviewed data and evaluation.

### 11.2 Usage and results

**Usage and results** (*Uso e resultados*), under Knowledge, aggregates:

- all calls by model and role: calls, repairs, tokens, average duration, estimated cost;
- the funnel per module: accepted in the first round, re-analyses, hand-offs, applied, reverted, validated;
- cost per validated case.

**Missing capabilities** (*Capacidades ausentes*), under Unhandled, groups the gaps the agents described, with CSV export for the HCM roadmap.

### 11.3 Functional evaluation

`scripts/evaluate.py --execute` runs the `kind=parameter` scenarios of [`evaluation/scenarios.json`](evaluation/scenarios.json) with the real model over a synthetic state per scenario, judges the proposal against the expected result and writes `artifacts/evaluation-<date>.json`. Run it inside the backend image with the repository mounted:

```powershell
docker compose run --rm --no-deps -v "C:/projects/ERP-AI:/repo" backend python /repo/scripts/evaluate.py --execute
```

- Without `--execute` it only lists the evaluable scenarios.
- `--only F01,N02` restricts the run; `--model` switches the model.
- The report includes accuracy per family, undue and missing changes, questions, validator errors, tokens and cost per scenario.
- `kind=flow` scenarios are covered by the automated suite or require manual review, as indicated in each.
- It does **not** touch HCM.

See [`evaluation/README.md`](evaluation/README.md) for the evaluation methodology (family separation, A/B comparison with and without retrieval, repeated samples, cost formula, keeping the blind test out of any training/RAG context).

---

## 12. Execution, testing and rollback

### 12.1 Credentials

The connection uses two distinct random credentials: **query** and **execution**. HCM stores only their hashes; ERP AI keeps them as backend secrets. The model never receives them.

### 12.2 Approval and idempotency

- Each approval includes a hash of the **plan** and of the **context** (versions, groups, company, environment and effective date).
- HCM checks again inside a transaction and blocks a changed context.
- A company lock serializes vacation writes.
- Each operation has an idempotent UUID: a lost response can be resumed without duplicating versions. ERP AI persists the identifier **before** the remote call.

### 12.3 Multi-module execution

- A plan with several modules produces **one HCM operation per module**, applied in order (`result.operations`).
- Before applying, each module is checked against the analyzed state.
- If one module is refused or undone by HCM after another was already applied, ERP AI reverts the previous ones (`execution_compensated`) and the case goes to `execution_failed` in the Unhandled queue.
- If compensation fails, the `compensation_failed` event and an on-screen warning ask the consultant to check.
- Verification, result approval and rollback walk through all operations. Rollback undoes them in **reverse order** and records what has already been undone, so it can be resumed.

### 12.4 Tests run by HCM

HCM checks values/schedules, dependencies, group precedence, installment limits, requester, advance payment, notice period and balance in synthetic scenarios; production must remain untouched. The policy/date engine is shared with the operational simulation. Test failures roll back the entire transaction. Tests never create real vacation requests.

### 12.5 Rollback

Rollback removes only the versions created by the operation, preserving history, audit and monotonic numbering. Newly created group rules go back to inheriting the previous value. Rollback is only allowed if the state still matches the operation's result.

### 12.6 Result approval

After success, the human approval of the result releases the case to memory. Tests verify the implemented behavior; fitness to the business requirement is still reviewed by the specialist.

---

## 13. Companies and the Unhandled queue

### 13.1 Companies

- The integration works for **any registered company**. There is no fixed list of companies or codes and no requirement for an *Active* status to configure.
- The company must exist, groups must belong to it, and the plan must match the approval and the effective date; publishing to production stays outside the approved flow.
- Company search queries HCM's official registry, ignores accents and case, and presents homonyms for confirmation.
- The identified company code follows the query, memory, proposal, application, verification and rollback.
- Operations and rollbacks use the company recorded on the operation itself, including when resuming old operations.
- There is **no default company** for new queries or new cases.

### 13.2 The Unhandled queue

The **Unhandled** (*Não tratados*) queue (`/api/unhandled`, with `/api/consultant-queue` kept for compatibility) includes missing capabilities, modules without a connector, interpretation reviews, invalid proposals and execution/integration failures. Each item shows company, module, request, reason and category.

- Requirements with no feasible interpretation are forwarded automatically, without forcing the choice of an impossible alternative.
- The consultant can review the rule, prepare a validated plan or close the item with a conclusion; resolved cases leave the queue.
- The queue does not send external messages.

---

## 14. Existing HR module parameters

The catalog and executor include **Vacation**, **Timekeeping**, **Hiring**, **Payslip** and **Benefits**.

- In the four HR modules, parameters are read from the real contract `hr_api.DEFAULTS` and verified by HCM's official validator `validate_config`.
- The code does not invent parameters to satisfy requests outside the catalog.
- Operations are bound to company and module, with audited versions, idempotency, verification and reversible withdrawal of the version (HCM migration `0010_hr_integration`).
- In Timekeeping, synthetic scenarios go through the same `time_evaluation` used by the operational simulator, without creating employee records. Overtime changes are supported through the existing parameters `allow_overtime` and `max_overtime_hours`.

---

## 15. Scope and inputs unrelated to HCM

- The graph checks scope **before** looking up the company or its configuration.
- A quick check blocks explicit instruction-override patterns and calculations that name nothing the HCM publishes; semantic classification also distinguishes configuration, relevant clarification and off-topic questions.
- The quick check has no HR word list of its own: a subject counts when it matches the label, description or parameters of a module the HCM publishes. That module list is read only when a calculation question mentions something besides numbers, and the classifier reuses it.
- The **latest message** is evaluated, even when the earlier history is legitimate.
- Out-of-scope inputs get their own notice; they do not enter Unhandled and do not become solution memory.
- The user can rephrase the request in the same case; the old text stays only in the history, without contaminating the new request.
- Repeated clarifications get a visible confirmation and the input field is cleared.

These checks complement the validators, company/module isolation and plan approval; they are **not** a guarantee against every form of prompt injection.

---

## 16. Model and costs

### 16.1 Supported models

- 21 compatible text models, with Standard prices verified on 2026-10-31 on the official pages.
- The provider's models API reports what the account lists, not price or guaranteed access.
- Models with an announced retirement show the date and leave the selector on shutdown.
- The suggested default for new configurations is **Luna**; an existing choice is never changed automatically.
- There is **no automatic escalation** to a more expensive model, other than the configured re-analysis model after a rejection.

### 16.2 Provider adapter

- Uses Chat Completions with strict JSON Schema, `store=false`, an output limit and reasoning effort appropriate to the model.
- Fields, groups and sources are restricted to the catalog.
- Invalid key, quota, access, refusal, timeout and invalid output each produce a distinct message, without exposing sensitive responses.
- Each generation records model, prompt version, schema hash, duration, tokens and estimated cost.

Prices, context, caching and taxes can affect actual billing; an estimate is not an invoice. There are no automatic paid tests of every model.

---

## 17. HTTP API reference

All routes except `/health` and `/api/login` require an authenticated session.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Health check. |
| POST | `/api/login`, `/api/logout` | Start/end session. |
| GET | `/api/me` | Current user. |
| GET/POST | `/api/model` | Read/save model settings. |
| GET | `/api/model/catalog` | Supported models and prices. |
| POST | `/api/model/test` | Test the model connection. |
| GET | `/api/connection` | HCM connection status. |
| GET/POST | `/api/cases` | List/create cases. |
| GET | `/api/cases/{id}` | Case details. |
| POST | `/api/cases/{id}/analyze` | Analyze a request. |
| POST | `/api/cases/{id}/orchestrate` | Start the LangGraph flow. |
| POST | `/api/cases/{id}/workflow` | Resume the flow (choice, rejection, clarification, consultant action). |
| POST | `/api/cases/{id}/capture` | Capture the current HCM state. |
| POST | `/api/cases/{id}/manual-proposal` | Consultant-authored validated plan. |
| POST | `/api/cases/{id}/review` | Review a proposal. |
| POST | `/api/cases/{id}/execute` | Apply the approved plan in staging. |
| POST | `/api/cases/{id}/verify` | Verify the applied operation(s). |
| POST | `/api/cases/{id}/confirm-result` | Approve the result and save the experience. |
| POST | `/api/cases/{id}/rollback` | Revert the application. |
| POST | `/api/cases/{id}/check-result`, `/validate` | Result check and validation. |
| POST | `/api/cases/{id}/obsolete` | Mark a case as obsolete (excluded from memory). |
| GET | `/api/companies` | Company search in HCM. |
| GET | `/api/unhandled` (`/api/consultant-queue`) | Unhandled queue. |
| GET | `/api/unhandled/capabilities` | Missing capabilities (with CSV export). |
| GET | `/api/interpretation-memories` | Intent memories. |
| POST | `/api/interpretation-memories/{id}/forget` | Forget an intent memory. |
| GET/POST | `/api/knowledge/settings` | Toggle knowledge sources. |
| GET | `/api/metrics` | Usage and results. |

---

## 18. Tests

### 18.1 Automated tests

Run inside the container to keep dependencies isolated:

```powershell
docker compose exec -T -e DATABASE_URL=sqlite:// backend python -m pytest -q
```

Frontend build:

```powershell
cd frontend
npm ci
npm run build
```

HCM has its own suite (`python -m pytest -q` in its `backend`), covering rules, isolation, permissions, atomic execution, idempotent retry, changed context, future effective dates, groups and rollback. ERP AI's suite also covers recovery from a lost response, bound approval, confirmation without manual evidence, exclusion of reverted cases from memory, orchestration, contract v2, multi-module plans, input guard, learning and model settings.

Tests with mocks do not prove model quality; the scenarios in `evaluation/` still require specialist review and functional comparison.

### 18.2 Live acceptance scripts

All of them are reversible and only touch staging.

| Script | What it does | Model cost | HCM writes |
|---|---|---|---|
| `scripts/smoke.py` | Live smoke test; writes only a rejected technical case in ERP AI. | — | None |
| `scripts/live_acceptance.py --execute` | One real proposal (Luna), automatic application, checks and exact rollback. Report in `artifacts/live-acceptance.json`. | One Luna generation | Temporary, reverted |
| `scripts/live_orchestration.py --execute --pause-after-guide`, restart the backend, then `--execute --resume` | LangGraph + HCM with a real provider across a backend restart: applies a reversible change, verifies, approves, reverts and disables the technical memory. Report in `artifacts/live-orchestration.json`. | Yes | Temporary, reverted |
| `scripts/live_named_config.py --execute` | Creates a temporary named configuration, assigns it to a group, checks that the group's effective rule now comes from the assignment, confirms and reverts. | None | Temporary, reverted |
| `scripts/live_hr_acceptance.py` | Real model → HR catalog → approved application → tests → exact rollback. | Yes | Temporary, reverted |
| `scripts/live_escalation.py` | Luna → Sol (high reasoning) → consultant. | Yes | None |
| `scripts/live_module_battery.py [round2] [module,…]` | One realistic request per HCM module through classification and interpretation. | Yes | None |
| `scripts/evaluate.py --execute` | Functional evaluation (see §11.3). | One call per scenario | None |

These scripts never change the saved model and never publish production. Technical cases are reverted and do not become approved experience.

---

## 19. Operations

- `docker compose stop` / `docker compose start` pause and resume.
- `docker compose down` preserves the volume. **Do not remove volumes to update.**
- **Backup:** `pg_dump` of ERP AI's own database; verify the restore in a separate database. Never use HCM's `reset_demo`.
- Rotating or removing the execution hash in HCM revokes write access without revoking query access.
- Rotating `SESSION_SECRET` ends all ERP AI sessions.

---

## 20. Security

- Eight-hour sessions, HttpOnly/SameSite cookie and origin check.
- Ports bound to localhost; database without a public port.
- Secrets in git-ignored local files (`.env`, `secrets/`, `.local-access.txt`), never in prompts, checkpoints, logs or responses.
- Every write requires human approval of the exact plan, an approval hash and a context check in HCM.
- The initial deployment targets local use by one specialist and one backend instance. A public deployment requires TLS, `COOKIE_SECURE=true`, individual identities, shared rate limits and external secret management.

---

## 21. Limits and known constraints

- ERP AI configures HCM modules for registered companies; it does **not** compute legal payroll, does **not** schedule split vacation periods per installment, and does **not** publish production automatically.
- When a module or requirement has no capability in HCM's catalog/connector, the case goes to Unhandled for consultant review.
- The model uses external memory; there is no automatic training of weights.
- Remote execution is not a distributed transaction: a pending state is resumed with the same idempotent key, and multi-module failures are compensated.
- Final approval is human and reversible as long as there are no later changes.
- Input checks reduce, but do not eliminate, prompt-injection risk.

---

## 22. Glossary of UI labels

| English | Portuguese (UI) |
|---|---|
| AI model | Modelo de IA |
| Cases | Atendimentos |
| Agent models | Modelos dos agentes |
| Analyze request | Analisar solicitação |
| None of these options | Nenhuma dessas opções |
| Approve and apply in staging | Aprovar e aplicar em homologação |
| Approve and save experience | Aprovar e guardar experiência |
| Revert this application in staging | Reverter esta aplicação em homologação |
| Knowledge | Conhecimento |
| Usage and results | Uso e resultados |
| Unhandled | Não tratados |
| Missing capabilities | Capacidades ausentes |
| Already like this today | Já está assim hoje |
| How to use | Como usar |
| Staging | Homologação |
| Vacation / Timekeeping / Hiring / Payslip / Benefits | Férias / Controle de Ponto / Admissão / Folha demonstrativa / Benefícios |
