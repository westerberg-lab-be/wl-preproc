# The lab host protocol

**How wl.works talks to a lab host, and what a lab host answers.**

This document is written by **wl-preproc**, which implements the host half, and is
**proposed to wl.works** rather than written into that repository. Seven documents there
name `docs/ops/lab-host-protocol.md` — Plan 10 §2 makes it the host-side contract, §4.1
puts a specific sentence at the top of it, and `waiting-on.md` lists writing it as
explicitly not blocked — and neither repository had written it. This is that file.

Its two readers are the person installing the responder on the lab host, and whoever
maintains the client in wl.works. Neither should have to re-derive anything, so every
status code, header and number below is stated rather than implied, and each was taken
from the shipped implementation in `wl_preproc/responder/` rather than from a design
document. Where the two disagreed, the code won and the design document was corrected.

---

## The most prominent line

wl.works' Plan 10 §4.1 asks that this sentence sit at the top of this file rather than
buried in it, and it is right to:

> **Publishing an action makes it available to every member of the lab.** There is no
> permission model on the app side. If an action should not be triggerable by anyone who
> can log in, do not publish it.

And its counterpart, which is this host's own:

> **Every request to this host must carry a bearer token.** Every endpoint. An action the
> app declines to show is still an HTTP endpoint on the LAN, and the population of an
> unauthenticated LAN endpoint is not "any lab member" — it is anything plugged into the
> lab network.

---

## What changes on the wl.works side

Everything else here describes behaviour that already exists. These four items are the
ones a client written against Plan 10 as it stands today would get wrong.

1. **Send `Authorization: Bearer <token>` on every endpoint.** Plan 10 §6's request is an
   action name plus an idempotency key and carries no credential. Without the header,
   every request — including a health check — is answered `401`. See
   [Authentication](#authentication).
2. **Handle `409 Conflict` outside the retry loop.** It means an idempotency key was
   reused for materially different content, and no number of retries will clear it. See
   [409 is not retryable, and that is the point](#409-is-not-retryable-and-that-is-the-point).
3. **Handle `408 Request Timeout`, which is new on the wire.** It is retryable, unlike
   `409`. See [408 is retryable](#408-is-retryable).
4. **Fill in the three timing constants.** Plan 10 leaves poll cadence, request timeout
   and the per-host refresh cooldown as unfilled environment configuration. Values are
   proposed in [The three numbers](#the-three-numbers).

---

## Transport

| | |
|---|---|
| Scheme | `http://` — plain, on the lab LAN, per Plan 10 §5.4 |
| Address | the responder binds **every interface** on one TCP port |
| Direction | **wl.works opens every connection. This host never initiates one.** |
| Framing | HTTP/1.0 responses; **one request per TCP connection**, no keep-alive |
| Content type | `application/json` on every response that carries headers at all — three framing-error paths carry none, see the framing note under [Status codes](#status-codes) |
| Encoding | UTF-8; a request body that is not valid UTF-8 is `422` |
| `Server` | present, with a single-space value (`Server:  ` on the wire) — never a version |

**This host never initiates a connection**, and that is not merely an intention. Plan 10
§11 rules out push and heartbeats from the app's side; the parent wl-preproc design §3.1
says the same from this side (*"Never opens a connection outward"*). It is enforced by
`tests/test_cli_guardrails.py::test_nothing_in_wl_preproc_opens_an_outbound_connection`,
an AST walk over **every** `.py` file in the `wl_preproc` package — not only
`wl_preproc/responder/` — which fails the build if any module imports `requests`,
`httpx`, `aiohttp`, `urllib.request`, `http.client` or `socket`. The scope is the whole
package because the property is not a property of a directory: `GET /health` calls
`wl_preproc.cli.report.gather_readings` on the request path, so an outbound call added in
`cli/report.py` would run inside a request the responder is serving while sitting outside
a `responder/`-only scan. The inbound listening socket comes from `http.server`, which
binds without this repository's source naming `socket` at all.

**Results are pulled, never pushed.** Nothing in this protocol uploads an output, and
nothing calls back when a job finishes. wl.works discovers finished work the same way it
discovers everything else: by polling. See
[What this protocol does not carry](#what-this-protocol-does-not-carry).

**No keep-alive.** Responses are HTTP/1.0 and the server closes the connection after each
one, whatever the request's own version was — measured directly against the shipped
handler. A client must not hold a connection open between polls.

---

## Authentication

Both endpoints require:

```
Authorization: Bearer <token>
```

- The scheme token is matched **case-insensitively** (RFC 7235 §2.1), so `bearer`
  authenticates exactly as `Bearer` does.
- The token is compared with `hmac.compare_digest`, not `==`.
- The check runs **before routing and before any request body is read**. An
  unauthenticated caller learns nothing about which paths exist or which verbs they
  answer, and this host spends no effort parsing a body it was never entitled to send.

**Every authentication failure is the same `401` with the same body.** A missing header, a
header with the wrong scheme, and a header carrying the wrong token are indistinguishable
from the response:

```
HTTP/1.0 401 Unauthorized
Content-Type: application/json

{"error": "unauthorized"}
```

Never `403`: the absence of any credential and the presence of a wrong one are the same
failure from the caller's side, not two. There is deliberately no hint about which part
was wrong.

**Every verb other than `GET` and `POST` also answers `401`** — `PUT`, `DELETE`, `HEAD`,
`OPTIONS`, `PATCH`, `TRACE`, and any arbitrary method token (`CONNECT`, `PROPFIND`,
`BREW`, lowercase `get`). An *authenticated* `PUT`/`DELETE`/`HEAD`/`OPTIONS`/`PATCH`/`TRACE`
gets the honest routing answer instead (`404` or `405`); an authenticated arbitrary token
still gets `401`, because the code path that answers it cannot tell whether authentication
passed. `401` is the safe direction of that trade.

**The `Server` header discloses no version.** Do not expect it to be *absent*: measured on
the wire it is `Server:  ` — the header is sent on every response that carries headers at
all, and its value is a single space. `server_version` and `sys_version` are both emptied,
so the string the standard library joins them into has nothing in it but the separator, and
stdlib sends the header name regardless. What it never contains is this host's interpreter
version. No response body ever contains a Python traceback, a stack frame, or that version
either.

### Operating the token

| | |
|---|---|
| Source | the environment variable `WLPP_RESPONDER_TOKEN`, read at process start |
| Default | **none.** There is no built-in, no fallback, and no value in this repository |
| Unset or empty | `wlpp responder` prints an error and exits **2** without binding a socket |
| Non-ASCII | `wlpp responder` prints an error and exits **2** without binding a socket |

The non-ASCII refusal is not pedantry. `hmac.compare_digest` raises `TypeError` when
either `str` operand contains a non-ASCII character, and that exception escapes the
handler's own error handling entirely — the socket closes with no HTTP response at all. A
single en-dash pasted from a document into the token would therefore have made **every**
request fail that way, the correct token included, silently and permanently for as long as
the process ran. The responder refuses to start instead, loudly, at the source.

**Rotation is not solved here.** Where the token is stored, how it is rotated, and whether
wl.works can hold two during a rotation are open (responder design §12). Today: one token,
one host, changed by editing the environment file and restarting the unit, which drops
in-flight requests and makes the host briefly unreachable — which wl.works renders as
`unknown`, not `down`, and which is why the poll cadence proposed below tolerates several
consecutive misses.

---

## `GET /health`

The endpoint wl.works polls. It takes no body and no query parameters. It only ever reads
— proven by row snapshots rather than by DataJoint's `in_transaction`, which `insert()`
bypasses (`tests/responder/test_health.py::test_build_health_does_not_write`).

**Response `200`**, body validated against
[`docs/schemas/health_response.json`](../schemas/health_response.json):

```json
{
  "verdict": "ok",
  "readings": [
    {"key": "ingested_24h",      "label": "Ingested (24 h)",   "value": "3",                     "featured": true},
    {"key": "quarantined_7d",    "label": "Quarantined (7 d)", "value": "0",                     "featured": false},
    {"key": "stalled_transfers", "label": "Stalled transfers", "value": "0",                     "featured": false},
    {"key": "stuck_jobs",        "label": "Stuck jobs",        "value": "0 stale reservation(s)","featured": false},
    {"key": "disk_headroom",     "label": "Disk headroom",     "value": "4210 GiB free (ok)",    "featured": false}
  ],
  "actions": []
}
```

`verdict`, `readings` and `actions` are all **always present**. `actions` may be empty and
is empty today; a 10a client must accept and ignore the field, so that shipping 10b is not
a breaking protocol change to responders already deployed (Plan 10 §9).

**Readings carry no timestamp of their own.** The observation time is the time wl.works
polled — `lab_host_observation.observed_at`, which is already how Plan 10 §3.2 models it.

### The verdict is three values, not four

wl.works validates `verdict` against exactly `ok`, `degraded`, `down`, `unknown` and
rejects anything else. This host emits **three** of the four:

| Verdict | When this host emits it |
|---|---|
| `ok` | The database is reachable and nothing needs a human. |
| `degraded` | Reachable, but something needs a human: a storage-root scan fault, an unmeasurable disk, stuck jobs, quarantined sessions, stalled transfers, or scratch below the floor. |
| `down` | The database is unreachable. Nothing else could be computed. |
| `unknown` | **Never emitted by this host.** |

`unknown` is **wl.works' word for this host's silence**, recorded on their side when a
host goes quiet past its `lab_host.stale_after_seconds`. A host that is still running and
answering `/health` is by construction never in a position to assert it — doing so would
be claiming knowledge of its own absence. The value stays in the shared enum because their
verdict function produces it; it simply never arrives on the wire from here.

**A `down` verdict arrives as HTTP `200`, not as a `5xx`** — measured directly. That is
deliberate and it matters for Plan 10 §1.3's rule that `down` is reserved for a *positive
observation of failure*: a `200` carrying `"verdict": "down"` **is** the responder
answering and declaring itself faulty. A `500`, a connection refused, or a timed-out poll
is the opposite — no observation at all — and should be recorded as a transport failure
(`lab_host_observation.verdict` NULL, `error` set) and left to age into `unknown`. Reading
a transport failure as `down` would claim a certainty the wire does not support.

### The readings

Emitted in this order, always, except where noted:

| `key` | `label` | `value` |
|---|---|---|
| `ingested_24h` | `Ingested (24 h)` | count of sessions ingested in the last 24 h |
| `quarantined_7d` | `Quarantined (7 d)` | count of sessions quarantined in the last 7 days |
| `stalled_transfers` | `Stalled transfers` | count of sessions incomplete and quiet for ≥ 2 h |
| `walk_fault` | `Storage root scan` | **present only when the storage root could not be fully listed**: `root not fully scanned — <error>` |
| `stuck_jobs` | `Stuck jobs` | `<n> stale reservation(s)`, or `not checked (no schema activated in this process)` |
| `disk_headroom` | `Disk headroom` | `<n> GiB free (ok)` / `<n> GiB free (LOW)`, or `not measured — <error>` |

On the `down` path the array is replaced by a single reading —
`{"key": "database", "label": "Database", "value": "<ExceptionType>: <message>", "featured": true}`
— because nothing else could be computed, and `actions` is hardcoded empty there even if
stages exist: this host has just declared its database unreachable, so publishing a button
that ultimately inserts a request row would be an overclaim.

**Strings are plain text, never markup.** `<`, `>` and `&` are rejected outright in a
`label`, and in producer-supplied `value` text (an exception message, a filesystem path)
they are replaced with the literal substitutes `(lt)`, `(gt)` and `(amp)` before the
response is built. So a storage root genuinely named `A&B` renders as `A(amp)B` rather
than failing the whole response. wl.works escapes what it renders anyway; this host
refuses to emit markup in the first place rather than relying on that.

### `featured`: exactly one, always

Plan 10 §4 settles the ambiguity — *more than one `featured` reading: the first wins* — so
this host emits **exactly one**, on every path including `down`. Publishing two would let
their renderer choose for us, silently.

The featured reading is whichever condition drove the verdict away from `ok`, or the
ingest count when nothing did. When several conditions are bad at once, this priority
order decides, highest first:

1. `walk_fault` — the storage root could not be listed at all
2. `disk_headroom`, because the disk **could not be measured** (not because it read low)
3. `stuck_jobs`
4. `quarantined_7d`
5. `stalled_transfers`
6. `disk_headroom`, because the disk measured **below the floor**
7. `ingested_24h` — the `ok` fallback

The ordering is this host's own call; Plan 10 does not rank them and neither does the
responder design's §5.1. It separates **events** from the one **level**. A low disk can
stay true for weeks, long after the lab already knows about it, so ranking it with the
events would let a known chronic condition permanently occupy the one home-page slot and
keep a fresh acute fault off it. A disk that could not be measured at all ranks as an
event, and above the other events, because an unmeasured disk might already be below the
floor and this host cannot rule that out; a vanished storage root outranks even that,
because when the root is gone the listing and the disk measurement fail from the same
cause, and "Storage root scan" is the more honest description of what broke than "Disk
headroom: not measured".

### `actions`: derived, and empty today

`actions` is empty, and that is the deliverable rather than a placeholder for one. wl.works
renders every published action as a button any lab member can press, so a button that
queues work no stage will pick up for six months teaches people to distrust the surface.

An entry appears when **two hardcoded lists are extended in the same commit**: a computed
table is added to `wl_preproc/daemon.py::_computed_tables()`, and that table's name is
mapped to one of the five domains in `wl_preproc/responder/actions.py::_TABLE_DOMAINS`.
**This is a mapping, not a discovery** — nothing introspects a schema, a dependency graph,
or `populate()`'s key source, and a table present in `_computed_tables()` with no mapping
entry is silently excluded rather than raising, so a bookkeeping gap fails toward
"nothing published" instead of breaking `/health` for every caller.

The five domains and the exact `label` each will carry are already fixed:

| `name` | `label` |
|---|---|
| `behaviour` | `Per-task behavioural analysis` |
| `neural` | `Spike sorting` |
| `export` | `Trim-and-export an NWB to a block subset for DANDI deposit` |
| `atlas` | `@animal_warper atlas registration` |
| `calibration` | `Stimulus calibration from a .spectrashop measurement` |

Sorted alphabetically by `name`, so two polls against the same state render byte-identical
JSON. When a stage lands, its action appears here with **no change in wl.works** — which
is what "the host publishes its own action list" was written to buy.

---

## `POST /jobs`

Request body validated against
[`docs/schemas/job_request.json`](../schemas/job_request.json). Unknown top-level fields
are refused (`extra=forbid`), so adding a field is a protocol change on both sides rather
than something one side can do quietly.

```json
{
  "domain": "neural",
  "selection": {
    "session_datetime": "2027-06-01T09:00:00+00:00",
    "montage_id": 1,
    "probe_runs": {"19011110001": [1, 2]}
  },
  "parameters": {},
  "idempotency_key": "6f1c2f8e-…",
  "metadata": {
    "runs": [
      {"run_number": 1, "start_s": 0.002, "end_s": 1803.5, "works_run_id": "asr-311"},
      {"run_number": 2, "start_s": 1810.0, "end_s": 3604.25, "works_run_id": "asr-312"}
    ],
    "probes": [
      {
        "serial": "19011110001",
        "insertion_number": 1,
        "trajectory_id": "T-0042",
        "target": {"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
        "area_assignment": {"area": "V4d", "source": "at_rig", "asserted_at": "2027-06-01T10:15:00Z"}
      }
    ],
    "experimenter": "jwesterberg",
    "subject": "pico",
    "task_types": ["memguided"]
  }
}
```

- **`domain`** is one of the five `name` values from the published `actions` list. The app
  never sends a command string — that distinction from SSH is the whole security argument.
- **`selection`** requires `session_datetime` and `montage_id` (`group_id` instead of `montage_id` for a
  `shared` request, below). `session_datetime` arrives
  as an ISO-8601 string (JSON has no datetime type); a `Z` suffix, a numeric offset, or no
  offset at all are all accepted, and the value is normalised to naive UTC on this side.
  **A sub-second component is accepted and truncated to whole seconds** — a session is
  identified at second resolution here, and every column this host stores one in is a MySQL
  `DATETIME` with no fractional precision. So `…T09:00:00.123Z`, `…T09:00:00.000123Z` and
  `…T09:00:00Z` all name the same session, and the `activation` this host returns always
  names the whole second. **Send whatever your serialiser emits** —
  `Date.prototype.toISOString()` always writes milliseconds and a Postgres `timestamptz`
  keeps microseconds; neither needs trimming on your side. Truncated, not rounded:
  `…T09:00:00.600Z` names `09:00:00`, never `09:00:01` — which matters because MySQL, left
  to do this itself, rounds half-up and would have filed it under the next second.
  Measured over a real socket against a session landed at `2027-08-04 09:00:00`: ten
  spellings — four whole-second (`+00:00`, `Z`, no offset, `11:00:00+02:00`) and six
  fractional (`.000`, `.123`, `.000123`, `.600`, `.999999`, and `.123` at `+02:00`) — every
  one `200` with `"session_datetime": "2027-08-04T09:00:00"`.
  **`montage_id` names a montage this host listed** (design spec `2026-11-24-montages-design.md` §4.6): one
  of the `montages` in that session's `GET /sessions` entry, whose `positions_status` is `placed` or
  `not_needed`. The request never describes a montage; this host reads the one it stored and holds the
  request to it. A montage that is not listed is a `422`; see *What the montages add to the refusals*.
  **A request names runs** (design spec
  `2026-10-01-session-listing-and-run-requests-design.md` §3):
  - `run_numbers`, when present and non-empty, makes the request a **derivative**
    activation holding those whole runs, each measured, in the montage's window, and
    asserted in `metadata.runs` (now or by an earlier request). Its identity is its task
    type and its run set.
  - Absent or empty, the request is the **canonical** activation for `(session, montage)`,
    which holds **every measured run whose start lies in the montage's `[start_s, end_s)`
    window**. Each must be asserted in `metadata.runs`.
  - `probe_runs`, a canonical's only: for **every** probe in `metadata.probes`, keyed by
    serial, the runs its sort covers, **stated in full** (the record is the run set each
    probe resolved to, not the exclusions), and only the file's runs. An empty list leaves
    that probe out of sorting. This is how a run bad on one probe is left out of that
    probe's sort only. Each run is also among the runs the montage lists as `sortable_runs`
    for that probe (see the refusals below). A derivative sends none: each probe it names covers all
    of its runs, and those too must be among its sortable runs in the montage.
  - **`block_ids` is retired**, and so is `metadata.blocks`: a non-empty one is a `422`
    naming its replacement.

  **Since the canonical lifecycle** (design spec `2026-09-30-canonical-lifecycle-design.md`
  §3), two optional keys say more:
  - `"role": "canonical"` is the canonical above; a canonical cannot name a subset of its
    montage's runs (`run_numbers` with it is a `422`). `"role": "derivative"` means what
    `run_numbers` alone means, and needs them.
  - `"supersedes_activation_id": N`, with `"role": "canonical"`, is a **replacement**: a
    new canonical superseding `N`, which must be the montage's current canonical. The
    superseded file stays where it is, readable, and `GET /nwb` marks it. A replacement
    naming anything else is a `409`. A null `supersedes_activation_id`, like a null
    `role`, means the key is absent.
  - A canonical request with no `supersedes_activation_id`, for a montage that already has
    one, returns the montage's **current** canonical.

  **Since the montages piece (2026-11-24), a third role: `"role": "shared"`.** A shared group is a set
  of montages in which one probe held one electrode selection at one position
  (`shared_groups` in `GET /sessions`). Its electrodes are sorted once, over the runs of all those
  montages; this request says which runs.
  - **Its key is `selection.group_id`**, a `group_id` the session's `shared_groups` lists, in place of
    `montage_id`. Sending `montage_id` (non-null) or `run_numbers` with `"role": "shared"` is a `422`, and
    so is `supersedes_activation_id`: a group has one shared activation and it is never superseded.
  - **`selection.probe_runs` names its one probe**, `{"<the group's serial>": [runs]}`: at least one run,
    each among the group's `sortable_runs` and asserted in `metadata.runs` or by an earlier request. A
    missing or empty `probe_runs`, or an empty list for the probe, is a `422`: there is nothing to sort.
  - **`metadata.probes` is exactly that probe's entry**: one entry, with the group's `serial` and
    `insertion_number`.
  - **Idempotency and the answers are a canonical's.** The same key with the same content is a `200` with
    the same `activation`; another key for the same group returns the group's stored activation as it
    is (the first request's runs stand); a key reused for another request is a `409`.
  - **The `200` body is** `{"activation": {"subject": "…", "session_datetime": "2027-06-01T09:00:00",
    "group_id": 1, "activation_id": 0}, "accepted": true}`. There is one shared activation per group, so
    `activation_id` is `0`.
  - **It is stored to wait for sorting.** Nothing builds a file for it yet, and `GET /nwb` lists
    nothing for it. A canonical's NWB description says which shared groups its montage belongs to (see
    `GET /nwb`).
- **`metadata`** is the bundle this host needs from the ELN, and it is the reason this
  protocol works pull-only: everything wl-preproc needs arrives inbound with the request,
  because this host cannot call wl.works to ask. **`montage_boundaries` is retired**
  (the field `metadata.montage_boundaries`, since 2026-11-24): this host derives a session's montages from the
  positions sent to `PUT /positions` and lists them in `GET /sessions`, and a request names one in
  `selection.montage_id`. An empty list is accepted and says nothing, as an empty
  `metadata.blocks` is; a non-empty one is a `422` with the code `retired_field`, whatever it holds.
  Nothing is recorded from it.
- **`metadata.runs`** lists every run you hold for the session: `run_number`, `start_s` and
  `end_s` — your copy of what `GET /sessions` listed, measured from the recording — and
  `works_run_id`, your `animal_session_run` id. **Every run in `metadata.runs` is checked
  against this host's measured run as the request arrives**, whether or not the file holds
  it, start and end within 2 ms (`events/agreement.py::RUN_AGREEMENT_TOLERANCE_S`); a copy
  of the listing agrees exactly, and a stale copy of a run outside the file is refused too.
  One `works_run_id` names one run: the same id for two runs is a `422` within a request,
  and a `409` against an id already recorded for another run of the session.
  - **A stale listing is a `422`**: a run this host did not measure, its times off, or a
    measured run in the montage's window that the request does not assert. The message
    names the run and ends *rebuild the request from a fresh GET /sessions*. Stop and show
    it.
  - **A session whose runs are not measured yet is a `422` that clears itself**: the event
    stage has not read it, or the rig sent no run markers. Resend once `GET /sessions`
    lists its runs.
  - **A run already recorded under one `works_run_id` and named under another is a
    `409`.** The two records disagree about which run it is; the first is kept.
  - **A session with a repeated run number** (a crash restart before wl-xcon's XC-026) has
    only the first of each measured, so a montage cannot include the restarted runs.
  - An empty `metadata.blocks`, which every request sent while it was required, is
    accepted and says nothing.
- **`metadata.probes`** lists each insertion: its probe's `serial` and its
  `insertion_number`, with an optional `trajectory_id`, and, since the probes design
  (`2026-09-30-nwb-probes-design.md` §4), two more optional keys:
  - `target` is the insertion's aim, your `targetArea`, `atlas` and `atlasLevel`, as
    `{"area", "atlas", "atlas_level"}`. **All three or none**: a partial one is a `422`.
    `area` and `atlas` are at most 32 characters; `atlas_level` is 0–255.
  - `area_assignment` is the insertion's **latest** `insertion_area_assignment`, as
    `{"area", "source", "asserted_at"}`. `source` is one of `histology`,
    `functional_mapping`, `waveform_depth`, `structural_imaging`, `at_rig` and `other`.
    `asserted_at` is ISO-8601, like `session_datetime`, but is **kept to the
    microsecond**, since two assignments may fall in one second. There is no atlas key,
    because your assignment row lists no atlas column.
  - **The latest request wins** for an insertion, unlike a run's
    `works_run_id`, and as for `subject_details`: you are the authority on where a
    probe went. Assignments accumulate, as in your own table, and a request repeating one
    adds nothing. An insertion that a request does not mention is left as it was, so a
    request may name only its own montage's insertions.
  - This host joins each insertion to the probe the recording names, by `serial` (for
    SpikeGLX, the `.meta`'s `imDatPrb_sn`). **A probe that moved has one insertion per position**,
    each reported by its own request; a file takes the insertion its montage places the probe at, so
    the same serial carries a different `insertion_number` and area in different montages' files.
    The NWB file notes what it could not join (see *The notes a probe can carry* under `GET /nwb`)
    rather than guessing which segments each covers.
  - **With positions stored, each entry names one** (since 2026-11-24, plan decision D14): the same `serial`
    at the same `insertion_number` as a position sent to `PUT /positions`, or the request is a `422`. With none
    stored (a session with no ephys), entries are recorded as they come.
- **`parameters`** is recorded verbatim as part of the stored request and is read by
  nothing today; the stage that eventually runs the job is what will interpret it. It is
  not inert, though: the whole request payload participates in the key-reuse comparison, so
  **resending the same `idempotency_key` with different `parameters` is a `409`**, not a
  retry.
- **`metadata.experimenter`** becomes the request's `requested_by`. **It is at most 64
  characters, and a longer one is a `422`**; before 2026-11-24 it failed at the database
  (`request.Request.requested_by` is `varchar(64)`). The empty string is still accepted and
  gives a file with no experimenter. **`metadata.subject`**, not any `subject` inside
  `selection`, is what identifies the session.
- **`idempotency_key`** is 1 to 64 characters, and anything else is a `422` (the column is
  `varchar(64)`). A key of `wlw-act-` and a 36-character uuid is 44.
- **`metadata.subject_details`**, optional, is wl.works' own record of the animal:
  `species` (1 to 64 characters), `sex` (`M`, `F` or `U`, default `U`), `date_of_birth`, and
  `nickname` (**1 to 64 characters, since 2026-11-24**; a longer or empty one is a `422`).
  The latest request wins. A request without `nickname` leaves the stored one as it was.
  - **`nickname` is in the file's subject**, in `/general/subject/description`: "Nickname
    <name>, as wl.works' record gives it. As wl.works' job request stated it (design spec
    section 9)." A nickname that equals the subject's id (the stand-in the landing stub
    writes) is not stated, and the description's `subject.nickname` is then null.
  - **A changed nickname does not correct files already built**, unlike species, sex and date of
    birth: a file states the nickname stored when it was built. A file built after the change
    does carry it. To put a changed nickname in a file already built, send a replacement.
- **A run's mark, `metadata.runs[].marked_bad` and `bad_reason`, since 2026-11-24.**
  `marked_bad` is a boolean, default `false`. `bad_reason` is free text of at most 1000
  characters, default null. They go together: `marked_bad` true with no reason (empty or only
  white space), or a `bad_reason` with `marked_bad` not set, is a `422` naming the run. **Send null,
  not `""`, as the reason of a run that is not marked.**
  - The marks stay in the request as received; nothing else stores them.
  - **They are in the file**, from the file's own request: the runs table's `marked_bad` and
    `bad_reason` columns (`''` where not marked), and each run's `marked_bad` and `bad_reason` in
    `GET /nwb`'s description (false and null where not marked). A run the request's
    `metadata.runs` does not list reads as not marked.
  - **A later mark reaches a file only through a later request**, a replacement: nothing corrects
    a built file in place.
- **A run that ends before it starts cannot be requested.** `GET /sessions` leaves it out
  (see `ends_before_start`), so it is never in a copy of the listing. Asserting one in
  `metadata.runs`, or naming it in a derivative's `run_numbers`, is a `422` that names the
  run and both its times, for example *run 3 ends at 101.250 s, before its start at 101.300 s: left
  out of this listing; it cannot be requested*. **A canonical need not assert it**: the run is
  left out of the runs its montage's window requires. A window whose every run ends before it
  starts is a `422` naming them, never an empty file. The file leaves such a run, block or
  SpikeGLX file out and says so in its `notes`, with its times.

**Response `200`:**

```json
{"activation": {"subject": "pico", "session_datetime": "2027-06-01T09:00:00", "montage_id": 1, "activation_id": 0}, "accepted": true}
```

`activation` is the primary key of the `Activation` row this request resolved to.
`activation_id` is `0` for a montage's first canonical activation, and a positive integer
for a derivative or a replacement canonical.
`session_datetime` is rendered ISO-8601 and is naive UTC.

**`accepted: true` means recorded, not finished.** The responder does not compute. It
writes a Manual-tier request row and the daemon picks it up, exactly as the ingest watcher
does.

### What the montages add to the refusals

All of these are a `422` (nothing is written), and each message names what was wrong. A request is
checked against the stored derivation `GET /sessions` lists, never against a montage derived again, and the
refusals come in this order.
- **A montage or group that is not listed**: *no montage `N` is listed for `<subject>`/`<session_datetime>`:
  positions_status is `<status>`; send PUT /positions, or rebuild the request from a fresh GET /sessions*
  (*no shared group `N`* for a `shared` request). A montage or group is refused unless the session's
  `positions_status` is `placed` or `not_needed`, so a `waiting` or `ambiguous` session refuses every
  request, whatever montage id it names. Until a session's first accepted request its montage ids and
  group ids may change when its positions do; once a request has named one, they are never renumbered.
- **Positions changed since the montages were derived**: *session `<subject>` `<session_datetime>`: its
  positions changed (seq `N`) since its montages were derived `(seq M)`* (*with no positions*, when it
  was derived with none), *send the request again once GET /sessions lists seq `N`*. `PUT /positions` stores
  a seq at once, and the daemon derives and lists it on its next pass; **a request built from a listing
  older than the stored seq is refused, never applied.** Send it again once the session's
  `positions_seq` is the seq you sent.
- **A probe that names no stored position**: with positions stored, a `metadata.probes` entry that is not
  one of them is a *metadata.probes names probe `S` at insertion_number `N`, and no stored position is that
  probe at that insertion: the positions stored for `<subject>`/`<session_datetime>` are …; send PUT
  /positions, or rebuild the request*.
- **A run a probe cannot be sorted on**: *probe `S`'s run(s) `[…]` are not among its sortable runs `[…]` in
  montage `M`*, or, for a probe the montage does not list, *probe `S` holds no position in montage `M`, so it
  sorts no run there; the request gives it run(s) `[…]`*. A derivative's runs are each of its probes', so
  it is checked as a canonical stating them is.
- **A shared request that names no run**: *group `G`: a shared request names at least one of its sortable
  runs for probe `S` in selection.probe_runs*, for a missing `probe_runs`, an empty one, or an empty list
  for the probe. The other shared refusals say what differed: *metadata.probes names `N` probes, and a shared
  request names its group's one*; *metadata.probes names probe `S` at insertion_number `N`, and group `G` is
  probe `S2` at insertion_number `N2`*; *selection.probe_runs names `[…]`, and group `G` is probe `S`: a
  shared request states its one probe's runs*; and *selection.probe_runs[`'S'`] names run(s) `[…]` not among
  group `G`'s sortable runs `[…]`* (or *asserted neither in metadata.runs nor before*).
- **A request that races a re-derivation**: *the montages of `<subject>`/`<session_datetime>` were derived again
  while this request was checked: positions_status is `<status>`; rebuild the request from a fresh GET
  /sessions*. A new set of positions reached the daemon between this host's checks and its write. Nothing was
  accepted, and it is safe to resend. (A request refused here may have recorded the subject's details and
  the insertion reports it carried, as the latest request wins for both and a resend replaces them.)

### Idempotency, and what the key is for

The key distinguishes **a retry of a request this host already accepted** from **a
genuinely new request**. It is not what prevents a second run; single-flight is structural
on this side (a request whose `(selection, task type)` is already in flight returns the
running one).

Plan 10 §6.1 specifies that the key is *"generated once when the confirmation dialog is
accepted and reused across retries of that same intent, never regenerated per click."*
This host is built around that exactly:

- The **same key with the same content** returns `200` with the same `activation` — every
  time, however many retries. A genuine retry is a `200`, not a `409`.
- The **same key with materially different content** — a different domain, origin,
  requester, payload, or selection — is `409`. See below.
- **A different key naming the same selection** deduplicates: it returns the existing
  activation rather than starting a second run.

An aborted request is safe to repeat. If wl.works' client times out and retries with the
same key, and this host had in fact already accepted the first attempt, the retry returns
that same activation.

---

## `GET /nwb`

*Added 2026-09-29 with NWB publishing (`docs/superpowers/specs/2026-09-29-nwb-publishing-design.md`
section 6).* Lists every NWB file whose record changed after a cursor, so wl.works can learn
where each file is and what it holds without opening it.

```
GET /nwb?since=<cursor>&limit=<n>
Authorization: Bearer <token>
```

- **`since`** is optional, one non-negative integer. Without it, every file is listed.
- **`limit`** is optional, one positive integer, with no upper bound (since 2026-11-24). Without it, every
  file after `since` is listed, as before. With it, the page is the first `limit` files, and
  **`cursor` is the latest `change_seq` on the page**, or `since` (0 without it) when the page is empty. A page
  with fewer than `limit` files means you are caught up. Any other key, a repeat of either, or a value that is
  not a number of that kind is a `422`.
- **The order is by each file's latest change (since 2026-11-24).** It was by activation key. An unpaged call
  returns the same files and the same `cursor` as before, in a new order, so a reader that took the order from
  the key must sort. Only the files on the page are read and described.
- **The response** is [`docs/schemas/nwb_listing.json`](../schemas/nwb_listing.json):
  `{"cursor": <int>, "files": [...]}`. Send `cursor` as the next `since`; it only increases.
- **Each file:**
  - `activation`: the key;
  - `identifier` and `status` (`written`, `invalid` or `refused`), and `reason`;
  - `placement`: `tier` (`fast` or `slow`), `host`, `share`, `path` relative to the share,
    and `n_bytes`, or `null` until published;
  - `description`: [`docs/schemas/nwb_description.json`](../schemas/nwb_description.json),
    or `null` for a refused activation. **Version 3** (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` §4) replaces `blocks` with
    `runs`, since version 2 allowed only additions and this renames and removes. Each run
    of the file: `run_number`, `works_run_id` (join it to `animal_session_run`), `task`
    (`code` and `name`), its `measured` interval, `closed`, `trials` (total and by
    outcome), each system's `coverage`, its `conditions`, and its measured `blocks`
    (`block_number`, `block_in_run`, `block_type`, `measured`, `closed`, `trials`). Each
    probe also carries `sorted_runs`, its list from the request (a derivative's: all of its
    runs). **Version 2** (the probes
    design, `2026-09-30-nwb-probes-design.md` §3.2) gave `probes` its shape: each probe's
    `serial`, `probe_type`, `insertion_number`, `trajectory_id`, `n_electrodes`, `target`
    and `assignment`, and `area_from`, which says whether the file's area label came from
    the assignment, the aim, or neither. **Within version 3, since 2026-10-14** (design spec
    `2026-10-14-session-kind-and-warnings-design.md` §5): what wl-xcon's record says the session
    was, `session_kind`, `calibration` (`id`, `standard`, `measured_on`) and
    `accepted_warnings` (each `code` and `run_number`, null at the open), and each run's
    `calibration`; null and empty for a session wl-xcon did not run. **Within version 3, since
    2026-10-18** (design spec `2026-10-18-training-records-and-backup-clock-design.md` §3.2):
    `training_records`, how many of wl-xcon's training records the file holds, its
    `parameter_changes` and its `controls` (the rows themselves are in the file's `rig_record`
    module, which names no one who acted); null for a session wl-xcon did not run, and in a file
    built before. **Within version 3, since part 3b-1** (the same design spec, amendments 46 and
    50): `quality.clock`, which clock kept the file's time (`syncbox` or `nidq`, defaulting to
    `syncbox`, so a description written before reads unchanged), and two more values of
    `quality.reference_source`, where the wall-clock time of t = 0 came from under the NI:
    `rig_clock` (the rig's clock fitted to the recording) and `nidq_meta` (the first NI file's
    `fileCreateTime`), beside `barcode` and `manifest`. **Within version 3, since 2026-11-24**
    (design spec `2026-11-24-wl-works-requests-design.md` §4.1 and §4.2): each run's `marked_bad` (false
    unless the file's request marked the run bad for behaviour) and `bad_reason` (null unless marked), and
    the `subject`'s `nickname` (null where wl.works sent none), each defaulted so a description written before
    reads unchanged. The exported schema refuses fields and
    values it does not know, so a copy validated against must take these. Read `schema_version`,
    and ignore fields you do not know.

A file changes when it is built, published, moved between shares, or **its subject's details are
corrected** (design spec `2026-10-05-subject-corrections-design.md`): a request that carries an
animal's corrected species, sex or date of birth has every file already built for that animal
rewritten in place within the daemon's next pass, its annotations kept. Its `description` then
carries the new `subject`, and its `notes` a line such as *"Corrected 2026-11-02: date of birth
2016-03-02 → 2016-03-01."* A request that leaves the date of birth out does not clear it: the one
on record stays, and the request's other details are still corrected. A correction changes the
checksums of the subject's four datasets (`/general/subject/species`, `sex`, `date_of_birth` and
`description`), which `description.checksums` lists with the file's written-once data; every other
checksum stays as built.

### The notes a probe can carry, and the shared-groups sentence (since 2026-11-24)

A file's `description.probes` and its `notes` follow its montage (`2026-11-24-montages-design.md` §4.3,
§4.7). Each probe takes the insertion, and the site map, its montage lists for it: the montage's
`electrode_config_hash` and `sortable_runs`, read from the stored rows and never derived again. A probe
carries one of these notes where the file cannot place it or join it:
- *probe `S` records two site maps under this file's runs, the change inside run `R`: the file gives it the
  site map its sortable runs in montage `M` share, and run `R` is not sorted for it.* A bank change inside a
  run is built, not refused: the montage cuts at every change between runs, and a run the change falls
  inside is left out of the probe's sortable runs. When the probe has no sortable run in the montage, the
  note ends *; it has no sortable run in montage `M`, so it has no electrodes in this file* instead.
- *probe `S` has no sortable run in montage `M`, so it has no electrodes in this file.* The probe is recorded
  but none of its runs in the montage can be sorted (an edge of its position fell inside its one run, say).
- *probe `S` is named by no position from wl.works, so its insertion and area are unknown*, and *probe `S` holds
  no position over montage `M`'s runs, so its insertion and area are unknown*.
- *probe `S` (insertion `N`) has no report from wl.works, so its area is unknown.*
- *insertion `N` names probe `S`, which no recording in this session names, so the report is not joined*; for
  an Intan (RHS) session, which names no probe, *probe `S` (insertion `N`) is listed from wl.works' report
  alone: no SpikeGLX recording names it, and an Intan (RHS) header does not name its probe*.

A **canonical's** session description (the file's `description` text) also names the shared groups its montage belongs to: *Electrodes it
shares with other montages are sorted in shared groups: group `G` (probe `S`, montages `A, B`, `K` electrodes)*,
with *every channel* where the group's electrodes are not listed. The file itself holds only its own montage's
runs; a shared group's sort, and its file, wait for sorting (see the `shared` role under `POST /jobs`).

## `PUT /nwb/active`

*Added 2026-09-29, the same spec, section 5.* The **whole** set of activations wl.works wants
on the fast share, every time. This host records it and moves files in the daemon's next
pass; `GET /nwb` then shows each file's new `placement`. Sending the same set twice changes
nothing.

```json
{"activations": [{"subject": "…", "session_datetime": "2027-01-12T09:00:00",
                  "montage_id": 0, "activation_id": 0}],
 "requested_by": "<wl.works user, or null>"}
```

The body is [`docs/schemas/active_set_request.json`](../schemas/active_set_request.json),
read under the same `Content-Length` rules as `POST /jobs`. The answer is `202` with
`{"accepted": <n>, "unknown": [<keys this host has no activation for yet>]}`. Unknown keys
are kept: a dataset may be marked active before its files exist, and a file in the set
publishes straight to the fast share.

## `PUT /positions`

*Added 2026-11-24, `2026-11-24-montages-design.md` section 4.1.* The **whole** set of one session's
probe positions, every time: where each probe was inserted and when, as wl.works records it. This host
stores the set as sent and derives nothing in the request; **the montages follow on the daemon's next
pass** (D17), and `GET /sessions` lists them, with the `positions_seq` they were made from. The answer says
what this host holds, not what is listed.

```json
{"subject": "…", "session_datetime": "2027-06-01T09:00:00Z", "positions_seq": 4,
 "positions": [{"serial": "19011110001", "insertion_number": 1,
                "inserted_at": "2027-06-01T09:00:00Z", "ended_at": null, "end_kind": null,
                "inserted_after_run": null, "ended_after_run": null}]}
```

The body is [`docs/schemas/positions_request.json`](../schemas/positions_request.json), read under the same
`Content-Length` rules as `POST /jobs`. Request fields: `subject`, `session_datetime`, `positions_seq` and
`positions`.
- **`positions_seq`** (0 to 4294967295) is wl.works' own counter for the set: it orders the sets it sends.
  The highest wins (see *The seq*, below). `subject` is 1 to 64 characters. `session_datetime` and each
  time below are ISO-8601, with or without an offset; one without is taken as UTC, and every time is stored
  as naive UTC to the microsecond.
- **Each position** carries `serial` (1 to 32 characters), `insertion_number`, `inserted_at`, `ended_at`,
  `end_kind`, `inserted_after_run` and `ended_after_run`. `insertion_number` is wl.works' identity for the
  position, 1 to 255, never renumbered; gaps are fine. A probe that moves is two positions of one serial,
  each with its own number. No author is carried. `ended_at` and `end_kind` (`withdrawn` or `moved`) go
  together; null for a probe still in at the end of the session. A `moved` end cuts the session into a montage
  on each side of the move, and so does the start of a serial's second or later position; a `withdrawn`
  end and a serial's first insertion cut nothing by themselves. A change of a probe's electrode selection
  between runs cuts it too, with no position involved.
- **The answer is `200`** `{"positions_seq": <the seq this host now holds>, "changed": <bool>}`. `changed`
  is false when the set was already held.

**How a time is placed among the runs.** A time is placed once the session's t = 0 wall time is stored
(`t0_datetime`), its probe census is read and it has measured runs; until then `positions_status` is `waiting`.
- **Precision (D5).** A time that is a whole minute (seconds and microseconds zero) stands for that
  minute: the window `[time, time + 60 s)`. A time with seconds stands for the wall-clock second it falls in,
  `[second, second + 1 s)`, a sub-second part truncated (not rounded) and t = 0 not floored. The window is
  half-open, so one that ends exactly where a run starts does not touch it.
- **The widening.** Where t = 0 came from the manifest (`t0_source` `manifest`, accurate to the minute), the
  window widens by 60 s each side; from `barcode`, `rig_clock` or `nidq_meta` it does not.
- **The gap.** The window touches the gaps between runs (before run 1, between runs, after the last).
  One touched gap is the placement. A window wholly inside one run (and no gap named) is placed after that run,
  and the run is left out of that probe's sortable runs (flag `position_edge_inside_run`). More than one gap is
  ambiguous.
- **`inserted_after_run` and `ended_after_run`** say which gap, when the time alone does not: **the gap after
  that run number, and `0` for the gap before run 1** (the gap before the first run has no run to follow, so 0
  names it). The name is trusted only if the time touches that gap; a name the time does not touch, or a run
  that is not a measured run of the session, is flagged `position_edge_ambiguous` and no montage is listed
  until the name or the time is corrected. A named gap is how a person chooses among the gaps an ambiguous time
  touches.
- A session with an edge that cannot be placed lists `positions_status` `ambiguous`, no montages, and the
  flags that say which edge and why.

**The seq.**
- **Until the session's first accepted job request**, the highest `positions_seq` wins. A higher seq replaces
  the whole set. The same seq with the same set is a `200` with `changed` false; the same seq with another
  set, or a lower seq, is a `409`.
- **After it, the set is fixed** (the request's activation covers montages derived from it). A changed set is a
  `409`. A **higher seq with the same set** records the seq alone (`changed` false), and the listed
  `positions_seq` follows it on the same call, so wl.works' gate that compares its own seq with the listed one
  never sticks. An empty set sent for a session whose request was accepted with no positions on file
  records its seq the same way. **A non-empty set for a session that was accepted with no positions** is a
  `409` carrying `positions_seq` `0`: nothing is stored, so there is no seq to carry.

**Refusals.** `422` for a body that fails the schema, each message naming the field and the position's
`insertion_number` (wl.works shows them): an `ended_at` without an `end_kind`, an `end_kind` or `ended_after_run`
without an `ended_at`, an `ended_at` before `inserted_at` (*ended_at is before inserted_at for insertion_number
`N`*), an `insertion_number` given twice (*insertion_number `N` is given twice in positions*), and two positions
of one serial that overlap (*positions of serial `S` overlap: insertion_number `A` and insertion_number
`B`*; an end equal to the next start touches and is not an overlap, and an open end runs on). `404` for a session
this host has not landed, `{"error": "no session of subject '<subject>' at <session_datetime> has landed
here"}`. `409`, `{"error": "<why>", "positions_seq": <the seq this host holds>}`:
- *positions_seq `N` is below the `M` this host holds*;
- *positions_seq `N` is the one this host holds, with another set of positions*;
- *the positions are fixed by the request `'<idempotency key>'`; positions_seq `N` changes them*, and *… adds them*
  (with `positions_seq` 0), after the session's first accepted request.

The `404` and `422` carry no `positions_seq`. A `408` means the body never arrived and nothing was stored.

## `GET /sessions`

*Added 2026-10-01 with the landed-session listing
(`docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md` section 2).*
Lists every landed session whose entry changed after a cursor: its runs, the blocks inside them,
the SpikeGLX segments and probes it recorded, and its flags. It is how wl.works learns a
session's measured runs, to make the session's runs from them and to fire its canonical NWB.

```
GET /sessions?since=<cursor>&limit=<n>
Authorization: Bearer <token>
```

- **`since`** is optional, one non-negative integer, as for `GET /nwb`. Without it, every
  listed session is listed.
- **`limit`** is optional, one positive integer, with no upper bound (since 2026-11-24), as for
  `GET /nwb`. The sessions changed after `since` are ordered by their latest change, as before, and the
  page is the first `limit` of them; **`cursor` is the latest `change_seq` on the page**, or `since` (0
  without it) when the page is empty. A page with fewer than `limit` sessions means you are caught up.
  Only the sessions on the page are built. Any other key, a repeat, or a value that is not a number of that
  kind is a `422`.
- **The response** is [`docs/schemas/session_listing.json`](../schemas/session_listing.json):
  `{"cursor": <int>, "sessions": [...]}`. Send `cursor` as the next `since`; it only increases.
- **A session is listed** once the daemon has read its event codes, and again whenever its entry
  changes: a timing tier computed later, a probe census, a rejected segment. **Each entry is the
  session as it stands now.**
- **Each session:**
  - `subject`, `session_datetime` (naive, UTC), and `session_name`, the rig's `YYYY-MM-DD_NN`;
  - `tier`, the timing tier (`A` to `D`, or `N` where the NI kept the session's time, its codes hold
    every run and nothing failed; `D` quarantined), or `null` until computed (under the NI, Intan is placed
    by its strobes, so a session with Intan can be `N`; where a sync box recorded part of the session, the
    NI's own barcodes place the box, the eye tracker and the cameras over that part, and the box's codes there
    are checked against the NI's, so such a session can be `N` too; under the NI the eye tracker may also be
    placed by its analog copy on one of the NI's analog inputs, over its whole recording, so a session whose
    eye file holds no barcode can be `N`, while one whose copy's lag, measured against the eye file's
    barcodes, is out of bounds or disagrees with them is `D`; a session with the cameras under the NI where
    the NI recorded no barcode, or with a camera file that decoded none, or with an eye file that neither its
    barcodes nor its copy placed, is still `D`);
    and
    `rejected_segments`, each file this host could not use, with its reason (under the NI,
    `outside_covered_span` is a file none of whose barcodes the NI recorded, so it lies outside the part the
    sync box covered: it is left out and named, and does not make the session `D`; `no_analog_match` is an
    eye-tracker file whose analog copy on the NI was found but refused and which no barcode placed, or an eye
    file that is not the one its copy placed (another, or one changed since): it makes the session `D`);
  - `runs`, measured from the recording's `RUN_START` (`0x8006`) and `RUN_END` (4): `run_number`
    (wl-xcon's `run_in_session`), `task_code`, `task` (wl-xcon's name for it), `task_hash` (the content hash of the task file it ran, `sha256:` and
    its digest, from its start row, or null where the rig named none or named a path), `start_s` and
    `end_s` on the recording's clock, `closed`, wl-xcon's `stopped_because` and `stop_kind`,
    `calibration` (the display calibration the run ran against, from its start row in wl-xcon's
    record, or null), `segments` (the `segment_id`s of the SpikeGLX segments it spans), and `blocks`;
  - each block: `block_number` (in the session), `block_in_run`, `block_type`, `start_s`,
    `end_s`, `closed` (null when never recorded, for a session read before 2026-10-01),
    `n_trials`;
  - `segments`, each SpikeGLX file once: `segment_id` (the file's path within `spikeglx/`), `start_s`, `end_s`, and per probe its
    `serial`, `part_number`, `probe_type`, site map (`electrode_config_hash`, `n_electrodes`,
    `electrodes` as SpikeGLX electrode numbers), its `~imroTbl` verbatim as `imro_table`, and
    any `problem`; `probes` is null until the probe census has read the file, and `[]` when it
    recorded none. **Runs and segments do not align**: a bank change needs a SpikeGLX restart,
    which is a new segment, and a run need not stop for it;
  - `probes`, every serial the recording names;
  - what wl-xcon's record says the session was (design spec
    `2026-10-14-session-kind-and-warnings-design.md` §4), carried and never acted on here:
    `session_kind` (`training`, `piloting` or `recording`), `calibration` (`id`, `standard`: true for
    the sRGB standard nobody measured, and `measured_on`, empty for the standard), and
    `accepted_warnings`, each with its `code`, its sentence as `detail`, the kinds it is acceptable
    in as `accepted_in`, `how` it was accepted (`open`, `start` or `--accept-warnings`), the
    `run_number` it was accepted for (null at the open, for a run the recording did not
    measure, for one with no start row in wl-xcon's record, or for one whose number could not
    be stored, only the last flagged as a `rig_record_problem`), `at_local` (the rig's local time, not the recording's clock) and `by`: `kind` (`box`
    for a name typed at the rig, `member` for a wl.works member, `system` for the rig itself), `name` and `account` (null for a
    box), never the token or its issuer. A session wl-xcon did not run, one whose record predates
    its engine build B, and one read before this host carried them (2026-10-14) list null, null and
    `[]` alike;
  - `training_records`, how many of wl-xcon's training records the session has (design spec
    `2026-10-18-training-records-and-backup-clock-design.md` §3.4): its `parameter_changes`, and of
    its console controls its `pauses` (a person's) and `manual_rewards`, and `system_pauses`, the
    rig's own, sent when its display process died, counted apart; the rows are in the database and the
    NWB file, never listed. Null for a session wl-xcon did not run; a session read before this host
    carried them (2026-10-18) lists zeros;
  - `clock`, which clock kept the session's time: `syncbox`, `nidq` (built since part
    3b-1) or `lab` (part 4, not built yet), or null until the daemon has chosen it;
  - `t0_datetime`, the wall-clock time of session t = 0, ISO-8601, naive UTC like `session_datetime`
    but kept to the microsecond, and `t0_source`, where it came from: `barcode` (the sync box's first
    barcode), `rig_clock` (the rig's clock fitted to the recording) or `nidq_meta` (the first NI file's
    creation time), each only within 60 s of the manifest's `started_at`, else `manifest` (`started_at`
    itself, which `session_datetime` is). Worked out once by the daemon, after the session's event codes
    are read, and the value every NWB file of the session built since it was stored starts at
    (`timebase/clock_reference`'s `reference_datetime`); a file built before a session was re-timed keeps
    the value it was built with (see below). Both are null until it is stored. It is what places a
    time of day between runs: `session_datetime` may be up to 60 s from it, and a run's `start_s` counts
    from it;
  - **The montages** (since 2026-11-24, `2026-11-24-montages-design.md` §4.5): what this host derived from
    the positions sent to `PUT /positions` and the recording, stored by the daemon and listed as stored:
    - `positions_status`: `not_needed` (no ephys was recorded: one montage over every run, no probes, and no
      positions to wait for), `waiting` (positions not sent yet, or `t0_datetime`, the probe census or the
      runs not stored yet), `placed` (every position's edges are in a gap between runs) or `ambiguous` (an edge
      could not be placed, or two placements contradict; the flags say which, and no montage is listed). It is
      `waiting` until the daemon has passed the session.
    - `positions_seq`: the seq of the positions the stored derivation was made from, null until the daemon has
      passed the session (and while no positions were sent). **Compare it with the seq you sent**: a request
      built before it lists your seq is refused. After the session's first accepted request it follows a
      seq-only `PUT /positions` at once.
    - `montages`, null unless `positions_status` is `placed` or `not_needed`: each a `montage_id` (this host's,
      from 1), `start_s` and `end_s` (the window `[start_s, end_s)` in session seconds; the last ends at its
      last run's end, or 1 ms after a zero-length last run's start), `run_numbers` (the runs whose start lies in
      the window), and its `probes`, each a `serial`, `insertion_number` (wl.works', from the position), the
      site map it records there as `electrode_config_hash` (null for a probe the census does not name, such as an
      Intan probe) and `sortable_runs` (the montage's runs its position covers, less a run an edge of that
      position falls inside and a run in which its selection changes). **A montage splits where a probe moves
      or its selection changes.**
    - `shared_groups`, null as `montages` is, `[]` when nothing is shared: each a `group_id` (this host's, from
      1), `serial`, `insertion_number`, `montage_ids` (the montages the group spans), `n_electrodes`,
      `electrodes` (SpikeGLX electrode numbers, the ones selected in every one of its montages; both null for a
      probe with no channel map read, whose group shares every channel) and `sortable_runs` (the runs of
      all those montages the probe can be sorted on). Groups may overlap: for a probe on map A, then B, then A
      at one position, A-and-B is shared over montages 1 to 3 and all of A over montages 1 and 3. A
      `shared` request names one (see `POST /jobs`).
  - `flags`, each a `code`, a `message`, and the `run_number`, `block_number` or `segment_id` (a
    SpikeGLX file) it is about:
    `waiting_for_run_markers` (no measured run yet), `repeated_run_number` and
    `repeated_block_number` (a crash restart before wl-xcon's XC-026; only the first is
    listed), `run_without_block`, `bank_change_in_run`, `block_type_unknown`, `task_unknown`,
    `block_outside_runs` (a block in no measured run: its run's `RUN_START` was lost), and
    `rig_record_problem` (what wl-xcon's record could not supply, each naming its file, line by
    line: `runs.jsonl`, `config.json`, `warnings.jsonl`, `parameter_changes.jsonl` or
    `controls.jsonl`, the last two also when a count of codes disagrees or rows cannot be timed; or
    the run record's absence; a problem stored before 2026-10-14 names no file), and
    `ends_before_start`: a run, block or file whose end comes before its start, **left out of the
    entry** and named here with both its times (a block with its run's number, when it lies in one).
    It is the one flag that means something was withheld. Equal start and end is listed.
    The montages add three, each from a session's stored derivation and with a message that names the probe and
    its insertion: `position_edge_ambiguous` (an edge that touches more than one gap, names a gap its time does
    not touch, names a run that is not measured, or ends before it starts once placed; `positions_status`
    is `ambiguous` and no montage is listed until it is corrected), `position_edge_inside_run` (an edge falls
    inside a run: it is placed after that run, and the run is left out of that probe's sortable runs;
    `run_number` says which) and `probe_without_position` (a probe the recording names that no position
    names: its runs are sortable in no montage, and its area is unknown). A session in `ambiguous` lists all
    three kinds it has at once, so everything to correct is in one listing. **None of them blocks anything
    here**; a montage that is not listed is what stops a request, with its own `422`.

### `t0_datetime` is fixed when it is stored: re-timing a session

A session's t = 0 wall time is fixed when a daemon pass stores its `timebase.ClockReference` row,
with the NI machine's time zone that pass had (`wlpp daemon --nidq-timezone <IANA name>`, such as
`America/New_York`; unset, an NI file's creation time places nothing). That row is what
`GET /sessions` lists as `t0_datetime` and `t0_source`, and what every NWB file of the session
starts at. Starting the daemon later with another zone, or with one where it had none, changes no
session already stored. `wlpp nwb build` takes no zone at all: it reads the stored row.

**To re-time a session**, delete its row, then run a daemon pass with the zone set:

    python -c "from wl_preproc.daemon import activate_all; activate_all(prefix='wlpp_'); \
      from wl_preproc.schema import timebase; \
      (timebase.ClockReference & {'subject': '<subject>', 'session_datetime': '<session_datetime>'}).delete_quick()"
    wlpp daemon --nidq-timezone America/New_York …

A session whose scratch copy was freed is skipped by the daemon, so rehydrate it first with
`wlpp rehydrate --session <session folder> --nas-root <mount>`, then delete the row and run the pass.

Until that pass, the session lists `t0_datetime` and `t0_source` as null and its NWB files wait.
Once it has run, the session's entry changes, so `GET /sessions` lists it again. **Files already
built keep the value they were built with** (their `quality.reference_source` and
`timebase/clock_reference` say which); only a file built after the pass carries the new one.

---

## Status codes

Every code this host can return, on any endpoint.

| Code | Endpoint | Body | Meaning | Retryable? |
|---|---|---|---|---|
| `200` | all but `PUT /nwb/active` | the response above | Accepted, health served, or files listed. A `down` verdict is a `200`. | — |
| `202` | `PUT /nwb/active` | `{"accepted": …, "unknown": […]}` | The active set is recorded; the daemon's next pass moves files. | — |
| `400` | all | `{"error": "bad request"}` | Malformed request line, or an unparseable version such as `HTTP/9.9.9`. See the framing note below. | No — fix the client |
| `401` | all | `{"error": "unauthorized"}` | Missing, wrong-scheme, or wrong token; or a verb no endpoint answers. | No — fix the credential |
| `404` | all | `{"error": "not found"}` | Path is not one of `/health`, `/jobs`, `/nwb`, `/nwb/active`, `/positions`, `/sessions`; or a query string on any path but `/nwb` and `/sessions`. | No |
| `404` | `PUT /positions` | `{"error": "no session of subject '<subject>' at <session_datetime> has landed here"}` | The session has not been ingested here: its own message, not the `not found` above. | Not until the transfer lands; then resend |
| `405` | all | `{"error": "method not allowed"}` | Known path, wrong verb — `GET /jobs`, `POST /health`, `GET /nwb/active`, authenticated `PUT /health`. | No |
| `408` | `POST /jobs`, `PUT /nwb/active`, `PUT /positions` | `{"error": "request timed out"}` | The declared body never fully arrived. | **Yes** |
| `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content (a `shared` request's too); a replacement naming a canonical that is not the montage's current one; or a run named under a `works_run_id` other than the one recorded, or a `works_run_id` recorded for another run. | **No — needs a human** |
| `409` | `PUT /positions` | `{"error": "<why>", "positions_seq": <the seq this host holds>}` | A `positions_seq` below the stored one; the stored seq with another set; or, after the session's first accepted request, a changed set (the message names that request's idempotency key). | No — send a higher seq, or the stored set |
| `414` | all | `{"error": "request line too long"}` | Over-long request line. | No |
| `422` | `POST /jobs`, `PUT /nwb/active`, `PUT /positions`, `GET /nwb` and `GET /sessions` (a `since` that is not one non-negative integer, a `limit` that is not one positive integer, any other key, or a key sent twice) | `{"error": "…"}` or `{"error": "invalid request body", "detail": […]}` | The request is malformed, or asks for something this host cannot do — **including, for `POST /jobs`, naming a session it has not ingested yet** (`PUT /positions` answers that with a `404`), a montage or group it has not listed, or positions that changed since its montages were derived. | No — fix and resend; for a not-yet-ingested session, or a montage or seq not listed yet, resend once the transfer lands or `GET /sessions` lists it |
| `431` | all | `{"error": "request header fields too large"}` | Oversized header. | No |
| `500` | all | `{"error": "<ExceptionType>: <message>"}` | This host's own fault, infrastructure included. | **Yes** |
| `505` | all | `{"error": "http version not supported"}` | `HTTP/2.0` or later. See the framing note below. | No |

**The framing note**, stated rather than left to be discovered. On the paths where the
request line itself could not be parsed — a malformed request line, and any unparseable or
unsupported version string — the response carries **no status line and no headers**, only
the JSON body. The request version is still the `HTTP/0.9` placeholder at that point, and
the standard library's status-line and header machinery are both no-ops for HTTP/0.9. So
`400` and `505` are the codes this host *means* on those paths, and neither reaches the
wire as a number: a client sees a bare JSON body and should read that as a client-side
framing error rather than as a transport failure. `414` and `431` are not affected — those
arrive as ordinary responses with a status line, as does every other code above.

### `422` is the caller's mistake

`422` covers everything wrong with what was sent, and it never carries a traceback:

- **Bad JSON**, or a body that is not valid UTF-8 → `{"error": "<parser message>"}`.
- **JSON that fails the `JobRequest` schema** → `{"error": "invalid request body",
  "detail": [ … ]}`, where `detail` is pydantic's own error list verbatim (with its
  documentation links stripped), including `extra_forbidden` for an unknown field.
- **A well-formed request this host refuses**: a `selection` missing `session_datetime` or
  `montage_id`; a `session_datetime` that is not parseable ISO-8601 or is before year 1000;
  an oversized `subject`; **a session this host has not ingested yet** (see below), or
  whose runs it has not measured yet; a `montage_id` that will not fit its column; a
  `montage_id` or `group_id` this host has not listed; a non-empty `metadata.montage_boundaries` (retired);
  positions that changed since the montages were derived; **a
  run that does not match the measured one** — not measured, its times more than 2 ms
  off, a measured run in the window left unasserted, or a derivative's run outside the
  window — whose message ends *rebuild the request from a fresh GET /sessions*; a
  `probe_runs` that does not state every probe's runs, or names a run the file does not
  hold; `run_numbers` on a canonical, or `probe_runs` on a derivative; the retired
  `block_ids` or a non-empty `metadata.blocks`; a `role` other than `canonical`,
  `derivative`, or `shared`; `supersedes_activation_id` without `"role": "canonical"`, or not a
  non-negative integer; `"role": "derivative"` without `run_numbers`; or any refusal under *What
  the montages add to the refusals*. The message names what was wrong.
- **A session not ingested yet is a `422` for `POST /jobs`, and a `404` for `PUT /positions`**: a positions set
  carries no job to refuse, and its `404` carries its own message (see the status table).

**A session this host has not ingested yet is a `422`, and it is the ordinary case.** You
know a session exists from the ELN the moment it is created; this host knows it exists only
once its data transfer has landed and been ingested. Between those two moments — routinely
hours, sometimes overnight — a job posted for that session is refused:

```json
{"error": "session pico/2027-06-15T09:00:00 is not yet on record on this host: …"}
```

The full message, which is one line on the wire:

> session `<subject>`/`<session_datetime>` is not yet on record on this host: no Session row
> exists for it, so there is nothing to attach a montage, a run or a request to. wl.works
> knows a session exists from the ELN before its data transfer lands here; until ingest has
> landed it, this host cannot accept a job for it. Resend once the transfer has completed.

The subject and the session datetime are always named, so this is renderable as **"that
session has not arrived on the lab host yet"** rather than as "invalid request". It is a
`422` and not a `409` on purpose: `409` here means *stop and tell a person*, and nobody
needs telling — the situation resolves itself when the transfer completes. Resending later
is the correct client behaviour; resending immediately, in a tight retry loop, is not, and
that is exactly why this is not a `500`.

**A rejected request leaves nothing behind.** Every check runs against candidate rows
before anything is written, so a request refused over a run that disagrees with the measured one does not plant that
assertion, and the corrected request that follows succeeds. That holds for the not-yet-
ingested case too: the check runs before the first write, so nothing is left half-created
for the real ingest to collide with when the transfer does land. The one exception is the request that races a
re-derivation, which is refused after the subject's details and insertion reports were recorded (see *What the
montages add to the refusals*).

### `Content-Length` rules

`POST /jobs` validates the header itself before reading a byte:

| Header | Result |
|---|---|
| Absent | Treated as `0`; the empty body then fails JSON parsing → `422` |
| Plain digits, `0` … `10000000` | Accepted; body read |
| Anything not all digits — `-1`, `1_2`, `+5`, `1.0`, `0x10` | `422` — `{"error": "Content-Length '-1' is not a valid non-negative integer"}` |
| Digits above `10000000` | `422` — `{"error": "Content-Length 10000001 exceeds the 10000000-byte limit"}` |
| Plain digits within the cap that **overstate** the body | `408` after 30 s (see below) |

The digits-only rule is not fussiness about grammar. `int("-1")` succeeds and
`read(-1)` reads until the peer closes, which a live client never does; `int("1_2")` is
`12`, silently reinterpreting a header that is not valid HTTP at all. Both used to hang a
handler thread forever. The 10 MB cap is far larger than any legitimate body — a real
request's metadata is a few hundred small JSON entries — and it bounds **memory only**.
What bounds thread occupancy is the timeout.

### `408` is retryable

The responder sets a **30-second socket timeout** on every request. If a `POST /jobs`
declares a `Content-Length` whose bytes never fully arrive — a dropped connection
mid-upload, a lying header, a client that stalled — the read gives up at 30 s and this host
answers:

```
HTTP/1.0 408 Request Timeout
{"error": "request timed out"}
```

**`408` means the request never fully arrived, so nothing was done with it.** No row was
written, no job was queued, no key was consumed. It is safe and correct for wl.works'
retry loop to resend the identical request with the identical idempotency key, and the
retry is the right response — this is a transport hiccup, not a rejection. It is
`408` specifically, and not `500`, so it can be told apart from a host-side fault: RFC 9110
§15.5.9 defines it for exactly this case.

The timeout scopes strictly to the body read. A timeout raised anywhere downstream —
inside the database write, say — is a `500`, not a `408`, so `408` never claims a body
arrived late when in fact it arrived completely and something else was slow.

### `409` is not retryable, and that is the point

`PUT /positions` has its own `409`s, with a `positions_seq` in the body (see that section): a seq below the
stored one, the stored seq with another set, and a changed set after the session's first accepted request.
They are cured by sending a higher seq, or the stored set, and not by resending. What follows is `POST /jobs`.

`409 Conflict` has three causes on this host, and resending cures none of them: **an
idempotency key reused for materially different content**; a replacement naming a canonical
that is not the montage's current one; and **a run already recorded under one
`works_run_id` named under another**, or an id already recorded for one run named for
another, where the two records disagree about which run it is and a person must settle it. For the first, the request cannot succeed as sent, and the
remedy is a *new key*, which Plan 10 §6.1 puts outside the retry loop's power to produce — the key is
minted once when the confirmation dialog is accepted and reused across every retry of that
intent, never regenerated per click.

So a retry loop that treats `409` as transient will resend the identical key, hit the
identical refusal, and do so forever. **The right client behaviour is to stop retrying and
tell a human** — reopen the confirmation dialog, which mints a fresh key — rather than to
back off and try again. RFC 9110 §15.5.10 describes it exactly: *"the request could not be
completed due to a conflict with the current state of the target resource… the user might
be able to resolve the conflict."*

**The body names what differed and states the remedy**, so it can be shown to that human
rather than paraphrased. It enumerates whichever of `task_type`, `origin`, `requested_by`,
`payload` and `selection` disagree with what is already recorded under that key, and ends
with the sentence `Mint a new idempotency key.`

Because a genuine retry carries the same key **and the same content**, it is a `200`, not
a `409`. **That holds across versions of this contract (since 2026-11-24).** Before comparing, the stored payload
is read again through today's `JobRequest`, so a field added with a default since the request was stored
(`marked_bad`, `bad_reason`, `nickname`) does not make the same body a different request. A stored payload that
today's model refuses is compared as it is stored, so **a field removed since storing still makes an honest
retry a `409`**. A client following Plan 10 §6.1 will never see `409` from an honest retry; it sees
it when two different intents were given one key.

### `500` **is** retryable, infrastructure faults included

Everything that is this host's own fault is a `500` with a clean JSON body naming the
exception type and message — never a traceback, under any input:

```json
{"error": "LostConnectionError: <the database driver's own message>"}
```

**A lost database connection is a `500`, on purpose.** `POST /jobs` writes inside a
transaction, so a MySQL restart or a LAN blip mid-request raises a connection error on the
production path. Those are faults a retry clears, and the retry loop is exactly where they
belong. Every DataJoint error other than key reuse — a lost connection, an access error, a
missing table, an integrity error — is a `500` here, and only key reuse is translated to
`409`.

**One integrity error used to reach here and no longer does.** A job naming a session this
host has not ingested yet violated the foreign key on the first write, and that arrived as
a `500` — which this section tells you to retry, for a request that could not succeed until
a transfer completed. It is now caught before any write and answered `422`; see *`422` is
the caller's mistake* above. Nothing else about this section changed, and no other integrity
error is known to be reachable from a well-formed request: if you see one, it is genuinely
this host's fault and a retry is genuinely the right response.

An earlier draft of the implementation mapped the whole family to `409`, which told
wl.works to stop retrying and escalate to a human for the one class of fault a retry would
have cleared on its own; that is fixed, and the pairing above is the reason the two codes
are worth keeping distinct in the client.

The short version of the distinction, which is the single most useful thing in this
document for whoever writes that client:

> **`408` and `500`: retry, on the retry loop's own schedule.**
> **`422` naming a session this host has not ingested yet: resend the identical request —
> unchanged, same key — once the transfer lands. There is nothing to fix and nobody to
> tell; this is the ordinary case, not an error in what you sent.**
> **`409`: stop, and tell a person.**
> **Every other `4xx`: fix the request or the credential; retrying changes nothing.**

The not-yet-ingested `422` is the one `4xx` that is not the caller's mistake to correct,
which is why it is called out here rather than left inside the general `4xx` rule. Tell it
apart by its message, which always begins `session <subject>/<session_datetime> is not yet
on record on this host`; the status table above and *`422` is the caller's mistake* below
both say the same. Resending it in a tight loop is still wrong — the resend belongs on the
timescale a data transfer takes, not on the timescale a `500` retry does.

---

## The three numbers

Plan 10 defers poll cadence, request timeout and the per-host refresh cooldown to
environment configuration and gives no value for any of them. A protocol document with no
timing in it is what leaves two sides guessing, so each is proposed here with its
reasoning. They are proposals, not requirements: wl.works owns all three, because all
three describe how hard the app is willing to push rather than a property of this host.

### Poll cadence: **60 s**

The scheduled sweep interval (Plan 10 §5.1, global environment configuration).

`lab_host.stale_after_seconds` defaults to **300**, and that is the only thing that turns
silence into `unknown`. At 60 s this host must miss **five consecutive polls** before its
card blanks, so a responder restart, a token rotation, or one dropped health check never
blanks the board — while the home page's featured reading is never more than a minute old.
Going faster buys nothing: no reading here changes on a shorter timescale than that (the
ingest window is 24 h, the quarantine window 7 days, the stall threshold 2 h). Going
slower widens the gap between "the transfer finished" and the dashboard saying so, which
is the question the feature exists to answer.

### Request timeout: **10 s** for `GET /health`, **30 s** for `POST /jobs`

The hard timeout on each outbound call (Plan 10 §5.3 layer 3, §5.5). If only one constant
is available, use **30 s**.

`GET /health` is not free. It runs three database queries, lists the storage root, reads one
manifest per session directory, runs one `statvfs`, and — for each session still
**incomplete** — walks that session's tree for its newest mtime. On a host mid-transfer,
over NAS storage, that walk is the term that can take seconds. **All of it runs under the
same process-wide lock `POST /jobs` takes**, so a health check can also wait behind a job
write. 10 s gives the realistic case an order of magnitude of headroom while staying a
sixth of the poll cadence, so even a fully hung host never leaves two health checks
outstanding at once.

`POST /jobs` gets the longer bound because truncating it is worse than waiting: it does
real database work, behind that same lock, and a client that gives up early has learned
nothing about whether the write happened. 30 s also lines up with this host's own
30-second socket timeout, so the two sides give up on the same scale rather than one
silently outliving the other. Aborting early is nonetheless safe — the idempotency key
makes the retry converge on the same activation.

### Per-host refresh cooldown: **30 s**

The floor between on-demand refreshes of one host (Plan 10 §5.5). A health check newer
than this returns the existing observation instead of making a call.

The abuse profile is a member holding down the refresh button, and this number is the only
thing on the app's side that bounds it. At 30 s against a 60 s sweep, the worst case a
leaned-on button can produce is **twice** the scheduled load on this host rather than a
multiple of it — which matters more here than it would for a passive monitor, because
health checks contend with `POST /jobs` for the one lock. It stays short enough to answer
the question the feature was requested for: a member who clicks refresh, waits half a
minute and clicks again gets a genuinely fresh look rather than the same cached
observation.

---

## Running the responder

```
wlpp responder --port PORT --root ROOT [--prefix PREFIX]
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--port` | **yes** | **none** | TCP port to bind, 0–65535. An out-of-range value is refused by argument parsing, not forty frames deep in the socket layer. `0` is legal and asks the OS for an ephemeral port — useful in tests, never in a unit file. |
| `--root` | **yes** | **none** | The storage root holding session directories. `GET /health` reads it — the disk, stall and scan readings are all about this directory. |
| `--prefix` | no | `wlpp_` | The schema prefix. |

`WLPP_RESPONDER_TOKEN` must be set in the environment. There is no `--token` flag, so the
token never appears in a process listing or a shell history.

### The port must be written identically in three places

The systemd unit, this document, and whatever wl.works is configured with. That is why
`--port` has **no default**: a default invites two of the three to disagree silently, and
the failure is a host that looks permanently `unknown` on the dashboard while running
perfectly well on a port nobody is asking.

**Proposed: `8420`.** It is above 1024, so the unit needs no `CAP_NET_BIND_SERVICE` and can
run as an unprivileged user; it is below the Linux ephemeral range floor
(`net.ipv4.ip_local_port_range`, default 32768–60999), so the kernel can never hand it out
as an outbound source port and steal it from a restarting responder; and it is unassigned
in the IANA service-name registry (checked against `/etc/services`, where its neighbours
8417 and 8443 are assigned and 8420 is not). Confirm once, at install time, that nothing
else on the host already listens there — then write it in all three places.

### A systemd unit

Illustrative rather than prescriptive — which supervisor runs this host's services is an
open ops decision (Phase 1c-1 design, "Who runs the daemon"). What is not optional is
where the port and the token come from.

```ini
[Unit]
Description=wl-preproc responder for wl.works
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wlpp
EnvironmentFile=/etc/wlpp/responder.env
ExecStart=/opt/wlpp/venv/bin/wlpp responder --port 8420 --root /srv/sessions
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

`/etc/wlpp/responder.env` holds `WLPP_RESPONDER_TOKEN=…` and should be mode `0600`, owned
by root. Use `EnvironmentFile=`, **not** `Environment=`: values written inline in a unit
file are visible to any local user through `systemctl show`.

The responder also needs whatever DataJoint configuration the rest of the pipeline uses;
`wlpp doctor` is what checks that the database is reachable from this host at all.

### Exit codes

Re-derived by running the shipped CLI, one subprocess per row, rather than read off the
source — most recently 2026-08-16, in full rather than row by row. `2` means **refusing to
start**: the invocation or the configuration is wrong and nothing was bound. `1` means it
tried and the OS said no. `0` means it never tried, and asking for help is the only way to
reach it in practice.

| Code | Cause | What the journal shows |
|---|---|---|
| `2` | `WLPP_RESPONDER_TOKEN` unset, or empty (`WLPP_RESPONDER_TOKEN=` in the unit file) | `error: WLPP_RESPONDER_TOKEN is not set (or is empty); refusing to start a responder with no token -- there is no default.` |
| `2` | `WLPP_RESPONDER_TOKEN` containing a non-ASCII character | `error: refusing to start: the configured bearer token contains a non-ASCII character; …` — it would fail every request, the correct one included |
| `2` | `--root` naming a path that does not exist, names a file, or is only whitespace | `error: --root '/srv/sesions' does not exist or is not a directory; refusing to start a responder with a broken storage root.` |
| `2` | `--root=` with nothing after it — `--root=${WLPP_ROOT}` with `WLPP_ROOT` unset expands to exactly this | the same message, with `''` — **not** the working directory, which is what an earlier version silently served |
| `2` | `--port` outside 0–65535 | `wlpp responder: error: argument --port: 99999 is not a TCP port (must be 0-65535)` |
| `2` | `--port` not an integer | `wlpp responder: error: argument --port: invalid tcp_port value: 'abc'` |
| `2` | `--port` or `--root` absent | `wlpp responder: error: the following arguments are required: --port` (or `--root`) |
| `2` | No subcommand at all — including `wlpp --nope`, where the missing subcommand is reported and the unrecognised flag is not | `wlpp: error: the following arguments are required: group` |
| `2` | An unrecognised flag *after* a valid subcommand | `wlpp: error: unrecognized arguments: --nope` (from `wlpp doctor --nope`). Prefixed `wlpp:` rather than `wlpp responder:` because the top-level parser collects the leftovers |
| `1` | The bind failed | `error: responder failed on port 8420: <the OS's own errno text>` — measured as `[Errno 48] Address already in use` on the development machine; Linux reports that same condition as errno 98. A restart racing the previous process is the usual cause; a privileged port with no capability to bind it arrives on this same path |
| `0` | `--help`, on any subcommand or on `wlpp` itself | argparse's own help text on stdout. Measured across all 11 `--help` forms this CLI has; every one exits `0` |
| `0` | Otherwise only if `serve_forever()` returns, which nothing in this CLI asks it to | — in practice a running responder ends on a signal instead |

**The order the checks run in, which decides what you see when two things are wrong at
once.** There are **four** gates, not three, and the two token checks are not adjacent:

1. **Argument parsing** (argparse: `--port` range and type, required flags, unknown flags,
   the subcommand itself). Wins over everything below — measured, a bad `--port` *and* an
   unset token *and* a missing `--root` together report only the `--port` error.
2. **`WLPP_RESPONDER_TOKEN` unset or empty.** Measured, this beats a broken `--root`.
3. **`--root`.**
4. **A non-ASCII `WLPP_RESPONDER_TOKEN`** — raised from inside `make_handler`, which
   `serve()` calls, so it runs **after** `--root` has already passed. Measured: a non-ASCII
   token together with a `--root` that does not exist reports the **`--root`** error, not
   the token one. It is still before any socket is bound, and it is still exit `2`.

So "the token" is not one gate: an unset token is refused before `--root` and a non-ASCII
one after it. The guard lives in `make_handler` by a deliberate Task 9 ruling — one place
for it, not a CLI copy free to drift — which is why the two halves sit where they do.

No row prints a traceback. Every one is a single line an operator reads in a journal.

---

## What this protocol does not carry

Declined rather than skipped, so a later revision overturns a decision instead of
discovering an option.

- **No job status endpoint.** `POST /jobs` returns an activation key; there is no
  `GET /jobs/<id>`, and nothing calls back when work finishes. Today a submitted job's
  progress is not observable through this protocol at all. When it becomes observable it
  will be as a **reading**, because readings are the surface this host already publishes
  and wl.works already polls — not as a new endpoint.
  - *Amended 2026-09-29 for NWB files, not job progress: `GET /nwb` is a new endpoint,
    because what wl.works needs there is a list of files, each with its location and its
    description, which a reading's single `label`/`value` string cannot carry
    (`docs/superpowers/specs/2026-09-29-nwb-publishing-design.md` section 6). Job progress
    itself is unchanged by this: still a reading when it arrives.*
- **No result upload.** wl.works pulls; this host never pushes. Rows 27 and 29 already
  discover their outputs by polling the NAS.
- **No TLS.** Plan 10 §5.4 makes plain HTTP the stated default for this leg and argues it:
  the call never leaves the building, and an internal CA or self-signed certificates on
  every lab appliance is real friction for confidentiality against an attacker already
  inside the LAN. If this endpoint ever becomes reachable from outside the lab, TLS and a
  reverse proxy in front of it are the precondition, not an improvement.
- **No permission model on this host.** wl.works' is flat by design; inventing per-action
  roles here would put this host in the business of deciding who in the lab may do what.
  The bearer token is an authentication boundary, not an authorisation one: it separates
  wl.works from everything else on the LAN, and does not separate lab members from each
  other. That is what makes the most prominent line at the top of this document load-bearing.
- **No rate limiting.** One known client, with a cooldown on its own side.
- **No `Retry-After` header.** The retry policy lives in wl.works' client; this host does
  not attempt to schedule it.

---

## Machine-readable schemas

`wlpp schemas export` writes JSON Schema for every wire contract, and CI diffs the
directory so a drifted export fails the build:

- [`docs/schemas/health_response.json`](../schemas/health_response.json)
- [`docs/schemas/job_request.json`](../schemas/job_request.json)
- [`docs/schemas/nwb_listing.json`](../schemas/nwb_listing.json) and
  [`docs/schemas/nwb_description.json`](../schemas/nwb_description.json), `GET /nwb`
- [`docs/schemas/active_set_request.json`](../schemas/active_set_request.json), `PUT /nwb/active`
- [`docs/schemas/session_listing.json`](../schemas/session_listing.json), `GET /sessions`
- [`docs/schemas/positions_request.json`](../schemas/positions_request.json), `PUT /positions`

These are what wl.works' contract tests should validate against, and they are why this
protocol needed no OpenAPI-generating web framework on the box that holds every session's
raw recordings.

---

## Provenance

Written 2026-08-16 against the shipped responder in `wl_preproc/responder/`
(`handler.py`, `server.py`, `health.py`, `actions.py`, `jobs.py`),
`wl_preproc/contracts/protocol.py`, and `wl_preproc/cli/main.py`. Every status code, header
and error body above was measured against a real `ThreadingHTTPServer` running the shipped
handler, not read off a design document. Design: [Phase 1c-3
§7](../superpowers/specs/2026-08-15-phase-1c3-responder-design.md).

**Revised 2026-08-16, after review found three of its claims false.** All three had been
written from reasoning about the source rather than from running it, which is the failure
mode the paragraph above exists to prevent — one of them (`--help` exiting `2`) was true
when written and made false by a fix one commit later, with no sweep of the document
describing it. Each revision was re-measured: the exit-code table and the check-ordering
list by running the shipped CLI once per row in its own subprocess, and the
`session_datetime` sub-second contract by POSTing every spelling of one landed session's
timestamp to a real `ThreadingHTTPServer`.

**Proposed to wl.works, not written into it.** If they accept it, the natural home is
`docs/ops/lab-host-protocol.md` in that repository, with this copy kept in step. Amendments
to their documents are staged in [`docs/pending-wl-works-amendments.md`](../pending-wl-works-amendments.md)
in the append-only form that file establishes, and are applied by hand after
re-verification — never unilaterally.
