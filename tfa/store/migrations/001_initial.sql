-- ---------------------------------------------------------------------------
-- TFA schema, first migration.
--
-- Three kinds of table, and the distinction is the whole design:
--
--   IMMUTABLE   run, run_filing, fact_cell, finding, filing, filing_blob.
--               Written once, never updated. A run is a statement about what
--               the filings said at a moment in time, and rewriting it would
--               destroy the only thing that makes a finding re-derivable.
--
--   DURABLE     matter, disposition, enquiry. These carry the work: what a
--               person decided about a finding and what was asked of the
--               client. They survive re-runs, which is why they are keyed on
--               the matter rather than on the run.
--
--   DERIVED     nothing. Trends, the narrative and the report are functions of
--               the tables above and are recomputed. Storing them is how a
--               system starts disagreeing with itself.
--
-- Everything is scoped by client_id. There is no cross-client query in the
-- application; the schema merely makes one possible later, deliberately.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migration (
    id          text        PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    digest      text        NOT NULL
);

-- --------------------------------------------------------------- the client
CREATE TABLE IF NOT EXISTS client (
    client_id       text        PRIMARY KEY,
    name            text,
    pan             text,
    entity_type     text,
    declared        boolean     NOT NULL DEFAULT false,
    context         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN client.declared IS
  'True where a clients/<id>.yaml exists. Where false, the identity was read '
  'out of the filings and no declaration-versus-filing comparison was made.';

-- -------------------------------------------------------------- the filings
-- Content-addressed: the digest is the identity. A client who re-sends the
-- same file does not create a second row, and a file that is renamed or moved
-- is still recognised as the one a past run consumed.
CREATE TABLE IF NOT EXISTS filing (
    digest          text        PRIMARY KEY,
    client_id       text        NOT NULL REFERENCES client(client_id),
    ay              text,
    kind            text,                    -- itr | 3cd | unknown
    form            text,
    rel_path        text        NOT NULL,
    bytes           integer     NOT NULL,
    readable        boolean     NOT NULL,
    detection       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    superseded_by   text        REFERENCES filing(digest)
);

CREATE INDEX IF NOT EXISTS filing_client_year ON filing (client_id, ay, kind);

-- The bytes themselves, so a run stays re-derivable after somebody tidies the
-- folder. Keyed on the digest, so a re-sent file costs nothing.
CREATE TABLE IF NOT EXISTS filing_blob (
    digest      text        PRIMARY KEY REFERENCES filing(digest) ON DELETE CASCADE,
    content     bytea       NOT NULL,        -- the JSON as read, gzip compressed
    stored_at   timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ the run
CREATE TABLE IF NOT EXISTS run (
    run_id          uuid        PRIMARY KEY,
    client_id       text        NOT NULL REFERENCES client(client_id),
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL DEFAULT now(),
    run_mode        text        NOT NULL,
    years           text[]      NOT NULL DEFAULT '{}',
    -- what the run was measured against; a finding cannot be re-derived
    -- without knowing which version of each of these produced it
    rules_version   text        NOT NULL,
    rules_digest    text        NOT NULL,
    factmap_version text        NOT NULL,
    factmap_digest  text        NOT NULL,
    pack_version    text        NOT NULL,
    pack_digest     text        NOT NULL,
    doctrine_digest text        NOT NULL,
    ai_available    boolean     NOT NULL DEFAULT false,
    model           text,
    ai_usd          numeric(12,6) NOT NULL DEFAULT 0,
    findings_total  integer     NOT NULL DEFAULT 0,
    findings_raised integer     NOT NULL DEFAULT 0,
    exposure_inr    numeric(18,2) NOT NULL DEFAULT 0,
    manifest        jsonb       NOT NULL,
    manifest_digest text        NOT NULL
);

CREATE INDEX IF NOT EXISTS run_client_time ON run (client_id, finished_at DESC);

CREATE TABLE IF NOT EXISTS run_filing (
    run_id      uuid    NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    digest      text    NOT NULL REFERENCES filing(digest),
    in_play     boolean NOT NULL DEFAULT true,
    PRIMARY KEY (run_id, digest)
);

-- ----------------------------------------------------------- the fact matrix
-- One row per located figure per run. Cheap, and the only way to answer "what
-- did this fact do across every client in this sector" later.
CREATE TABLE IF NOT EXISTS fact_cell (
    run_id      uuid    NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    fact_id     text    NOT NULL,
    ay          text    NOT NULL,
    value_num   numeric(20,4),
    value_text  text,
    pointer     text,
    file_digest text    REFERENCES filing(digest),
    resolved_by text    NOT NULL,            -- path | key search | computed
    PRIMARY KEY (run_id, fact_id, ay)
);

CREATE INDEX IF NOT EXISTS fact_cell_fact ON fact_cell (fact_id, ay);

-- --------------------------------------------------------------- the matter
-- The durable identity of a thing that is wrong, across runs.
--
-- matter_key = sha256(client_id | rule_id | ay | fingerprint of the cited
-- values). Same key means the same matter in the same state, so a disposition
-- recorded weeks ago still applies. Different values mean a different matter:
-- the old one is closed as superseded and the new one raised, because a note
-- saying "agreed, the client will pay it" must not silently attach itself to a
-- different figure.
CREATE TABLE IF NOT EXISTS matter (
    matter_key      text        PRIMARY KEY,
    client_id       text        NOT NULL REFERENCES client(client_id),
    rule_id         text        NOT NULL,
    instance_id     text        NOT NULL,
    ay              text,
    theme           text,
    severity        text,
    question        text,
    -- the wording as first raised; later runs may reword it and the change is
    -- visible in the finding rows rather than by overwriting this
    statement       text,
    observation     text,
    enquiry_text    text,
    vulnerability   text,
    amount_inr      numeric(18,2),
    fingerprint     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    state           text        NOT NULL DEFAULT 'open',
        -- open | accepted | rejected | amended | resolved | superseded
        --   accepted  a person agrees it is a matter to address
        --   rejected  a person disagrees, with a reason on record
        --   amended   accepted with the wording or amount changed
        --   resolved  no longer arising: the filings that produced it changed
        --   superseded the same rule and year now reports different figures
    closed_reason   text,
    superseded_by   text        REFERENCES matter(matter_key),
    first_run       uuid        NOT NULL REFERENCES run(run_id),
    last_run        uuid        NOT NULL REFERENCES run(run_id),
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    times_raised    integer     NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS matter_client_state ON matter (client_id, state, severity);
CREATE INDEX IF NOT EXISTS matter_lineage ON matter (client_id, rule_id, ay);

-- -------------------------------------------------------------- the finding
-- What a given run said about a matter. Immutable, and one row per run, so the
-- history of a matter across runs is a query rather than a guess.
CREATE TABLE IF NOT EXISTS finding (
    run_id          uuid        NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    instance_id     text        NOT NULL,
    matter_key      text        REFERENCES matter(matter_key),
    rule_id         text        NOT NULL,
    theme           text,
    ay              text,
    verdict         text        NOT NULL,
    severity        text,
    confidence      numeric(4,3),
    amount_inr      numeric(18,2),
    statement       text,
    observation     text,
    enquiry_text    text,
    method          text,
    provenance      text,
    needs_review    boolean     NOT NULL DEFAULT false,
    na_reason       text,
    skipped_reason  text,
    evidence        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    facts_used      text[]      NOT NULL DEFAULT '{}',
    authority       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, instance_id)
);

CREATE INDEX IF NOT EXISTS finding_matter ON finding (matter_key);
CREATE INDEX IF NOT EXISTS finding_verdict ON finding (run_id, verdict);

-- ---------------------------------------------------------- the disposition
-- Append-only. A decision is never edited: a change of mind is another row,
-- and the current state of the matter is the latest row's action.
CREATE TABLE IF NOT EXISTS disposition (
    id          bigserial   PRIMARY KEY,
    matter_key  text        NOT NULL REFERENCES matter(matter_key) ON DELETE CASCADE,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       text        NOT NULL,
    action      text        NOT NULL,    -- accept | reject | amend | note | reopen
    note        text,
    amount_inr  numeric(18,2),
    run_id      uuid        REFERENCES run(run_id)
);

CREATE INDEX IF NOT EXISTS disposition_matter_time ON disposition (matter_key, at DESC);

-- -------------------------------------------------------------- the enquiry
-- What was asked of the client, and what came back. The point of the whole
-- analysis is to produce these and then close them.
CREATE TABLE IF NOT EXISTS enquiry (
    id              bigserial   PRIMARY KEY,
    matter_key      text        NOT NULL REFERENCES matter(matter_key) ON DELETE CASCADE,
    client_id       text        NOT NULL REFERENCES client(client_id),
    question        text        NOT NULL,
    status          text        NOT NULL DEFAULT 'to_ask',
        -- to_ask | asked | answered | withdrawn
    asked_at        timestamptz,
    asked_by        text,
    channel         text,
    answer          text,
    answered_at     timestamptz,
    document_ref    text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS enquiry_client_status ON enquiry (client_id, status);

-- ------------------------------------------------------------------- views
-- The register as a person reads it: every open matter with its latest
-- decision and how many runs have raised it.
CREATE OR REPLACE VIEW matter_register AS
SELECT m.*,
       d.action     AS last_action,
       d.actor      AS last_actor,
       d.at         AS last_action_at,
       d.note       AS last_note,
       (SELECT count(*) FROM disposition x WHERE x.matter_key = m.matter_key)
                    AS decisions,
       (SELECT count(*) FROM enquiry e
         WHERE e.matter_key = m.matter_key AND e.status IN ('to_ask', 'asked'))
                    AS open_enquiries
  FROM matter m
  LEFT JOIN LATERAL (
        SELECT action, actor, at, note
          FROM disposition
         WHERE matter_key = m.matter_key
         ORDER BY at DESC, id DESC
         LIMIT 1
  ) d ON true;
