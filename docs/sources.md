# Job boards

Every board reaches the engine through the `JobSource` Protocol and nothing else, so a board's
quirks stop at its adapter. Four ship today.

| Board | Access | Eligibility data | Pay |
|---|---|---|---|
| Himalayas | JSON API | `locationRestrictions` and `timezoneRestrictions`, structured, per posting | figures, period inferred from magnitude |
| RemoteOK | JSON API | none, so its postings take the text path | figures, currency assumed USD |
| Remotive | JSON API | `candidate_required_location`, mixing places and timezone bands | free text, period usually stated |
| WeWorkRemotely | RSS | `country`, ISO 3166 names; its `region` is **not** a restriction | none published |

## Adding one

A new board is one adapter plus one line in `sources/defaults.py`, plus whatever new names for the
world it introduces. In order:

1. **Probe the live board first and write down what you find.** Every fact in this file was
   measured, and several contradicted what the board's own documentation implied. Do not design
   against a schema you have not seen answer.
2. **Write the adapter** in `infrastructure/sources/<board>.py`. It needs `name` as a class
   attribute, `is_available()` (no I/O), and `fetch(query) -> SourceResult`.
3. **`fetch` must never raise.** It runs in a thread pool beside the other boards, so a failure is
   a `SourceResult` with `error` set. Use `base.get_json` or `base.get_xml`, which turn a decoding
   failure into an `httpx.HTTPError` your existing catch already handles.
4. **Use `scanning.collect`** unless the board paginates. It honours the scan depth and the age
   window and reports whether records were left unread, which is what `truncated` must reflect.
   Himalayas does not use it and says why in its own module.
5. **Report coverage honestly.** `truncated=True` whenever the board had more to give, including
   when it only ever publishes a window of a larger corpus. `SearchResult.fully_scanned` is derived
   from this, and an agent is told a partial run was complete if you get it wrong.
6. **Report eligibility as the board stated it, never as you interpret it.** `None` means the board
   said nothing, `()` means it stated there is no restriction. Collapsing those is the worst failure
   this engine has. If a board's field is unreliable, do not launder it into a structured claim:
   WeWorkRemotely's `region` says "Anywhere in the World" on postings restricted to the US, so that
   adapter reports only `country` and passes `region` through as location text.
7. **Declare pay in the board's own terms.** `base.salary_from_bounds` requires the currency, where
   the currency came from, and the period, with no defaults, so a new adapter cannot skip the
   question and publish figures nobody can compare.
8. **Register it** in `sources/defaults.py`, and **add a sample payload** to `PAYLOADS` in
   `tests/infrastructure/sources/test_source_contract.py`. A test asserts that table covers every
   registered board, so a new adapter cannot opt out of the contract by not being listed.
9. **Add any new spellings of the world** to `domain/regions.py`. A board that calls a country
   something no other board calls it is adding a spelling, not a rule. WeWorkRemotely added eleven.

The contract test then holds the board to the query it was given: the depth bounds what comes back,
a bounded scan reports truncated, `scanned` counts what was read, and postings outside the age
window do not return.

## What each board actually does

Captured from live runs. Where a board's behaviour contradicts its documentation, this records the
behaviour.


- **Himalayas**: `GET https://himalayas.app/jobs/api?limit=20&offset=N`. `limit` is capped at 20 per
  page regardless of what you pass. Filter params (`title=`, `search=`, `category=`) are **ignored**;
  the API always returns the full recency-ordered feed (~103k live postings). So you paginate and
  filter client-side. Job fields include `title, excerpt, companyName, minSalary, maxSalary, currency,
  seniority, locationRestrictions (list[str]), timezoneRestrictions (list[float]), categories (list),
  description, pubDate, applicationLink, guid`. A full scan is ~5,155 pages; be polite (~0.15s delay,
  back off on HTTP 429, stop after 3 consecutive empty pages).
- **Remotive**: `GET https://remotive.com/api/remote-jobs?category=software-dev`. Under load it
  throttles and ignores `search=`/`category=` (returns the same ~39). Fields: `title, company_name,
  description, url, publication_date, salary, candidate_required_location, job_type`.
  `candidate_required_location` holds two kinds of value in one comma-separated list: places
  ("Germany", "Europe", "Worldwide") and timezone bands ("European timezones", "USA timezones",
  4 of 20 postings in one window). A band is not a place, and the adapter routes it to
  `timezone_restrictions` so the timezone rule reads it.
- **RemoteOK**: `GET https://remoteok.com/api`. First array element is legal boilerplate, skip it.
  Filter by `tags`. Default DevJobsHub filter is dev-only; **broaden** to include ai/ml/llm/
  machine-learning/data tags. Fields: `position, company, description, url, date, tags, salary_min,
  salary_max, location`.
- **WeWorkRemotely**: RSS at `https://weworkremotely.com/remote-jobs.rss`, exactly 100 items, ten
  per category. `?page=2` returns the same hundred, so there is nothing to paginate. Fields:
  `title, region, country, state, skills, category, type, description, pubDate, expires_at, guid,
  link`. Title is `"Company: Role"`, split on the first `": "`. `pubDate` and `expires_at` are
  RFC 2822 dates, not relative ones. There is no pay field.
  **`region` is not a restriction:** 93 of 100 items say "Anywhere in the World" and 14 of those
  also name a `country` that restricts them, one to the US alone. `country` is the eligibility
  field. It lists ISO 3166 official names ("United States of America"), each prefixed by its flag
  emoji, separated by commas with an Oxford "and"; split on the flags, since "Bosnia and
  Herzegovina" contains the separator word.
- **WorkingNomads**: RSS at `https://www.workingnomads.com/jobs/feed/development` (returned 0 in one
  run; treat as best-effort, must not break the pipeline if empty).
- **ZipRecruiter**: blocked by Cloudflare 403 for scrapers; do not rely on it.
- **LinkedIn "Worldwide"** (via JobSpy): returns city-tagged jobs, not hire-from-anywhere. Do not
  treat a LinkedIn worldwide result as globally eligible without reading the posting.

---

