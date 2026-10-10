# CivicResolve (controlled hackathon demo)

FastAPI + SQLite with a citizen portal and an authenticated administrator portal. Citizen submission persists a report, runs the existing saved ML models, derives conservative recurrence evidence, then runs the deterministic 35/25/15/15/10 priority engine. Gemini is **admin-triggered only**.

## Run on Windows

```powershell
cd D:\Hackathon
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.init_admin
.\run_civicresolve.ps1
```

Choose a unique admin ID and a password of at least 12 characters when prompted; there is no default password. Open `http://127.0.0.1:8000/` for role selection. Citizen pages are at `/citizen`, `/report` and `/track`; the administrator login is at `/portal/admin/login`. `/docs` shows API schemas. For a fresh environment, use Python 3.11+ and install the requirements. The database defaults to `data/complaints.sqlite3`; set `COMPLAINT_DB_PATH` **before** both admin setup and server startup to use another SQLite file. Example: `$env:COMPLAINT_DB_PATH = 'D:\Hackathon\data\demo.sqlite3'`. Do not put a live DB in a publicly served directory.

## Website workflow

Choose **Citizen** to submit a report without an account. Save the complete private `ID:token` reference from the confirmation screen; use it on **Track Complaint** to see the recorded workflow status. Choose **Administrator** to sign in with the account created by `app.init_admin`. The browser keeps the bearer token in memory and sends it in the Authorization header; refreshing an admin page requires a new login. Logout revokes the server session. Admin pages and API requests remain protected by backend authentication.

The dashboard lists paginated complaints with backend search, filters and priority/date sorting. Dashboard totals come from `/admin/summary` and cover all records, regardless of the current page. Emergency flags are shown independently of priority; missing scores are labeled pending evaluation. Open a complaint to review its report, ML category and severity, and current priority summary. Continue to **Action & Allocation** for operational decisions. Browser Back and Forward move between these two screens.

On **Action & Allocation**, **Generate AI Recommendations** calls the server-side provider. Its output is advisory. An optional population-impact suggestion can be accepted, ignored, or modified after a backend preview and explicit confirmation. The original priority is never overwritten. Resource allocation uses synchronized sliders and number fields bounded by catalog stock, plus the configured budget policy from **Resources**. Verified inputs can reserve stock, while unverified inputs produce `pending_verification` with no reservation. Status changes follow Submitted → Under Review → Assigned → Resolved separately from AI and allocation decisions. Administrative history remains available in a disclosure. Duplicate linking and tracking credential revoke/rotate controls are hidden from the normal UI; their authenticated backend endpoints remain available for controlled administrative use.

No resource inventory or budget is seeded. **Resources** can add or edit catalog items and a configured budget ceiling. **Load Demo Resources** explicitly adds three synthetic, unverified items after confirmation. Its authenticated backend action creates only missing items under a database write lock, so repeated use never overwrites existing inventory. The budget policy is a configured ceiling, not a real appropriation. The UI does not show a remaining balance because the backend checks committed allocations only when an approval is submitted.

`GEMINI_API_KEY` is optional until an admin explicitly requests a recommendation. FastAPI loads `.env` from the project root at startup; existing process environment variables take precedence. Keep the key server-side, never in browser code. `GEMINI_MODEL` defaults to `gemini-2.5-flash-lite`; see `.env.example`. Without a key, the recommendation endpoint returns a structured 503, while submission/tracking/priority continue to work. Automated Gemini tests use mocks and do not make a live request.

For a local persistent Gemini key, keep it in the ignored project-root `.env`. If `.env` is absent, `run_civicresolve.ps1` can still read a Windows user environment variable. The launcher contains no key and can be committed; never commit `.env` or the key itself.

## Citizen and admin API

Citizen routes are same-origin and do not require an account:

| Route | Purpose |
| --- | --- |
| `POST /complaints` | Submit; returns ID, private `tracking_token`, limited receipt, HTTP 201. Repeating the same body and `Idempotency-Key` returns the same receipt, HTTP 200. |
| `POST /complaints/{id}/track` | Body `{ "token": "..." }`; returns only status, processing state, estimated category/review markers, title, and timestamps. No description, location, severity, or priority details. Rate limited. |
| `POST /classify` | Standalone ML inference; no persistence. |
| `POST /priority/calculate` | Standalone deterministic scoring; no Gemini or persistence. |

`GET /complaints` and `GET /complaints/{id}` remain for compatibility but now require admin bearer authentication. A UUID **alone is not a citizen tracking credential**. The Citizen Portal copies the full `ID:token` reference; keep it private. The token is sent in a POST body, not a URL. The database stores a SHA-256 verifier, not the token. Idempotent replay derives the same token from an application secret stored in SQLite; therefore both the database and its backups must be kept confidential. Treat `Idempotency-Key` as sensitive as a tracking reference.

Example citizen submission:

```powershell
$body = @{ title='Road cavity'; description='Open manhole beside the school entrance'; location='Mansarovar Jaipur'; affected_area='street_lane'; days_existing=3; location_tags=@('school') } | ConvertTo-Json
$receipt = Invoke-RestMethod http://127.0.0.1:8000/complaints -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key'='demo-submission-001'} -Body $body
$receipt | ConvertTo-Json
Invoke-RestMethod "http://127.0.0.1:8000/complaints/$($receipt.id)/track" -Method Post -ContentType 'application/json' -Body (@{token=$receipt.tracking_token} | ConvertTo-Json)
```

The first response includes `{ "id": "<UUID>", "tracking_token": "<64-character secret>", "title": "Road cavity", "status": "Submitted", "processing_status": "completed", "created_at": "<UTC timestamp>" }` (plus limited triage indicators and reported location). The tracking response includes status, processing status, title, category/review indicator, and UTC timestamps, but **not** the description, location, severity, or priority score. Do not display the token in logs or an admin queue.

Admin authentication (bearer token, eight-hour expiry, server-side revocation):

```powershell
$login = Invoke-RestMethod http://127.0.0.1:8000/admin/auth/login -Method Post -ContentType 'application/json' -Body (@{username='YOUR_ADMIN_ID'; password='YOUR_PASSWORD'} | ConvertTo-Json)
$headers = @{Authorization="Bearer $($login.access_token)"}
Invoke-RestMethod http://127.0.0.1:8000/admin/complaints -Headers $headers
```

The login response contains `{ "access_token": "<opaque token>", "token_type": "bearer", "expires_at": "<UTC timestamp>" }`. The queue response is `{ "items": [...], "page": 1, "page_size": 20, "total": 0, "total_pages": 0 }`; each item has a description summary, workflow/processing state, emergency flag, original priority, and `effective_priority_score`/`effective_priority_level`. Null priorities remain null.

Use `POST /admin/auth/logout` with the same header to revoke the session, and `GET /admin/auth/me` to validate it. No auth token is placed in a browser cookie, so CSRF risk is reduced; an eventual admin UI must keep the token out of localStorage and prevent XSS. Login and tracking attempts have basic per-process IP rate limits, not distributed enforcement.

Admin routes (all require `Authorization: Bearer <token>`):

| Route | Contract |
| --- | --- |
| `GET /admin/complaints` | `page`, `page_size`, `search`, `category`, `severity`, `priority_level`, `status`, `processing_status`, `emergency_only`, `sort_by=priority|submitted_at`, `order=asc|desc`. Emergency first; incomplete scores remain null/visible. |
| `GET /admin/summary` | Unfiltered total, Submitted/pending-review, emergency-review and Resolved counts. |
| `GET /admin/complaints/{id}` | Full complaint, original scoring, effective priority, recommendations, decisions, allocations, status history. |
| `POST /admin/complaints/{id}/duplicate-link` | `{ "root_id": "UUID", "reason": "..." }`; confirm the same unresolved incident after human verification; original priority snapshot is untouched. |
| `POST /admin/complaints/{id}/tracking/revoke` / `/tracking/rotate` | Revoke a leaked credential or issue a new one to an identity-verified citizen. Rotation returns the token once; old references and submission-key replay stop working. |
| `POST /admin/complaints/{id}/recommendations` | One Gemini call using current complaint and catalog; saves advisory result. No automatic approval. |
| `GET /admin/complaints/{id}/recommendations` | Saved recommendations. |
| `POST /admin/complaints/{id}/recommendations/{recommendation_id}/decision` | `{ "decision": "accept_for_review"|"reject", "reason": "..." }`. Acceptance does **not** allocate or change priority. |
| `POST /admin/complaints/{id}/population/preview` | Body `{ "decision": "accept"|"reject"|"manual", "recommendation_id": "UUID or null", "manual_score": 0..100 or null, "reason": "..." }`; no write. |
| `POST /admin/complaints/{id}/population/decisions` | Same body, plus `Idempotency-Key`; creates auditable human decision. Only an approved/manual population score changes effective priority; original remains unchanged. |
| `GET /admin/resources` | Persistent catalog. |
| `POST /admin/resources/demo` | Explicitly add missing synthetic, unverified catalog items; existing items are preserved. |
| `GET /admin/resources/budget/policy` | Current configured budget ceiling and verification status, or an unverified empty policy. |
| `PUT /admin/resources/{resource_id}` | Upsert `{id,name,kind,available_quantity,unit_cost,verified,demo_data}`; kind is `workforce`, `equipment`, or `materials`. No resources are silently seeded. |
| `PUT /admin/resources/budget/policy` | Set `{max_amount,currency:"INR",verified}`. This is a configured demo budget ceiling, not a real appropriation. |
| `POST /admin/complaints/{id}/allocations` | Body `{decision:"approve"|"reject",recommendation_id?,items:[{resource_id,quantity}],approved_budget?,remarks?}`, plus `Idempotency-Key`. Verified stock/cost/budget permits an atomic reservation. Unknowns yield `pending_verification` and **no stock decrement**. |
| `GET /admin/complaints/{id}/allocations` | Allocation history. |
| `POST /admin/complaints/{id}/status` | `{ "status": "Under Review"|"Assigned"|"Resolved", "remarks": "..." }`; only the next transition is permitted. |
| `GET /admin/complaints/{id}/status-history` | Actual transitions and administrator attribution. |
| `GET /admin/complaints/{id}/audit` | Administrative action history for the complaint. |

The admin UI should log in, keep bearer tokens in memory, show emergency/incomplete cases in the queue, require explicit review of Gemini's population and resource suggestions, preview before approval, then move workflow status separately. Never equate a recommendation/allocation with resolution.

## Scoring, ML, and data

The five factor weights remain severity 35%, estimated population impact 25%, recurrence 15%, duration 15%, and location 10%. Original scores and factor details live in the `complaints` table and are never overwritten by Gemini. Approved population revisions live separately in `population_decisions`. Missing recurrence produces a **null** priority, not zero. Emergency review flags are independent of score. Citizen-supplied location tags are unverified; no geography or actual population count is invented.

The default classifier loads `models/complaint_model (1).pkl` once per worker. It contains separate category and severity pipelines trained by the teammate with scikit-learn 1.6.1; `requirements.txt` pins that version for safe loading. No model is trained at startup. The original `models/category.joblib` and `models/severity.joblib` are retained and copied under `models/rollback/`. To roll back, install scikit-learn 1.9.1 (the version that saved the original models) with `.\.venv\Scripts\python.exe -m pip install scikit-learn==1.9.1`, set `$env:COMPLAINT_MODEL_SOURCE = 'legacy'`, and restart `./run_civicresolve.ps1`. To return to the combined model, install the pinned requirements, remove that variable, and restart. Existing complaint rows are never reclassified by switching models.

`models/evaluation.json` contains the original model's random, template-held-out, and different-wording evaluations. A separate fixed set of unfamiliar descriptions is in `tests/model_replacement_evaluation.csv`. On its 20 supported-category cases, the teammate model scored 20/20 category, 18/20 severity, and 8/9 high-severity recall. One serious open-drain case was predicted Low; the independent emergency detector now flags uncovered drains for human review without changing the predicted severity or priority weights. Five out-of-domain examples showed that a closed-set classifier can still assign a supported label to an unsupported issue; low category confidence raises a review flag, but high-confidence out-of-domain predictions remain possible. Treat classifications and probabilities as triage aids, not verified facts.

New SQLite tables are `app_secrets`, `tracking_credentials`, `administrators`, `admin_sessions`, `recommendations`, `recommendation_reviews`, `population_decisions`, `resources`, `budget_policy`, `resource_allocations`, `status_events`, `duplicate_reviews`, and `audit_events`. No existing complaint columns are altered or deleted. On first startup against an existing database lacking the new tables, the app makes a timestamped SQLite backup beside it before additive `CREATE TABLE` statements. Subsequent startups are repeatable. Keep the backup and database private. Existing/legacy complaints **do not** receive invented tracking credentials; they are visible to admins, not to public tracking. If a legacy citizen needs access, handle identity verification through a separate controlled migration process; there is no automatic public endpoint for it.

## Tests and limitations

```powershell
$env:COMPLAINT_DB_PATH = 'D:\Hackathon\data\isolated-test.sqlite3'
.\.venv\Scripts\python.exe -m pytest -q
node --test tests\frontend_api.test.mjs tests\frontend_retry.test.mjs tests\frontend_admin_api.test.mjs
```

Tests override database sessions with temporary SQLite files and mock Gemini. Do not put real complaints in the test path. The integration test submits, reviews, adjusts, allocates, resolves, and reopens the database. The real Gemini API is not tested without an explicitly authorized smoke test and configured key.

For browser-only offline verification, `tests/mock_browser_app.py:app` injects a deterministic test provider; run it only with an isolated `COMPLAINT_DB_PATH` and never as the normal server entry point. The browser demo used a synthetic complaint, a test administrator, demo-tagged verified resources and a demo budget in a separate SQLite file.

Known UI limitation: the queue API supports priority and submission-date sorting, not severity sorting. Login state intentionally does not survive a page refresh. Recommendation generation is not idempotent on the backend, so administrators should wait for a response before requesting another. The frontend prevents concurrent clicks while a request is in progress.

Security remains **controlled-demo only**: bearer tokens and tracking capabilities can be stolen by XSS, browser extensions, local malware, or leaked logs/backups; TLS is needed beyond localhost. Rate limits are in-memory and per-process. There is no verified geographic data, actual population count, verified municipal stock, or real budget unless an administrator supplies it. SQLite is appropriate for a local demo, not high-volume multi-worker deployment. Classification probabilities are not calibrated reliability guarantees. Administrators must verify hazardous cases and operational facts before dispatch.

Troubleshooting: if admin login fails, run `python -m app.init_admin` against the same `COMPLAINT_DB_PATH` used by the server. If Gemini returns `missing_api_key`, set `GEMINI_API_KEY` in the server environment and restart. If a copied reference fails, ensure it includes both ID and token; legacy complaints have no token. If models are missing, restore trusted `.joblib` files; never load untrusted Joblib data.
