"""Writing a run down, and reading the register back.

One transaction per run: either the whole run is on record or none of it is. A
half-written run is worse than an unwritten one, because it looks like a
complete statement of what the filings said.

Everything the immutable tables hold is derived from the run. Everything the
durable tables hold is either derived from the run's *identity* (a matter) or
put there by a person (a disposition, an answer to an enquiry). No function
here edits a decision: a change of mind is another row.
"""
import gzip
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..ingest.pointer import as_number
from . import matters as M
from .connection import StoreError, connect, scrub

# What may be written to; anything else is a bug in a caller.
SUMMARY_KEYS = ("run_id", "recorded", "reconciliation", "reason")


def _num(value: Any) -> Optional[float]:
    n = as_number(value)
    return None if n is None else round(n, 4)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)[:4000]
    return str(value)[:4000]


def _js(value: Any) -> str:
    return json.dumps(value, default=str)


def record_run(result, foundation, filings, matrix, run_label: str = "",
               store_blobs: bool = True) -> Dict:
    """Persist one completed analysis. Returns a summary, never raises for a
    database that is merely absent — the caller shows the reason instead."""
    try:
        conn = connect()
    except StoreError as exc:
        return {"recorded": False, "reason": scrub(str(exc)), "run_id": None,
                "reconciliation": None}

    run_id = str(uuid.uuid4())
    manifest = result.manifest
    client = foundation.client
    label = run_label or datetime.now().strftime("%d %B %Y")

    try:
        with conn:
            with conn.cursor() as cur:
                _upsert_client(cur, client)
                _upsert_filings(cur, client.client_id, filings, store_blobs)
                _insert_run(cur, run_id, client.client_id, foundation, result, matrix)
                _insert_run_filings(cur, run_id, filings)
                _insert_cells(cur, run_id, matrix, filings)
                raised = _insert_findings(cur, run_id, client.client_id,
                                          result.findings)
                _insert_charts(cur, run_id, getattr(result, "charts", None))
                _insert_inferences(cur, run_id, getattr(result, "inferences", None),
                                   raised)
                _insert_programme(cur, run_id, getattr(result, "programme", None))
                reconciliation = _reconcile(cur, run_id, client.client_id, raised,
                                            label)
        conn.close()
        return {"recorded": True, "run_id": run_id, "reason": "",
                "reconciliation": reconciliation.as_dict()}
    except Exception as exc:                       # the run itself still stands
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        return {"recorded": False, "run_id": None, "reconciliation": None,
                "reason": scrub(f"{type(exc).__name__}: {exc}")}


# ------------------------------------------------------------------ writers
def _upsert_client(cur, client) -> None:
    cur.execute("""
        INSERT INTO client (client_id, name, pan, entity_type, declared, context)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (client_id) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, client.name),
            pan = COALESCE(EXCLUDED.pan, client.pan),
            entity_type = COALESCE(EXCLUDED.entity_type, client.entity_type),
            declared = EXCLUDED.declared,
            context = EXCLUDED.context,
            last_seen = now()
    """, (client.client_id, client.name, client.pan, client.entity_type,
          client.declared, _js(client.manifest())))


def _upsert_filings(cur, client_id: str, filings, store_blobs: bool) -> None:
    for f in filings.filings:
        if not f.digest:
            continue
        cur.execute("""
            INSERT INTO filing (digest, client_id, ay, kind, form, rel_path, bytes,
                                readable, detection)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (digest) DO UPDATE SET
                rel_path = EXCLUDED.rel_path,
                ay = COALESCE(EXCLUDED.ay, filing.ay),
                kind = COALESCE(EXCLUDED.kind, filing.kind),
                form = COALESCE(EXCLUDED.form, filing.form),
                last_seen = now()
        """, (f.digest, client_id, f.ay, f.kind, f.form, f.rel, f.bytes,
              f.readable, _js(f.as_dict())))
        if store_blobs and f.readable and f.doc is not None:
            cur.execute("""
                INSERT INTO filing_blob (digest, content) VALUES (%s, %s)
                ON CONFLICT (digest) DO NOTHING
            """, (f.digest, gzip.compress(
                json.dumps(f.doc, default=str).encode("utf-8"))))

    # Supersession is a relation between two filings and can only be written
    # once both rows exist.
    for f in filings.filings:
        if f.superseded_by:
            winner = filings.by_rel(f.superseded_by)
            if winner and winner.digest:
                cur.execute("UPDATE filing SET superseded_by = %s WHERE digest = %s",
                            (winner.digest, f.digest))


def _insert_run(cur, run_id: str, client_id: str, foundation, result, matrix) -> None:
    manifest = result.manifest
    pack = manifest.get("authority_pack", {})
    rules = manifest.get("rules", {})
    factmap = manifest.get("fact_map", {})
    ai = manifest.get("ai", {})
    counts = manifest.get("verdict_counts", {})
    cur.execute("""
        INSERT INTO run (run_id, client_id, started_at, run_mode, years,
                         rules_version, rules_digest, factmap_version, factmap_digest,
                         pack_version, pack_digest, doctrine_digest,
                         ai_available, model, ai_usd, findings_total, findings_raised,
                         exposure_inr, manifest, manifest_digest)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s)
    """, (
        run_id, client_id, datetime.now(timezone.utc),
        foundation.client.run_mode, matrix.years,
        str(rules.get("version", "")), str(rules.get("digest", "")),
        str(factmap.get("version", "")), str(factmap.get("digest", "")),
        str(pack.get("version", "")), str(pack.get("digest", "")),
        str(manifest.get("doctrine", {}).get("digest", "")),
        bool(ai.get("ai_available")), ai.get("model"),
        float((ai.get("cost", {}) or {}).get("total_usd", 0) or 0),
        len(result.findings), int(counts.get("FAIL", 0)),
        float(manifest.get("exposure_flagged_inr", 0) or 0),
        _js(manifest), str(manifest.get("manifest_digest", "")),
    ))


def _insert_run_filings(cur, run_id: str, filings) -> None:
    rows = [(run_id, f.digest, f.in_play) for f in filings.filings if f.digest]
    cur.executemany("""
        INSERT INTO run_filing (run_id, digest, in_play) VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
    """, rows)


def _insert_cells(cur, run_id: str, matrix, filings) -> None:
    by_name = {f.name: f.digest for f in filings.filings if f.digest}
    rows = []
    for (fact_id, ay), cell in matrix.cells.items():
        rows.append((run_id, fact_id, ay, _num(cell.value),
                     None if _num(cell.value) is not None else _text(cell.value),
                     cell.pointer or None, by_name.get(cell.file), cell.by))
    cur.executemany("""
        INSERT INTO fact_cell (run_id, fact_id, ay, value_num, value_text, pointer,
                               file_digest, resolved_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """, rows)


def _insert_findings(cur, run_id: str, client_id: str, findings) -> List[Dict]:
    """Every finding is written; only a FAIL becomes a matter. A rule that
    passed is part of the record — it is how you show that a question was
    asked — but it is not something anyone has to decide about."""
    raised: List[Dict] = []
    rows = []
    for f in findings:
        key = None
        if f.verdict == "FAIL":
            key = M.matter_key(client_id, f.rule_id, f.ay, f.evidence_cells)
            raised.append({
                "matter_key": key, "rule_id": f.rule_id, "ay": f.ay,
                "instance_id": f.instance_id, "theme": f.theme,
                "severity": f.severity, "question": f.question,
                "statement": f.statement, "observation": f.observation,
                "enquiry": f.enquiry, "vulnerability": f.vulnerability,
                "amount": f.amount,
                "fingerprint": M.fingerprint(f.evidence_cells),
            })
        rows.append((
            run_id, f.instance_id, key, f.rule_id, f.theme, f.ay, f.verdict,
            f.severity, round(float(f.confidence or 0), 3),
            (float(f.amount) if f.amount is not None else None),
            _text(f.statement), _text(f.observation), _text(f.enquiry),
            f.method, f.provenance_source, bool(f.needs_review),
            _text(f.na_reason), _text(f.skipped_reason),
            _js(f.evidence_cells or []), list(f.facts_used or []),
            _js(f.authority or []), _js(f.detail or {}),
        ))

    # The matters must exist before the findings can point at them.
    _upsert_matters(cur, run_id, client_id, raised)
    cur.executemany("""
        INSERT INTO finding (run_id, instance_id, matter_key, rule_id, theme, ay,
                             verdict, severity, confidence, amount_inr, statement,
                             observation, enquiry_text, method, provenance,
                             needs_review, na_reason, skipped_reason, evidence,
                             facts_used, authority, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s, %s::jsonb, %s::jsonb)
        ON CONFLICT (run_id, instance_id) DO NOTHING
    """, rows)
    return raised


def _upsert_matters(cur, run_id: str, client_id: str, raised: Sequence[Dict]) -> None:
    for r in raised:
        cur.execute("""
            INSERT INTO matter (matter_key, client_id, rule_id, instance_id, ay,
                                theme, severity, question, statement, observation,
                                enquiry_text, vulnerability, amount_inr, fingerprint,
                                first_run, last_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    %s, %s)
            ON CONFLICT (matter_key) DO UPDATE SET
                last_run = EXCLUDED.last_run,
                last_seen = now(),
                times_raised = matter.times_raised + 1,
                -- the wording may improve between runs; the decision must not be
                -- disturbed, so state and closed_reason are left alone here
                statement = EXCLUDED.statement,
                observation = EXCLUDED.observation,
                enquiry_text = EXCLUDED.enquiry_text,
                severity = EXCLUDED.severity,
                amount_inr = EXCLUDED.amount_inr
        """, (r["matter_key"], client_id, r["rule_id"], r["instance_id"], r["ay"],
              r["theme"], r["severity"], _text(r["question"]), _text(r["statement"]),
              _text(r["observation"]), _text(r["enquiry"]), _text(r["vulnerability"]),
              (float(r["amount"]) if r["amount"] is not None else None),
              _js(r["fingerprint"]), run_id, run_id))

        # An enquiry is created once per matter, and only where the rule gave
        # one. Re-running must not re-ask a question already answered.
        if r["enquiry"]:
            cur.execute("""
                INSERT INTO enquiry (matter_key, client_id, question)
                SELECT %s, %s, %s
                 WHERE NOT EXISTS (SELECT 1 FROM enquiry WHERE matter_key = %s)
            """, (r["matter_key"], client_id, _text(r["enquiry"]), r["matter_key"]))


def _insert_charts(cur, run_id: str, charts) -> None:
    """The points, not the picture. A rendering stored is a rendering that will
    one day disagree with the screen."""
    if not charts:
        return
    rows = []
    for chart in charts:
        for series, overlay in ((chart.series, False), (chart.overlay, True)):
            for s in series:
                for point in s.points:
                    if point.value is None:
                        continue
                    rows.append((run_id, chart.spec.id, s.fact, point.ay,
                                 round(float(point.value), 4), overlay))
    cur.executemany("""
        INSERT INTO chart_point (run_id, chart_id, fact_id, ay, value_num, overlay)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
    """, rows)


def _insert_inferences(cur, run_id: str, inferences, raised: Sequence[Dict]) -> None:
    """Kept with the brief it was drawn from. Without that, nobody can tell
    later whether the conclusion was reasonable on what was in front of it."""
    if not inferences:
        return
    by_rule = {r["rule_id"]: r["matter_key"] for r in raised}
    rows = []
    for i in inferences:
        d = i.as_dict(include_brief=True)
        rows.append((
            run_id, i.spec_id, i.topic, _text(i.title), _text(i.question),
            i.verdict, _text(i.headline), round(float(i.confidence or 0), 3),
            i.severity, bool(i.ran), _text(i.reason), i.model, bool(i.cached),
            int(i.years_reasoned_over or 0),
            _js(d["aberrations"]), _js(d["correlations"]), _js(d["hypotheses"]),
            _js(d["audit_focus"]), _text(i.observation), _text(i.vulnerability),
            _js(d["evidence_cells"]), list(i.charts or []), _js(i.notes or []),
            _js(d.get("brief") or {}), by_rule.get(i.spec_id)))
    cur.executemany("""
        INSERT INTO inference (run_id, spec_id, topic, title, question, verdict,
                               headline, confidence, severity, ran, reason, model,
                               cached, years_reasoned, aberrations, correlations,
                               hypotheses, audit_focus, observation, vulnerability,
                               evidence, charts, notes, brief, matter_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s, %s::jsonb,
                %s::jsonb, %s)
        ON CONFLICT (run_id, spec_id) DO NOTHING
    """, rows)


def _insert_programme(cur, run_id: str, programme) -> None:
    if programme is None:
        return
    rows = []
    for area in programme.areas:
        rows.append((run_id, area.spec.id, area.spec.label, area.priority,
                     area.score, list(area.years),
                     (float(area.amount) if area.amount else None),
                     _text(area.spec.why), _js(area.reasons),
                     _js(area.procedures), False, None))
    for omitted in programme.omitted:
        rows.append((run_id, omitted["id"], omitted["label"], "not_indicated", 0,
                     [], None, None, _js([]), _js([]), True,
                     _text(omitted["reason"])))
    cur.executemany("""
        INSERT INTO plan_area (run_id, area_id, label, priority, score, years,
                               amount_inr, why, reasons, procedures, omitted,
                               omitted_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        ON CONFLICT (run_id, area_id) DO NOTHING
    """, rows)


def _reconcile(cur, run_id: str, client_id: str, raised: Sequence[Dict],
               label: str) -> M.Reconciliation:
    """Decide what this run did to the register, and write it.

    The matters were upserted a moment ago, so "does this matter already exist"
    cannot be answered by its presence in the table. It is answered by
    `first_run`: a matter whose first run is this one is new, and everything
    else was on file before the run started.
    """
    cur.execute("""
        SELECT matter_key, rule_id, ay, state, first_run = %s AS created_now
          FROM matter WHERE client_id = %s
    """, (run_id, client_id))
    rows = [{"matter_key": k, "rule_id": r, "ay": a, "state": s,
             "created_now": bool(c)} for k, r, a, s, c in cur.fetchall()]
    prior = [m for m in rows if not m["created_now"]]
    decided = M.plan(prior, raised)

    for item in decided["superseded"]:
        cur.execute("""
            UPDATE matter SET state = 'superseded', closed_reason = %s,
                              superseded_by = %s, last_seen = now()
             WHERE matter_key = %s AND state <> 'superseded'
        """, (M.closing_reason("superseded", label), item["new"], item["old"]))
        cur.execute("""
            INSERT INTO disposition (matter_key, actor, action, note, run_id)
            VALUES (%s, 'system', 'note', %s, %s)
        """, (item["old"], f"superseded by {item['new']} — the run of {label} raised "
                           f"the same rule and year on different figures", run_id))
    for key in decided["resolved"]:
        cur.execute("""
            UPDATE matter SET state = 'resolved', closed_reason = %s, last_seen = now()
             WHERE matter_key = %s AND state <> 'resolved'
        """, (M.closing_reason("resolved", label), key))
        cur.execute("""
            INSERT INTO disposition (matter_key, actor, action, note, run_id)
            VALUES (%s, 'system', 'note', %s, %s)
        """, (key, f"no longer raised as at the run of {label}", run_id))
    for key in decided["reopened"]:
        cur.execute("""
            UPDATE matter SET state = 'open', closed_reason = NULL,
                              superseded_by = NULL, last_seen = now()
             WHERE matter_key = %s
        """, (key,))
        cur.execute("""
            INSERT INTO disposition (matter_key, actor, action, note, run_id)
            VALUES (%s, 'system', 'reopen', %s, %s)
        """, (key, f"raised again by the run of {label} on the same figures", run_id))

    return M.Reconciliation(
        raised_new=decided["new"], raised_again=decided["again"],
        reopened=decided["reopened"], superseded=decided["superseded"],
        resolved=decided["resolved"])


# ------------------------------------------------------------------ readers
def _rows(cur) -> List[Dict]:
    names = [c.name for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def register(client_id: str, states: Optional[Sequence[str]] = None) -> Dict:
    """The durable register: every matter with its latest decision."""
    try:
        conn = connect()
    except StoreError as exc:
        return {"available": False, "reason": scrub(str(exc)), "matters": [],
                "counts": {}}
    try:
        with conn:
            with conn.cursor() as cur:
                if states:
                    cur.execute("""
                        SELECT * FROM matter_register
                         WHERE client_id = %s AND state = ANY(%s)
                         ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium'
                                  THEN 1 ELSE 2 END, ay DESC NULLS LAST, rule_id
                    """, (client_id, list(states)))
                else:
                    cur.execute("""
                        SELECT * FROM matter_register
                         WHERE client_id = %s
                         ORDER BY CASE state WHEN 'open' THEN 0 WHEN 'accepted' THEN 1
                                  WHEN 'amended' THEN 2 WHEN 'rejected' THEN 3
                                  WHEN 'superseded' THEN 4 ELSE 5 END,
                                  CASE severity WHEN 'high' THEN 0 WHEN 'medium'
                                  THEN 1 ELSE 2 END, ay DESC NULLS LAST, rule_id
                    """, (client_id,))
                rows = _rows(cur)
                cur.execute("""
                    SELECT state, count(*) FROM matter WHERE client_id = %s
                     GROUP BY state
                """, (client_id,))
                counts = {state: n for state, n in cur.fetchall()}
                cur.execute("""
                    SELECT run_id, finished_at, years, findings_raised, exposure_inr,
                           ai_usd, rules_version, model, manifest_digest
                      FROM run WHERE client_id = %s
                     ORDER BY finished_at DESC LIMIT 20
                """, (client_id,))
                runs = _rows(cur)
                cur.execute("""
                    SELECT e.*, m.rule_id, m.ay, m.severity
                      FROM enquiry e JOIN matter m ON m.matter_key = e.matter_key
                     WHERE e.client_id = %s AND e.status IN ('to_ask', 'asked')
                       AND m.state NOT IN ('resolved', 'superseded', 'rejected')
                     ORDER BY CASE m.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                              ELSE 2 END, e.created_at
                """, (client_id,))
                enquiries = _rows(cur)
        conn.close()
        return {"available": True, "reason": "", "matters": rows, "counts": counts,
                "runs": runs, "enquiries": enquiries}
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        return {"available": False, "reason": scrub(f"{type(exc).__name__}: {exc}"),
                "matters": [], "counts": {}}


def matter_detail(matter_key: str) -> Dict:
    try:
        conn = connect()
    except StoreError as exc:
        return {"available": False, "reason": scrub(str(exc))}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM matter_register WHERE matter_key = %s",
                            (matter_key,))
                found = _rows(cur)
                if not found:
                    return {"available": True, "matter": None,
                            "reason": "no such matter"}
                cur.execute("""
                    SELECT f.run_id, r.finished_at, f.verdict, f.statement,
                           f.amount_inr, f.evidence, f.confidence
                      FROM finding f JOIN run r ON r.run_id = f.run_id
                     WHERE f.matter_key = %s ORDER BY r.finished_at DESC
                """, (matter_key,))
                history = _rows(cur)
                cur.execute("""
                    SELECT at, actor, action, note, amount_inr FROM disposition
                     WHERE matter_key = %s ORDER BY at DESC, id DESC
                """, (matter_key,))
                decisions = _rows(cur)
                cur.execute("SELECT * FROM enquiry WHERE matter_key = %s ORDER BY id",
                            (matter_key,))
                enquiries = _rows(cur)
        conn.close()
        return {"available": True, "matter": found[0], "history": history,
                "decisions": decisions, "enquiries": enquiries, "reason": ""}
    except Exception as exc:
        return {"available": False, "reason": scrub(f"{type(exc).__name__}: {exc}")}


def add_disposition(matter_key: str, action: str, actor: str, note: str = "",
                    amount: Optional[float] = None,
                    run_id: Optional[str] = None) -> Dict:
    """Record a decision. Appends, and moves the matter's state to match."""
    if action not in M.ACTIONS:
        return {"ok": False, "reason": f"unknown action {action!r}; expected one of "
                                       f"{', '.join(M.ACTIONS)}"}
    state = {"accept": "accepted", "reject": "rejected", "amend": "amended",
             "reopen": "open", "note": None}[action]
    try:
        conn = connect()
    except StoreError as exc:
        return {"ok": False, "reason": scrub(str(exc))}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM matter WHERE matter_key = %s",
                            (matter_key,))
                row = cur.fetchone()
                if row is None:
                    return {"ok": False, "reason": "no such matter"}
                cur.execute("""
                    INSERT INTO disposition (matter_key, actor, action, note,
                                             amount_inr, run_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (matter_key, actor or "unnamed", action, _text(note) or None,
                      amount, run_id))
                if state:
                    cur.execute("""
                        UPDATE matter SET state = %s, closed_reason = NULL,
                                          last_seen = now()
                         WHERE matter_key = %s
                    """, (state, matter_key))
                if action == "amend" and amount is not None:
                    cur.execute("UPDATE matter SET amount_inr = %s WHERE matter_key = %s",
                                (amount, matter_key))
        conn.close()
        return {"ok": True, "state": state or row[0]}
    except Exception as exc:
        return {"ok": False, "reason": scrub(f"{type(exc).__name__}: {exc}")}


def update_enquiry(enquiry_id: int, status: str, actor: str = "",
                   answer: str = "", document_ref: str = "",
                   channel: str = "") -> Dict:
    if status not in ("to_ask", "asked", "answered", "withdrawn"):
        return {"ok": False, "reason": f"unknown status {status!r}"}
    try:
        conn = connect()
    except StoreError as exc:
        return {"ok": False, "reason": scrub(str(exc))}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE enquiry SET
                        status = %s,
                        asked_at = CASE WHEN %s = 'asked' AND asked_at IS NULL
                                        THEN now() ELSE asked_at END,
                        asked_by = COALESCE(NULLIF(%s, ''), asked_by),
                        channel = COALESCE(NULLIF(%s, ''), channel),
                        answer = COALESCE(NULLIF(%s, ''), answer),
                        answered_at = CASE WHEN %s = 'answered' THEN now()
                                           ELSE answered_at END,
                        document_ref = COALESCE(NULLIF(%s, ''), document_ref)
                     WHERE id = %s
                """, (status, status, actor, channel, answer, status, document_ref,
                      enquiry_id))
                touched = cur.rowcount
        conn.close()
        return {"ok": bool(touched), "reason": "" if touched else "no such enquiry"}
    except Exception as exc:
        return {"ok": False, "reason": scrub(f"{type(exc).__name__}: {exc}")}


def client_history(limit: int = 50) -> Dict:
    """Every client the database has seen, with its last run. Cheap enough to
    put on the client list, and the only place that shows a client whose folder
    has since been emptied."""
    try:
        conn = connect()
    except StoreError as exc:
        return {"available": False, "reason": str(exc), "clients": []}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.client_id, c.name, c.pan, c.entity_type,
                           max(r.finished_at) AS last_run,
                           count(DISTINCT r.run_id) AS runs,
                           (SELECT count(*) FROM matter m
                             WHERE m.client_id = c.client_id AND m.state = 'open')
                             AS open_matters
                      FROM client c LEFT JOIN run r ON r.client_id = c.client_id
                     GROUP BY c.client_id, c.name, c.pan, c.entity_type
                     ORDER BY last_run DESC NULLS LAST LIMIT %s
                """, (limit,))
                rows = _rows(cur)
        conn.close()
        return {"available": True, "clients": rows, "reason": ""}
    except Exception as exc:
        return {"available": False, "reason": scrub(f"{type(exc).__name__}: {exc}"),
                "clients": []}
