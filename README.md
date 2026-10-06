# TFA — Tax Filings Analyser

Reads a client's income-tax returns and tax audit reports for several years,
holds them as one fact matrix, and applies a canonical rule set to find what
does not agree — between the two documents for a year, between one year and the
next, and against the thresholds the firm has declared.

It is an independent system with its own interpreter, dependencies, knowledge
files and database, deployable on its own. It shares its *shape* with WPR — the
working paper review that sits beside it — because both come from the same
method: a declared context, a pinned body of law, deterministic rules first,
model judgment only where arithmetic cannot answer, and every finding tied to
the exact place it came from. The subject is different, so the system is
separate, and nothing here reads anything there.

## Where things go

```
TFA/
  filings/<CLIENT-CODE>/          the JSON exported by the e-filing utility
  clients/<CLIENT-CODE>.yaml      what the firm declares about the assessee (optional)
  scrutiny/reference/             scrutiny material we write rules from
  scrutiny/<CLIENT-CODE>/         that client's own notices, replies and orders
  tfa/knowledge/                  the method: law, facts, rules, doctrine
  tfa/store/                      the register: schema, migrations, matter identity
  generator/                      a synthetic client with defects we planted
~/.tfa/pgdata                     the local Postgres, deliberately outside the repo
```

Drop the files in `filings/<CLIENT-CODE>/`, flat or in `AY2023-24/`
subfolders — both work. **File names do not matter.** Whether a file is a
return or an audit report, which form it is, and which assessment year it
belongs to are read out of the contents; the folder name is only a cross-check,
and a disagreement between them is shown on the attendance tab. Fewer than five
years is fine. Missing years are fine — saying what is absent is half the job.

## Running it

```bash
cd /Users/sivaprasadannavarapu/TFA        # or wherever it is deployed

python3.12 -m venv .venv                  # first time only
.venv/bin/python -m pip install -r requirements.txt

./run.sh -m tfa.server                     # http://127.0.0.1:8814
```

`run.sh` uses `./.venv` and nothing else. Everything below can be run the same
way — `./run.sh -m tfa.cli …` — or with the virtualenv's Python directly.

Then press **Run analysis**. Headless:

```bash
./run.sh -m tfa.cli list                   # client folders on disk
./run.sh -m tfa.cli run ACME-PVT-LTD       # analyse one client
./run.sh -m tfa.cli run ACME-PVT-LTD --no-ai
./run.sh -m tfa.cli matrix ACME-PVT-LTD    # the fact matrix as a table
./run.sh -m tfa.cli rules                  # the canonical rule catalogue
./run.sh -m tfa.cli db --migrate           # the register, and its schema
./run.sh -m tests.test_invariants          # the promises the system rests on
./run.sh -m tests.test_store               # the register survives a re-run
./run.sh -m generator.generate --clean     # rewrite the synthetic client
```

The API key goes in `env` at the project root, which is re-read on every run.
With no key, every deterministic rule still runs and the parts that need a
model report as **not assessed** rather than as passed.

## The register

The analysis is a function of the filings and is recomputed every time. What
cannot be recomputed is the work: that a matter was accepted, by whom, with
what note, and which enquiries have been answered. That is what the database
holds.

```bash
./run.sh -m tfa.store.local start    # embedded Postgres in ~/.tfa/pgdata
                                     # prints a TFA_DB_URL to paste into env
./run.sh -m tfa.cli db --migrate     # where it is, and apply the schema
./run.sh -m tfa.cli register ACME-PVT-LTD --state open
```

`tfa.store.local` is for development and single-machine use: `pgserver` ships a
real Postgres in a wheel, so nothing has to be installed or looked after. For
anything shared, point `TFA_DB_URL` at a proper server **and a database of
TFA's own** — client tax data does not belong in another application's schema.
The data directory sits outside the repository on purpose; a database file
inside a working tree gets committed, archived and emailed by accident.

With no database configured, every rule still runs and the screen says the run
was not recorded. Persistence is optional; silence about it is not. Set
`TFA_DB_URL=off` to turn recording off deliberately — an empty value means
"look in the shell", which is a different statement from "do not record".

**The test suites cannot touch your register.** `tests.test_invariants` runs
with recording off. `tests.test_store` creates a database of its own
(`tfa_selftest`), works in that, and drops it; it refuses to run at all if
`TFA_DB_URL` points anywhere else. An earlier version of that test truncated
whatever the URL pointed at, which on this machine was the working register —
the guard is a bug that has already been paid for once.

### What is stored, and what is not

*Immutable, written once:* the run and its manifest, the filings (content
addressed, with the JSON kept as a compressed blob so a run stays re-derivable
after someone tidies the folder), every located fact cell, and every finding.

*Durable, and the point of the whole thing:* matters, dispositions and
enquiries.

*Not stored:* trends, the narrative and the report. All three are functions of
the tables above, and storing a derived value is how a system starts
disagreeing with itself.

### Matter identity — the part worth understanding

A matter is keyed on **what produced it**, not on when:

```
matter_key = sha256(client | rule | year | fingerprint of the cited values)
```

So on a re-run:

* the same figures raise **the same matter**, and last week's decision holds;
* changed figures raise a **new** matter, and the old one is closed as
  *superseded* with the lineage recorded — because "agreed, the client will pay
  it" said of ₹11.8 lakh is not a decision about ₹19.4 lakh;
* a matter that stops being raised is closed as *no longer arising*;
* a matter that comes back is **reopened**, with its whole history intact.

`./run.sh -m tests.test_store` exercises exactly that sequence against a real
database: raise, accept with a note, change the return, prove the accepted
matter is superseded rather than reused, then withdraw the change and prove the
original comes back with its history.

### Moving it to a shared server

All SQL is plain and parameterised, and every statement lives in
`tfa/store/`. Point `TFA_DB_URL` at the server, run `python3 -m tfa.cli db
--migrate`, and nothing else changes. Migrations are checksummed once applied:
edit an applied migration and the next run refuses to start, because the schema
in the database and the schema the code expects are no longer the same thing.
Add a new file instead.

## The five layers, and the contract between them

The value is not in any one layer; it is in the order, and in the fact that
each hands the next something narrower than it received.

```
  1  EXTRACT     JSON  ->  located facts        Python.  Every cell keeps its
                                                file and JSON pointer.
  2  MEASURE     facts ->  trends and charts    Python.  Year-on-year, CAGR,
                                                volatility, outliers; charts
                                                declared in a catalogue.
  3  DECIDE      facts ->  findings             Python.  70 canonical rules:
                                                tie-outs, continuity,
                                                arithmetic, thresholds.
  ---------------------------------------------------------------------------
  4  INFER       BRIEF ->  inference            The model, once per declared
                                                question, over a brief written
                                                by layers 1-3.
  ---------------------------------------------------------------------------
  5  ASSEMBLE    all   ->  report, register,    Python.  Observations,
                           audit programme      vulnerabilities, and the
                                                programme of work.
```

The line above and below layer 4 is the product. Everything a model is given is
a **brief** that Python wrote, and a brief carries exactly four things:

* **the series** — the same numbers the reader is looking at on the chart, with
  the movement already computed and a citation handle beside each figure;
* **what is settled** — the deterministic findings on that subject, marked as
  settled, with an instruction not to restate them;
* **the background** — what else was true in those years, quoted from
  `knowledge/context/`: the pandemic years, an input-cost cycle, the year
  section 43B(h) began to apply. *A model may correlate against these entries
  and against nothing else*, so "correlate the margin fall with the trade
  cycle" is answered from a file somebody maintains rather than from a model's
  memory;
* **one question**, with the standard for answering it.

The model gets no raw JSON, no free run of the fact matrix, and no ability to
choose its own evidence. It returns aberrations, correlations, and — the part a
reviewer acts on — **hypotheses, each with what would confirm it, what would
refute it, and the document to call for**. Every one of those is validated on
the way back: citations must resolve to cells that were actually offered, a
correlation to an undeclared factor is dropped, prohibited wording is withheld,
and a conclusion with nothing cited is downgraded.

Then layer 5 is deterministic again: the audit programme is assembled from what
the rules found, what the series did, and what the inference layer named — with
the procedures for each area coming from a maintained library, plus the
"call for X" from each hypothesis.

> **Why this rather than handing the file to a model?** A model given the whole
> file writes something plausible about anything, and nobody can tell which
> sentences are load-bearing. A model given a brief can only answer the question
> in front of it, and every sentence it writes cites a cell that can be checked
> against the chart on the page. The orchestration *is* the product; the model
> is one bounded step inside it.

## How the trends and charts are disclosed

Charts are part of the method, not decoration:

* they are **declared** in `knowledge/charts/canonical-v0.1.0.yaml` — which
  series, in what unit, with the note saying what a reader should take from it;
* they are **drawn by Python** as inline SVG that themes with the page and
  prints, so the screen and the report render the same drawing from one source;
* every point **keeps its cell**, so hovering gives the year and the value and
  the underlying pointer is one click away in the fact matrix;
* a gap is a gap — the line breaks across a year with no filing rather than
  joining over it, and the card says which years are missing;
* the ten charts marked `report: true` appear in the report in catalogue order,
  under the heading **The factual picture**, prefaced by a statement that they
  carry no inference.

The report therefore reads in one fixed sequence, and each section says what
kind of statement it is making:

| Section | What it is |
|---|---|
| Scope and the records provided | what was analysed, and what was missing |
| The factual picture | charts and series, stated as fact |
| What the filings say | observations from the deterministic rules |
| What the years suggest when read together | inference, with what would settle each |
| Where these matters expose the assessee | vulnerabilities (internal) |
| Audit programme indicated | areas and procedures, with what put each there |

## How it works

1. **Preflight.** The client context, the authority pack, the doctrine and the
   rule catalogue are loaded and validated. A rule citing a section the pack
   does not carry stops the run here — that is a defect in our method, and it
   must not reach a client as a finding about theirs.
2. **Read.** Every file in the folder is opened and identified from its
   contents. What could not be read is recorded, and an unread file that could
   have been a filing disables every later statement that something was never
   filed.
3. **Attendance.** Which of the two documents is present for each year in the
   span, which years are missing, and which filings supersede which.
4. **Fact matrix.** Each declared fact is located in each year, carrying the
   file and the JSON pointer it was read from. A fact resolved by searching for
   a field name rather than at a known path is marked as such. A fact that is
   not there is *not available* — never zero, never carried forward.
5. **Trends.** Year-on-year movement, compound change, volatility and outliers
   for every series worth trending. Measurement only.
6. **Rules.** 70 canonical rules, instantiated once, once per year, or once per
   year that has a predecessor. Applicability, then dependencies, then the
   rule, then the guards.
7. **Open comparison.** The whole matrix is put to the model with no question
   in front of it: what cannot be right? Anything it finds that the rules
   already cover is suppressed; anything it cannot cite is dropped.
8. **Report and enquiries.** Client-facing observations, through a gate that
   refuses to release a rule identifier or a word the language policy forbids —
   and, separately, the list of what to ask and which document would settle it.

## The four files that are the method

| File | What it holds |
|---|---|
| `tfa/knowledge/rules/canonical-v0.1.0.yaml` | the rules: question, how decided, provision cited, severity, what it exposes, what to ask |
| `tfa/knowledge/facts/fact-map-v1.0.0.yaml` | the facts, and where to look for each one inside the JSON |
| `tfa/knowledge/authority/IN-INCOMETAX-v1.0.0.yaml` | the law, quoted, with the dates it was in force |
| `tfa/knowledge/doctrine-v1.0.0.yaml` | the standing rules, and every threshold |
| `tfa/knowledge/charts/canonical-v0.1.0.yaml` | the charts: which series, in what unit, and what to take from each |
| `tfa/knowledge/context/IN-CONTEXT-v1.0.0.yaml` | what else was true in each year — the only thing an inference may correlate against |
| `tfa/knowledge/inference/canonical-v0.1.0.yaml` | the inference catalogue: every use of a model in this system |
| `tfa/knowledge/procedures/canonical-v0.1.0.yaml` | the audit programme: areas, what triggers each, and the procedures |

All four are published on the **Canonical rules** tab, rendered from what the
engine actually loaded, so the screen cannot drift from what runs.

### Adding a rule

Most rules need no Python. The implementations are parameterised, so a new
canonical rule is usually an entry naming an existing one:

```yaml
  - id: TIE-012
    theme: tie_out
    question: Does the interest disallowed in the return agree with the audit report?
    method: rule
    rule: tie_out                      # tie_out | continuity | restated_comparative
    for_each: year                     # none | year | year_pair
    params:
      left: interest_disallowed_3cd    # must be a fact in the dictionary
      right: interest_disallowed_itr
      subject: interest disallowed
    authority: [ITA-40a-ia]            # must resolve in the authority pack
    severity: high
    requires: [ATT-002, ATT-003]
    vulnerability: >-
      What this exposes if it is left as it stands.
```

If the fact is not in the dictionary yet, add it there first with the paths to
look for it at. `python3 -m tests.test_invariants` will refuse a rule that
names a fact or a provision that does not exist.

### Adding an inference

An inference is a question, a rubric, the series it may see, and the factors it
may correlate against. No Python:

```yaml
  - id: INF-009
    topic: profitability
    title: Whether the margin recovery is real
    charts: [CHT-001, CHT-002]
    facts: [gp_ratio_3cd, material_ratio_3cd, stock_ratio_3cd]
    settled_themes: [trend, arithmetic]
    correlate_with: [trade_cycle, input_costs]
    question: >-
      Is the recovery in gross margin corroborated by the input series?
    rubric: >-
      PASS ... FAIL ... INSUFFICIENT_EVIDENCE where fewer than three years carry
      the series. Never assert a cause the cells and the context do not support.
    authority: [FORM3CD-CL40]
    severity: high
```

It will refuse to load if it reads a fact that is not in the dictionary, points
at a chart that does not exist, cites law the pack does not carry, correlates
against a factor the context file does not declare, or has no rubric.

### Adding a period to the context

Correlation is only as good as the background. `IN-CONTEXT` carries one entry
per assessment year — trading conditions, statutory changes, filing extensions
— each dated and marked `verified: false` until a person has checked it. An
inference resting on an unverified entry says so on its own card.

### Adding a threshold

Put it in `doctrine-v1.0.0.yaml` and read it with `ctx.param("name", default)`.
A threshold hard-coded in a rule is a policy nobody agreed to, and it will not
appear in the run manifest that makes the finding re-derivable.

## Deploying it

TFA depends on nothing outside its own folder except a Python interpreter and,
optionally, a Postgres server. To put it on a machine:

```bash
git clone <repo> tfa && cd tfa          # or copy the folder
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp env.example env                      # then edit: API key, TFA_DB_URL
./run.sh -m tfa.cli db --migrate
./run.sh -m tfa.server --port 8814
```

What the host must provide:

| | |
|---|---|
| Python | 3.10 or later; 3.12 is what it is developed against |
| Database | a Postgres of TFA's own. Leave `TFA_DB_URL` empty and it runs without one, saying on every screen that nothing is being recorded |
| Model access | an Anthropic API key. Without one every deterministic rule still runs and the model-dependent parts report as not assessed |
| Disk | the filings folder, the model-response cache (`.tfa-cache`), and the database if it is local |
| Network | none, except to the model and the database. The service binds to loopback only |

Three things belong outside the deployed folder: the database (client tax data
should not live in a working tree), the API key once this leaves one desk, and
the filings themselves if they are mounted from elsewhere — `TFA_FILINGS_DIR`
points anywhere.

The service is single-process and binds to `127.0.0.1`. Putting it behind a
shared address needs an authenticating proxy and a decision about who "the
actor" is on a disposition, which is today an initial kept in the browser.

## What it will not do

* It does not read the books, the ledgers or the GST returns. It reads what was
  filed. Every finding is a question to put to the client or a reconciliation
  to call for — not a conclusion about their affairs.
* It does not assert evasion, concealment or intent, and it does not
  characterise a person. The language policy is enforced before the wording
  enters the record, not after it has left.
* It does not treat a missing figure as nil, fill a gap forward, or draw a
  five-year trend through two points without saying so.
* It does not decide anything on your behalf. Closing a matter as resolved or
  superseded is a statement about the filings, never about the merits, and a
  person's own decisions are only ever appended to — a change of mind is
  another row, not an edit.
* The authority pack is an **unverified draft**. Every provision in it needs
  checking against the bare Act by a person, and until that is done the
  citations shown are indicative and `run_mode: production` is refused.
