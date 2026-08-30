# LinkedIn Profile API

**Live deployment:** https://linkedin-profile-api-dwzr.onrender.com
([`/health`](https://linkedin-profile-api-dwzr.onrender.com/health),
[`/docs`](https://linkedin-profile-api-dwzr.onrender.com/docs)) — hosted on
Render's free tier, so the first request after a period of inactivity can
take ~30-60s while the instance spins back up.

A small HTTPS API that takes a public LinkedIn profile URL and returns
structured JSON: name, headline, location, about, experience, education,
skills, certifications, languages, and profile images.

It works by calling **the same internal endpoints linkedin.com's own web
front-end calls** when you open a profile page — directly, over HTTPS, with
plain `httpx` requests. There is no browser automation anywhere in this
service (no Selenium/Playwright/Puppeteer, no HTML rendering engine).

**This is on its second architecture**, and that's worth reading before the
rest of this doc. The original version hit LinkedIn's classic Voyager REST
endpoint (`/voyager/api/identity/profiles/<id>/profileView`); partway
through building this, that endpoint started returning `410 Gone` against a
real account, and tracing it live (via a browser's DevTools Network tab)
showed LinkedIn has migrated profile rendering entirely to a React Server
Components ("Flight" protocol) system — real content now comes back as a
serialized UI-component tree, not a JSON data object. Section 3 below
covers what that means for how this parses data, and it's the reason this
project reads less like "call an API, get clean JSON" and more like
"replay a specific request, then recover text from a rendered tree." That
shift is real and is the main thing to understand about this codebase.

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

1. **Auth.** LinkedIn's web app authenticates every internal request with an
   `li_at` session cookie plus a matching `JSESSIONID`/`csrf-token` pair.
   This service obtains that cookie one of two ways (see [Getting a
   LinkedIn session](#getting-a-linkedin-session) below), then reuses it
   for every request.
2. **Resolve the URL.** `https://www.linkedin.com/in/<public-id>/` is
   parsed to pull out `<public-id>`, LinkedIn's vanity identifier for the
   profile — this is also literally the value LinkedIn's own request body
   sends as `vanityName` (see step 4), so no extra ID-resolution call is
   needed.
3. **Fetch the basics from the page's own HTML.** `GET /in/<public-id>/` is
   requested like a browser would, and `name`/`headline`/`location`/
   `profile_picture` are read from that page's standard `<title>` and Open
   Graph meta tags (`og:title`, `og:description`, `og:image`) — the same
   tags any page exposes for link-preview cards on other sites. This is a
   deliberately different, more boring target than the SDUI system in step
   4: meta tags are conventional HTML, essentially the most stable part of
   any page, and sidestep the whole component-tree problem for exactly the
   fields simple enough not to need it.
4. **Fetch the deep sections via LinkedIn's UI-rendering endpoint.** About,
   Experience, Education, and Skills each come from a separate
   `POST /flagship-web/rsc-action/actions/component` call (`componentId` =
   `...aboutTopLevelSection` / `...experienceTopLevelSection` /
   `...educationTopLevelSection` / `...skillsTopLevelSection`, body
   `{"clientArguments": {"payload": {"vanityName": "<public-id>",
   "isSelfView": false}, ...}}`). This is
   the endpoint LinkedIn's own frontend calls to render those cards — found
   by watching real Network traffic in DevTools, not by guessing. Its
   response is a React Server Components ("Flight" protocol) stream: lines
   of `<chunk-id>:<payload>`, mostly JSON tuples shaped like
   `["$", elementType, key, props]` — literally a serialized UI tree, with
   real content (job titles, company names, dates) sitting inside it as
   plain rendered text, not as named fields like `companyName` or
   `startDate`. `app/sdui_parser.py` recovers that text by recognizing a
   few recurring component "shapes" (a `<p>` with one className is always a
   title, another className is always a subtitle, one generic text
   component holds short strings like dates, another holds expandable
   descriptions) rather than by resolving the tree the way React itself
   would. See its module docstring and [Known limitations](#known-limitations)
   for exactly what that tradeoff costs.
5. **Combine and respond.** `app/parser.py` merges the meta-tag basics and
   the four parsed cards into one `ProfileResponse`, cached in memory for
   `CACHE_TTL_SECONDS` (default 1 hour) so repeat lookups of the same
   profile don't hit LinkedIn again immediately. One profile lookup is five
   requests to LinkedIn (HTML page + 4 component calls), not one — worth
   knowing when thinking about rate limits or batch sizing.

The general shape of steps 1-2 (raw HTTP calls, `li_at` cookie auth) is
well documented in the LinkedIn reverse-engineering community — the
open-source [`linkedin-api`](https://github.com/tomquirk/linkedin-api)
Python library (now private; see below) used to be the standard reference
for it, and a community fork,
[`open-linkedin-api`](https://github.com/EseToni/open-linkedin-api), still
documents the classic REST flow. Neither describes the SDUI system in step
4 — that part was traced from scratch, live, against a real profile page,
because the endpoint they document (`/voyager/api/identity/profiles/.../profileView`)
is the one now returning `410 Gone`.

## Project layout

```
app/
  main.py            FastAPI app and the /v1/profile, /health routes
  auth.py            Obtains an li_at session (cookie env var, or login)
  voyager_client.py  Fetches the profile HTML page + 4 SDUI component calls
  sdui_parser.py     Recovers text from LinkedIn's Flight-protocol UI tree
  parser.py          Combines both into our ProfileResponse schema
  models.py          Pydantic response models
  config.py          Environment-variable settings
  exceptions.py      Typed errors, mapped to HTTP status codes in main.py
tests/
  test_parser.py       Unit tests for parser.py, against a fixture
  test_sdui_parser.py  Unit tests for sdui_parser.py, against synthetic
                        fixtures modeled on a real captured response
fixtures/
  sample_profile_response.json   Synthetic raw dict for parser.py's tests
  sdui_experience_response.txt   Synthetic Flight-protocol response
  sdui_about_response.txt        (same, for the About card)
  sdui_skills_response.txt       (same, for the Skills card -- different
                                  shape, and uses a different alias number
                                  for the same component on purpose)
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

`GET /` serves a single-page form (`app/static/index.html`): enter one or
more profile URLs/bare handles (e.g. `satyanadella`), one per line, and it
renders each parsed profile in the browser, with a "Raw JSON" toggle per
profile. It's a thin client over `POST /v1/profiles/batch` below (a single
line is just a batch of one) — no separate backend.

By default it looks up profiles using the session already configured on the
server (`LINKEDIN_LI_AT`) — that's the point of setting that up: paste your
own cookie once in the deploy environment, then look up any profile from the
page without re-entering anything. There's also an optional "session cookie
override" field for looking something up with a *different* LinkedIn
session instead; if you use it, that cookie is used only in memory to build
a one-off request and is never written to disk, logged, or cached (only the
parsed profile output is cached, and only for the server-session path — see
`POST /v1/profile`). An `li_at` cookie grants full access to whatever
LinkedIn account it belongs to, exactly like a password, so the same caution
applies if you ever point other people at this UI and let them use the
override field with their own.

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
      "employment_type": "Full-time",
      "location": "San Francisco, CA",
      "date_range": "2021 - Present",
      "description": "Working on backend systems."
    }
  ],
  "education": [
    {
      "degree": "B.S., Computer Science",
      "school": "State University",
      "date_range": "2014 - 2018",
      "description": null
    }
  ],
  "skills": ["Python", "Distributed Systems"],
  "certifications": [],
  "languages": []
}
```

`date_range` is whatever free-text date range LinkedIn's page itself shows
(e.g. `"2007 – Present"`, `"Jan 2024 - Present · 2 yrs 8 mos"`) — see
[Known limitations](#known-limitations) for why this isn't split into
separate start/end fields. `certifications` and `languages` are always
empty lists right now — not a bug, see the same section; `skills` only
includes what LinkedIn's default view already expanded, not a profile's
full list if it has more. Any other field LinkedIn didn't return for a
given profile comes back as `null` (or an empty list for sections), not
omitted.

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

Same result shape as `GET /v1/profile`. `li_at` is optional: omit it (or send
an empty string) and this behaves exactly like `GET /v1/profile`, cache
included. Supply it and this behaves like `GET`'s per-request cousin,
authenticating with that cookie instead — useful for letting someone else
use this API with their own LinkedIn session rather than yours. This is what
the browser UI (`/`) calls either way.

**Request**

```json
{ "url": "https://www.linkedin.com/in/some-person/", "li_at": "<optional override cookie>" }
```

`url` also accepts a bare handle (`"some-person"`) instead of a full URL —
`GET /v1/profile` accepts the same. When `li_at` is supplied, it's used to
build a one-off client for this single request only — it is not cached,
logged, or reused for later requests.

### `POST /v1/profiles/batch`

Looks up several profiles in one call, using one session for all of them.

**Request**

```json
{ "urls": ["satyanadella", "https://www.linkedin.com/in/some-person/"], "li_at": "<optional override>" }
```

`li_at` is optional here — omit it to use the server's configured session,
same as `GET /v1/profile`. Up to `BATCH_MAX_SIZE` URLs per call (default 20;
`400` above that).

**Response — `200 OK`**

```json
{
  "results": [
    { "url": "satyanadella", "ok": true, "profile": { "...": "..." }, "error": null },
    { "url": "https://www.linkedin.com/in/some-person/", "ok": false, "profile": null, "error": "No profile found for 'some-person' (or it's private)." }
  ]
}
```

The response is always `200` even if individual profiles failed — check
each result's `ok` field. Requests are made **one at a time with a short
delay in between** (`BATCH_DELAY_SECONDS`, default 1.5s), not in parallel: a
burst of simultaneous requests is a far stronger signal to LinkedIn's abuse
detection than the same requests spread out. If LinkedIn responds with a
challenge, a rate limit, or an expired session partway through the batch,
the whole batch **stops immediately** — every remaining URL comes back with
`ok: false` and an error explaining it was skipped, rather than also being
attempted against a session that's clearly already being flagged.

This is still a single, synchronous HTTP request under the hood, so very
large batches can run into the host's own request timeout — that's a
reason to keep `BATCH_MAX_SIZE` conservative rather than raise it, not a bug
to work around.

Full interactive documentation (OpenAPI/Swagger) is served at `/docs` on
any running instance.

## Known limitations

- **The individual pieces are verified against real LinkedIn traffic; the
  full pipeline as committed here is not.** The HTTP calls in
  `voyager_client.py` (the HTML page fetch and all four component POSTs)
  were confirmed live, getting real `200` responses with real data back —
  and `sdui_parser.py`'s extraction logic was verified against real
  captured `experienceTopLevelSection` and `skillsTopLevelSection`
  responses and matches both correctly. What hasn't been run end-to-end in
  this repo is the whole pipeline
  wired together against a live account (the synthetic fixtures in
  `tests/` model the real shape but aren't the real response). Run it
  against your own account and a few real profiles before trusting it
  further, and expect to adjust `app/parser.py`'s meta-tag assumptions in
  particular if they don't match.
- **LinkedIn's rendering system is undocumented, changes without notice,
  and already changed once during this project.** The original REST
  endpoint this was built against (`/voyager/api/identity/profiles/.../profileView`)
  is dead — confirmed live, mid-project, as `410 Gone`. The current
  implementation targets what replaced it (see "How it works" above), but
  that replacement is itself an internal UI-rendering system with no
  external documentation, discovered by watching one real browser session's
  network traffic. It could change again with no warning, the same way its
  predecessor did.
- **`app/sdui_parser.py` recovers text by pattern, not by a stable schema.**
  There is no `companyName` or `startDate` field anywhere in LinkedIn's
  response — this parser recognizes recurring UI component "shapes"
  instead (a specific CSS-in-JS className means "this is a title", a
  specific component type means "this is a short text field"). Those
  classNames (`c2d1c236` for a title, `_61558a10` for a subtitle,
  hard-coded in `sdui_parser.py`) are generated hashes tied to LinkedIn's
  current frontend build and **will** change on their next rebuild, at
  which point extraction breaks silently (empty/garbled fields, not an
  error) until the new hashes are found the same way these were: capture a
  real response in a browser's DevTools and diff it against what's here.
- **Descriptions are matched to entries positionally, not by any id that
  actually ties the two together.** If entry 2 of 5 has no description but
  entries 1, 3, 4, and 5 do, the description meant for entry 3 will attach
  to entry 2 instead, and every later one shifts by one. This is a
  documented, deliberate tradeoff (see `sdui_parser.py`'s
  `extract_card_entries` docstring) rather than an oversight — fixing it
  properly means walking the full component tree instead of pattern-matching
  chunk shapes.
- **`educationTopLevelSection` is assumed, not verified.** Its component ID
  follows the exact same naming convention as `experienceTopLevelSection`
  (verified) and `aboutTopLevelSection` (verified), and the design system
  strongly suggests the same title/subtitle/dates/location/description
  shape — but no real education-bearing response was captured to confirm
  it. If it's wrong, education will come back empty rather than error.
- **Skills are implemented; certifications and languages are not.**
  `skillsTopLevelSection` was confirmed live and decoded from a real
  captured response (`fixtures/sdui_skills_response.txt` models its exact
  shape). Its data layout is different from Experience/Education — no
  title/subtitle `<p>` pair, just the generic Text component reused at two
  font weights (bold+medium = a skill name, normal+small = supporting
  detail like endorsement counts), so `sdui_parser.extract_skills` matches
  on `fontWeight` rather than a className. It only returns skills LinkedIn
  already expanded into the response (a profile with many skills has a
  "Show all" button to a separate screen this doesn't follow).
  Certifications/languages never got a confirmed, stable component ID —
  the closest lead, a set of generically-numbered
  `profileCardsBelowActivityPart1` through `7` components, turned out to
  be *numbered per-profile based on which sections happen to be filled
  in* rather than a fixed convention (`part4` meant Languages on the one
  profile checked; nothing says it means that anywhere else). Hard-coding
  that mapping would be guessing, so both remain empty lists.
- **A real bug this surfaced and fixed: component alias numbers are
  per-response, not global.** A `"$L20"`-style element type is a local
  alias into a per-response module-declaration table, not a stable ID —
  the exact same generic Text component was aliased `$L20` in the
  Experience response and `$Ld` in the Skills response (confirmed: both
  aliases point to the identical module hash). An earlier version of
  `sdui_parser.py` matched on the literal alias string, which only worked
  by coincidence for the one response it was built against. It now
  resolves every element to its module hash first (see the module
  docstring), which is what actually made Skills extraction possible —
  and fixed a latent bug in the Experience/Education parsing too.
- **Name/headline/location come from Open Graph meta tags, not the SDUI
  system** — a deliberate, more stable choice (see "How it works"), but the
  exact tag format assumed in `app/voyager_client.py`/`parser.py`
  (`"<Name> - <Headline> | LinkedIn"` for the title, location leading
  `og:description` before a `·`) is standard practice, not something
  independently re-verified against a live capture in this project's own
  testing. If LinkedIn's actual tags differ, name/headline/location parsing
  will need adjusting the same way the rest of this did.
- **Five requests per profile lookup, not one.** The HTML page plus four
  SDUI component calls (about/experience/education/skills) all happen
  sequentially for a single `GET /v1/profile` call —
  `BATCH_DELAY_SECONDS` paces between *profiles* in a batch, not between
  these five sub-requests for the same profile, so a batch's real request
  volume against LinkedIn is `5 × number of URLs`, not `1 ×`. Worth
  knowing when thinking about detection risk.
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
- **No pagination for long sections.** A profile with many positions
  (LinkedIn's own UI showed 17 for one profile checked during development)
  only returns the ones the default card renders, with no code here to
  follow LinkedIn's own "Show all" pagination into a details sub-screen.
- **Rate limiting is naive.** There's no request queue or backoff strategy
  beyond LinkedIn's own 429s — hammering the endpoint will get you rate
  limited or challenged faster.

## Credentials

No secrets are committed to this repository. `.env` and the login session
cache (`data/`) are both gitignored — see `.env.example` for the variables
to set.
