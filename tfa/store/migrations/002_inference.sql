-- ---------------------------------------------------------------------------
-- The inference layer and the audit programme, persisted.
--
-- All three tables here are immutable, like the run they belong to. They exist
-- so that a conclusion drawn last quarter can be re-read with the brief it was
-- drawn from and the chart the reader was looking at — which is the only way
-- to tell, later, whether an inference was reasonable on what was known then.
--
-- A reportable inference also enters the `matter` register through the
-- ordinary finding path, so a partner accepts or rejects it exactly as they do
-- an arithmetic finding. These tables carry the working, not the decision.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chart_point (
    run_id      uuid    NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    chart_id    text    NOT NULL,
    fact_id     text    NOT NULL,
    ay          text    NOT NULL,
    value_num   numeric(20,4),
    overlay     boolean NOT NULL DEFAULT false,
    PRIMARY KEY (run_id, chart_id, fact_id, ay, overlay)
);

COMMENT ON TABLE chart_point IS
  'The points a chart was drawn from. The picture is not stored: it is a '
  'function of these, of the catalogue and of the house style, and storing a '
  'rendering would let the record and the screen drift apart.';

CREATE TABLE IF NOT EXISTS inference (
    run_id          uuid        NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    spec_id         text        NOT NULL,
    topic           text,
    title           text,
    question        text,
    verdict         text        NOT NULL,
        -- REPORTABLE | EXPLAINED | NOTHING_ARISING | INSUFFICIENT_EVIDENCE
    headline        text,
    confidence      numeric(4,3),
    severity        text,
    ran             boolean     NOT NULL DEFAULT true,
    reason          text,
    model           text,
    cached          boolean     NOT NULL DEFAULT false,
    years_reasoned  integer     NOT NULL DEFAULT 0,
    aberrations     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    correlations    jsonb       NOT NULL DEFAULT '[]'::jsonb,
    hypotheses      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    audit_focus     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    observation     text,
    vulnerability   text,
    evidence        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    charts          text[]      NOT NULL DEFAULT '{}',
    notes           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- the brief exactly as it was put to the model: without it, an inference
    -- cannot be judged on what it actually had in front of it
    brief           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    matter_key      text        REFERENCES matter(matter_key),
    PRIMARY KEY (run_id, spec_id)
);

CREATE INDEX IF NOT EXISTS inference_verdict ON inference (run_id, verdict);
CREATE INDEX IF NOT EXISTS inference_spec ON inference (spec_id);

CREATE TABLE IF NOT EXISTS plan_area (
    run_id      uuid          NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    area_id     text          NOT NULL,
    label       text,
    priority    text          NOT NULL,
    score       integer       NOT NULL DEFAULT 0,
    years       text[]        NOT NULL DEFAULT '{}',
    amount_inr  numeric(18,2),
    why         text,
    reasons     jsonb         NOT NULL DEFAULT '[]'::jsonb,
    procedures  jsonb         NOT NULL DEFAULT '[]'::jsonb,
    omitted     boolean       NOT NULL DEFAULT false,
    omitted_reason text,
    PRIMARY KEY (run_id, area_id)
);

CREATE INDEX IF NOT EXISTS plan_area_priority ON plan_area (run_id, priority);

-- The programme as it stood at each run, for one client, newest first. Used to
-- answer "what did we plan to look at last year, and did the reason change?"
CREATE OR REPLACE VIEW programme_history AS
SELECT r.client_id, r.run_id, r.finished_at, p.area_id, p.label, p.priority,
       p.score, p.amount_inr, jsonb_array_length(p.procedures) AS procedures
  FROM plan_area p
  JOIN run r ON r.run_id = p.run_id
 WHERE NOT p.omitted
 ORDER BY r.finished_at DESC, p.score DESC;
