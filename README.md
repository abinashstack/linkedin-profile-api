# LinkedIn Profile API

**Live deployment:** https://linkedin-profile-api-dwzr.onrender.com
([`/health`](https://linkedin-profile-api-dwzr.onrender.com/health),
[`/docs`](https://linkedin-profile-api-dwzr.onrender.com/docs)) — hosted on
Render's free tier, so the first request after a period of inactivity can
take ~30-60s while the instance spins back up.

A small HTTPS API that takes a public LinkedIn profile URL and returns
structured JSON: name, headline, location, about, experience, education,
skills, certifications, languages, and profile images.

It works by calling **LinkedIn's own internal "Voyager" JSON API directly**
over HTTPS — the same endpoint linkedin.com's web front-end calls when you
open a profile page. There is no browser automation anywhere in this
service (no Selenium/Playwright/Puppeteer, no HTML rendering); it's plain
HTTP requests with `httpx`, in and out.

## ⚠️ Before you use this

This uses LinkedIn's private, undocumented API and requires an
authenticated session. It **violates LinkedIn's Terms of Service**, and
LinkedIn actively detects and restricts accounts and IPs that do this,
including possible permanent bans. This project was built for a specific
take-home assignment as a demonstration of API reverse-engineering.

- Use a throwaway LinkedIn account, not your primary one.
- Don't run this against LinkedIn at any real volume or on a schedule.
- Only fetch profiles you have a legitimate reason to look up.

## How it works

1. **Auth.** LinkedIn's web app authenticates its Voyager API calls with an
   `li_at` session cookie plus a matching `JSESSIONID`/`csrf-token` pair.
   This service obtains that cookie one of two ways (see [Getting a
   LinkedIn session](#getting-a-linkedin-session) below), then reuses it
   for every request.
2. **Resolve the URL.** `https://www.linkedin.com/in/<public-id>/` is
   parsed to pull out `<public-id>`, LinkedIn's vanity identifier for the
   profile.
3. **Fetch.** `GET /voyager/api/identity/profiles/<public-id>/profileView`
   is called with the session cookies and the headers Voyager expects
   (`csrf-token`, `x-restli-protocol-version: 2.0.0`, etc.). This single
   endpoint returns most of a profile's sections in one response.
4. **Parse.** Voyager returns a *normalized* document: a flat `included`
   array of typed entities (each tagged with a `$type`, e.g.
   `com.linkedin.voyager.identity.profile.Position`), rather than one
   nested object. The parser (`app/parser.py`) scans that array by
   `$type` suffix and maps each entity type onto our response schema —
   see [Known limitations](#known-limitations) for the tradeoff this makes.
5. **Respond.** The parsed profile is returned as JSON and cached in memory
   for `CACHE_TTL_SECONDS` (default 1 hour) so repeat lookups of the same
   profile don't hit LinkedIn again immediately.

This general approach (raw HTTP calls to `/voyager/api/identity/profiles/...`,
authenticated with an `li_at` cookie) is well documented in the LinkedIn
reverse-engineering community — for example the open-source
[`linkedin-api`](https://github.com/tomquirk/linkedin-api) Python library
takes the same approach. The code here is an independent implementation,
scoped to what this assignment asks for.

## Project layout

```
app/
  main.py            FastAPI app and the /v1/profile, /health routes
  auth.py            Obtains an li_at session (cookie env var, or login)
  voyager_client.py  Raw HTTP client for LinkedIn's Voyager API
  parser.py          Normalizes a raw Voyager response into our schema
  models.py          Pydantic response models
  config.py          Environment-variable settings
  exceptions.py      Typed errors, mapped to HTTP status codes in main.py
tests/
  test_parser.py     Unit tests for the parser, against a fixture
fixtures/
  sample_profile_response.json  Synthetic Voyager-shaped response for tests
Dockerfile
render.yaml          Render.com deploy config
```

## Setup (local)

Requires Python 3.11+.

```bash
git clone <this-repo>
cd linkedin-profile-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Fill in `.env` — see the next section for how to get an `li_at` value —
then run:

```bash
uvicorn app.main:app --reload
```

The API is now at `http://127.0.0.1:8000`, with interactive docs at
`http://127.0.0.1:8000/docs`.

### Running the tests

```bash
pytest
```

The tests exercise the parser against a synthetic fixture and don't touch
LinkedIn — no credentials are needed to run them.

## Getting a LinkedIn session

The service needs an `li_at` cookie. There are two ways to provide one,
checked in this order:

### Option A — `LINKEDIN_LI_AT` (recommended)

1. Log into linkedin.com in a normal browser, with the account you're
   willing to use for this.
2. Open devtools → Application (Chrome) / Storage (Firefox) → Cookies →
   `https://www.linkedin.com`.
3. Copy the value of the `li_at` cookie.
4. Set it as `LINKEDIN_LI_AT` in `.env` (locally) or in your host's
   environment variables (when deployed).

This is the most reliable path. `li_at` cookies are typically valid for
weeks to a year; when LinkedIn eventually invalidates it (the API will
start returning 401s), just repeat the steps above and update the
environment variable.

### Option B — `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`

If `LINKEDIN_LI_AT` isn't set, the backend will instead try to log in on
its own: it `GET`s LinkedIn's login page for an anti-forgery token, then
`POST`s your credentials to the same endpoint the login form itself
submits to (`/uas/login-submit`). No browser is involved in this path
either — it's the same two raw HTTP requests a browser's network tab would
show during login.

This satisfies "use your own credentials" literally, but in practice
LinkedIn frequently responds to this flow with a security checkpoint
(CAPTCHA, or an emailed verification code) instead of a session — especially
from a cloud/datacenter IP, or the first time it sees a given IP or device.
When that happens the API can't proceed automatically and returns a clear
error (see [Known limitations](#known-limitations)); fall back to Option A.

A cookie obtained via login is cached to a local file
(`SESSION_CACHE_PATH`, default `./data/session_cache.json`, gitignored) so
the service doesn't log in again on every restart.

## Deployment (Render)

The repo includes a `Dockerfile` and `render.yaml`.

1. Push this repo to GitHub.
2. In Render, **New → Blueprint**, point it at the repo. It will read
   `render.yaml` and provision the service.
3. In the service's **Environment** tab, set `LINKEDIN_LI_AT` (or
   `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD`) as secrets. They are never
   committed to the repo.
4. Deploy. The service listens on the port Render assigns and exposes
   `/health` for the platform's health check.

Any other Docker-friendly host (Fly.io, Railway, a VM, etc.) works the same
way: build the `Dockerfile`, set the same environment variables, expose
port 8000.

**Note on hosting IPs:** LinkedIn is markedly more aggressive about
challenging traffic from known cloud/datacenter IP ranges than from
residential ones. A session that works fine when you test locally may get
challenged once deployed on a cloud host. If that happens, routing
`httpx`'s outbound requests through a residential/mobile proxy (via the
standard `HTTPS_PROXY` environment variable, which `httpx` honors
automatically) is the usual mitigation — this repo doesn't set one up for
you, but no code changes are needed to add one.

## Browser UI

`GET /` serves a single-page form (`app/static/index.html`): paste a LinkedIn
session cookie and a profile URL or bare handle (e.g. `satyanadella`), and it
renders the parsed profile in the browser, with a "Raw JSON" toggle. It's a
thin client over `POST /v1/profile` below — no separate backend.

The cookie you paste there is used only in memory to build a one-off request
to LinkedIn and is never written to disk, logged, or cached; only the parsed
profile output is cached (see `POST /v1/profile`). That said, an `li_at`
cookie grants full access to whatever LinkedIn account it belongs to, exactly
like a password — the page says as much, and the same caution applies if you
point other people at this UI.

## API documentation

### `GET /health`

Liveness check.

```json
{ "status": "ok", "authenticated": true }
```

`authenticated: false` means no LinkedIn session could be established at
startup (check the deploy logs / `LINKEDIN_LI_AT`).

### `GET /v1/profile?url=<linkedin-profile-url>`

Returns structured data for the given profile.

**Request**

```
GET /v1/profile?url=https://www.linkedin.com/in/some-person/
```

**Response — `200 OK`**

```json
{
  "public_id": "some-person",
  "profile_url": "https://www.linkedin.com/in/some-person/",
  "name": "Jane Doe",
  "headline": "Senior Software Engineer at Example Corp",
  "location": "San Francisco, California, United States",
  "about": "Backend engineer focused on distributed systems.",
  "profile_picture": { "url": "https://media.licdn.com/.../400_400.jpg", "width": 400, "height": 400 },
  "background_image": null,
  "experience": [
    {
      "title": "Senior Software Engineer",
      "company": "Example Corp",
      "company_urn": "urn:li:company:12345",
      "location": "San Francisco, CA",
      "employment_type": "Full-time",
      "starts_at": "2021-03",
      "ends_at": null,
      "description": "Working on backend systems."
    }
  ],
  "education": [
    {
      "school": "State University",
      "degree": "B.S.",
      "field_of_study": "Computer Science",
      "starts_at": "2014",
      "ends_at": "2018",
      "description": null,
      "activities": "ACM club"
    }
  ],
  "skills": ["Python", "Distributed Systems"],
  "certifications": [
    {
      "name": "AWS Certified Solutions Architect",
      "authority": "Amazon Web Services",
      "starts_at": "2022-05",
      "ends_at": null,
      "license_number": "ABC123",
      "url": "https://example.com/cert"
    }
  ],
  "languages": [{ "name": "English", "proficiency": "NATIVE_OR_BILINGUAL" }]
}
```

`ends_at: null` on an experience/certification entry means it's current
(no end date on the profile). Any field LinkedIn didn't return for a given
profile comes back as `null` (or an empty list for sections), not omitted.

**Errors**

| Status | Meaning |
|---|---|
| 400 | `url` isn't a `linkedin.com/in/<id>` profile URL |
| 401 | The configured session cookie was rejected (expired/invalid) — refresh `LINKEDIN_LI_AT` |
| 404 | No such profile, or it's not visible with this session (e.g. private) |
| 423 | LinkedIn returned a security checkpoint/CAPTCHA instead of data |
| 429 | LinkedIn rate-limited this session |
| 500 | No LinkedIn session could be established (check env vars / deploy logs) |
| 502 | LinkedIn returned something else unexpected |

Every error response is `{"detail": "<human-readable message>"}`.

### `POST /v1/profile`

Same result shape as `GET /v1/profile`, but authenticates with a session
cookie supplied in the request body instead of the server's configured one —
this is what the browser UI (`/`) calls. Useful for letting someone else use
this API with their own LinkedIn session rather than yours.

**Request**

```json
{ "url": "https://www.linkedin.com/in/some-person/", "li_at": "<their li_at cookie>" }
```

`url` also accepts a bare handle (`"some-person"`) instead of a full URL —
`GET /v1/profile` accepts the same. `li_at` is required; a missing or invalid
value returns `400` / `401` respectively. The cookie is used to build a
one-off client for this single request only — it is not cached, logged, or
reused for later requests.

Full interactive documentation (OpenAPI/Swagger) is served at `/docs` on
any running instance.

## Known limitations

- **Not tested against a live LinkedIn account.** This environment had no
  LinkedIn credentials available, so the request/parsing logic is built
  from well-documented Voyager API behavior and verified with a synthetic
  fixture (`fixtures/sample_profile_response.json`, `tests/test_parser.py`)
  rather than a real profile response. Before relying on this, run it
  against your own account and a few real profiles, and expect to adjust
  field names in `app/parser.py` if LinkedIn's actual response shape
  differs from what's assumed here.
- **LinkedIn's schema is undocumented and changes without notice.** The
  parser matches entities by `$type` suffix rather than threading every
  URN cross-reference, which is more resilient to small schema changes but
  can occasionally miss a section, misorder entries, or need a new
  `$type` suffix added if LinkedIn renames something.
- **Account/IP risk.** Scraping LinkedIn this way can get the backing
  account restricted or banned, and cloud-hosted IPs are more likely to be
  challenged than residential ones (see the deployment note above).
- **No CAPTCHA/checkpoint solving.** If LinkedIn challenges a login or a
  profile request, the API surfaces a clear error (423) rather than
  attempting to bypass it. There is no automatic retry/backoff beyond
  what's built into `httpx`'s defaults.
- **Single-instance, in-memory state.** The response cache and the
  disk-cached login session are local to one process; running multiple
  instances behind a load balancer means each maintains its own cache and
  may each attempt its own login.
- **Public profiles/URLs only.** Only `https://www.linkedin.com/in/<id>/`
  vanity URLs are supported — not `/pub/` legacy URLs, numeric profile
  IDs, or company pages.
- **No pagination for long sections.** LinkedIn paginates very long
  experience/education lists on its own site; `profileView` returns what
  it returns in one call, and this API doesn't page beyond that.
- **Rate limiting is naive.** There's no request queue or backoff strategy
  beyond LinkedIn's own 429s — hammering the endpoint will get you rate
  limited or challenged faster.

## Credentials

No secrets are committed to this repository. `.env` and the login session
cache (`data/`) are both gitignored — see `.env.example` for the variables
to set.
