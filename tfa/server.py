"""Local analysis service.

Serves the screen and streams a real run over server-sent events. Bound to
loopback only: the folder holds a client's tax returns, and the rule catalogue
is the firm's method. Neither should be reachable from the network.

    python3 -m tfa.server                        # http://127.0.0.1:8814
    python3 -m tfa.server --client SANKARSAI-CONSUMER-PRODUCTS --port 9000
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import traceback
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tfa.ai.chat import ask                                      # noqa: E402
from tfa.analysis.trend import build_trends                      # noqa: E402
from tfa.config import refresh                                   # noqa: E402
from tfa.facts.extract import load_fact_map                      # noqa: E402
from tfa.facts.matrix import build_matrix                        # noqa: E402
from tfa.foundation.preflight import preflight                   # noqa: E402
from tfa.ingest.loader import load_filings                       # noqa: E402
from tfa.pipeline import analyse_streaming, make_client          # noqa: E402
from tfa.rules.registry import default_rules_path, load_rules    # noqa: E402
from tfa.store.connection import redacted as redacted_db          # noqa: E402
from tfa.store import (                                          # noqa: E402
    add_disposition, client_history, health as store_health, matter_detail,
    migrate as store_migrate, register as store_register, update_enquiry,
)

UI = Path(__file__).resolve().parent / "report" / "ui"
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json"}

CONFIG = {"client": "ACME-PVT-LTD"}
MAX_BODY = 64 * 1024
HEARTBEAT_SECONDS = float(os.environ.get("TFA_HEARTBEAT_SECONDS") or 10)


def clients_available():
    """Every client folder on disk, and whether a context has been declared."""
    settings = refresh()
    out = []
    root = settings.filings_dir
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            files = [f for f in folder.rglob("*") if f.is_file()
                     and not f.name.startswith(".")]
            out.append({
                "client_id": folder.name,
                "files": len(files),
                "declared": (ROOT / "clients" / f"{folder.name}.yaml").is_file(),
            })
    return out


class Session:
    """The last understanding of the folder, so a question does not have to
    re-read it — but keyed on the folder's contents, so a changed or added file
    rebuilds rather than answering from stale figures."""

    def __init__(self):
        self.signature = None
        self.client_id = None
        self.matrix = None
        self.trends = None
        self.findings = []
        self.report = None
        self.rebuilt = False
        self.qa = []

    def _signature(self, root: Path):
        if not root.exists():
            return ()
        return tuple(sorted(
            (str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")))

    def load(self, client_id: str):
        foundation = preflight(client_id)
        signature = self._signature(foundation.filings_root)
        if (signature != self.signature or self.matrix is None
                or client_id != self.client_id):
            fact_map = load_fact_map()
            filings = load_filings(foundation.filings_root)
            self.matrix = build_matrix(filings, fact_map)
            foundation.settle(self.matrix.years)
            self.trends = build_trends(self.matrix, float(
                foundation.doctrine.param("yoy_material_change", 0.30)))
            self.signature = signature
            self.client_id = client_id
            self.rebuilt = True
        else:
            self.rebuilt = False
        return self.matrix, self.trends


SESSION = Session()


class Ledger:
    """What this session has cost, and what it is charged at.

    The portal charge is set equal to the AI charge, so the client pays twice
    what the model cost and the two halves are shown separately rather than
    rolled into one opaque number.
    """
    PORTAL_MULTIPLIER = 1.0

    def __init__(self):
        self.runs = []

    def record(self, ai_usd, findings, billed, cached, kind="analysis"):
        entry = {"index": len(self.runs) + 1, "kind": kind,
                 "at": datetime.now().strftime("%H:%M:%S"),
                 "ai_usd": round(float(ai_usd or 0.0), 6), "findings": findings,
                 "billed_calls": billed, "cached_calls": cached}
        self.runs.append(entry)
        return entry

    @staticmethod
    def charge(ai_usd):
        portal = round(ai_usd * Ledger.PORTAL_MULTIPLIER, 6)
        return {"ai_usd": round(ai_usd, 6), "portal_usd": portal,
                "total_usd": round(ai_usd + portal, 6)}

    def session(self):
        total = sum(r["ai_usd"] for r in self.runs)
        out = self.charge(total)
        out["runs"] = len(self.runs)
        out["billed_calls"] = sum(r["billed_calls"] for r in self.runs)
        out["cached_calls"] = sum(r["cached_calls"] for r in self.runs)
        out["history"] = self.runs[-12:]
        return out


LEDGER = Ledger()


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


class Handler(BaseHTTPRequestHandler):
    server_version = "TFA"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if args and "/api/run" in str(args[0]):
            sys.stderr.write("  analysis requested\n")

    # ------------------------------------------------------------------ util
    def _send(self, code, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        path = (UI / name).resolve()
        if UI not in path.parents or not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        body = path.read_bytes()
        if path.name == "index.html":
            body = self._stamp(body)
        self._send(200, body, MIME.get(path.suffix, "application/octet-stream"))

    @staticmethod
    def _stamp(html: bytes) -> bytes:
        """Stamp the asset links with a content hash: a tab left open across an
        edit otherwise keeps running yesterday's JavaScript, and the symptom
        looks like a broken run rather than a stale page."""
        text = html.decode()
        for asset in ("app.css", "app.js"):
            target = UI / asset
            if target.is_file():
                version = hashlib.sha256(target.read_bytes()).hexdigest()[:10]
                text = text.replace(f'"/{asset}"', f'"/{asset}?v={version}"')
        return text.encode()

    # ------------------------------------------------------------------- SSE
    def _stream(self) -> None:
        """Server-sent events, chunk-framed.

        Every event is written as its own chunk and flushed: a body with neither
        Content-Length nor Transfer-Encoding is malformed, and a browser's
        EventSource waits for framing that never arrives, which looks exactly
        like a run that stalled halfway.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        lock = threading.Lock()
        alive = threading.Event()

        def raw(payload: bytes) -> None:
            with lock:
                self.wfile.write(b"%X\r\n" % len(payload) + payload + b"\r\n")
                self.wfile.flush()

        def emit(event: dict, name: str = "message") -> None:
            raw(f"event: {name}\ndata: {json.dumps(event, default=_json_default)}\n\n"
                .encode())

        def heartbeat() -> None:
            # A single uncached model call is a minute of silence on this stream.
            # Without a heartbeat that is indistinguishable from a dead
            # connection.
            while alive.wait(HEARTBEAT_SECONDS) is False:
                try:
                    raw(b": still working\n\n")
                except (BrokenPipeError, OSError):
                    return

        def terminate() -> None:
            alive.set()
            try:
                with lock:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, OSError):
                pass

        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            for event in analyse_streaming(CONFIG["client"], client=make_client()):
                if event.get("stage") == "report":
                    SESSION.report = dict(event["detail"])
                if event.get("stage") == "assess" or event.get("stage") == "anomaly":
                    SESSION.findings.append(event)
                if event.get("stage") == "start":
                    SESSION.findings = []
                if event.get("stage") == "done":
                    d = event["detail"]
                    LEDGER.record(d.get("ai_usd", 0.0), d.get("findings", 0),
                                  d.get("billed_calls", 0), d.get("cached_calls", 0))
                    d["run_charge"] = Ledger.charge(d.get("ai_usd", 0.0))
                    d["session_charge"] = LEDGER.session()
                emit(event)
            emit({"stage": "closed"}, "end")
            terminate()
        except (BrokenPipeError, ConnectionResetError):
            alive.set()
        except Exception as exc:
            traceback.print_exc()
            try:
                emit({"stage": "error", "detail": f"{type(exc).__name__}: {exc}"})
                emit({"stage": "closed"}, "end")
                terminate()
            except (BrokenPipeError, OSError):
                alive.set()

    # ------------------------------------------------------- canonical rules
    def _rules(self) -> None:
        """The catalogue as the engine actually loaded it, served from the
        registry rather than transcribed, so the tab cannot drift from what
        runs."""
        try:
            foundation = preflight(CONFIG["client"])
            fact_map = load_fact_map()
            rule_set = load_rules(default_rules_path(), foundation.pack, fact_map)
            in_force = foundation.pack.in_force([])          # every provision

            rules = []
            for r in rule_set.rules:
                authority = []
                for pid in r.authority:
                    p = foundation.pack.resolve(pid)
                    authority.append({
                        "id": p.id, "citation": p.citation, "title": p.title,
                        "summary": p.summary,
                        "instrument": foundation.pack.instruments.get(p.instrument,
                                                                      p.instrument),
                        "verified": p.verified,
                        "effective_from": (p.effective_from.isoformat()
                                           if p.effective_from else None),
                    })
                facts = sorted({str(v) for k, v in r.params.items()
                                if fact_map.by_id(str(v))})
                rules.append({
                    "id": r.id, "theme": r.theme,
                    "theme_label": rule_set.theme_label(r.theme),
                    "question": r.question, "method": r.method, "rule": r.rule,
                    "severity": r.severity, "for_each": r.for_each,
                    "params": r.params, "facts": facts,
                    "requires": r.requires, "applicability": r.applicability,
                    "rubric": r.rubric, "scope": r.scope,
                    "vulnerability": r.vulnerability, "enquiry": r.enquiry,
                    "authority": authority,
                })
            body = {
                "version": rule_set.version, "digest": rule_set.digest,
                "path": str(rule_set.path.relative_to(ROOT)),
                "pack": f"{foundation.pack.id} v{foundation.pack.version}",
                "pack_status": foundation.pack.verification_status,
                "provisions": len(foundation.pack.provisions),
                "provisions_unverified": foundation.pack.unverified_count,
                "fact_map": {
                    "version": fact_map.version,
                    "path": str(fact_map.path.relative_to(ROOT)),
                    "digest": fact_map.digest,
                    "facts": len(fact_map.specs),
                    "derived": len(fact_map.for_scope("derived")),
                    "groups": [{"id": g, "label": label}
                               for g, label in fact_map.groups.items()],
                    "specs": [{
                        "id": s.id, "label": s.label, "group": s.group,
                        "scope": s.scope, "kind": s.kind, "clause": s.clause,
                        "trend": s.trend, "paths": s.paths, "keys": s.keys,
                        "aggregate": s.aggregate, "expr": s.expr, "note": s.note,
                    } for s in fact_map.specs],
                },
                "doctrine": {
                    "version": foundation.doctrine.version,
                    "articles": [{"id": a.id, "title": a.title, "text": a.text}
                                 for a in foundation.doctrine.articles],
                    "parameters": foundation.doctrine.parameters,
                    "prohibited": foundation.doctrine.prohibited,
                },
                "themes": [{"id": t, "label": label}
                           for t, label in rule_set.themes.items()],
                "rules": rules,
                "counts": rule_set.counts,
                "implementations": sorted({r.rule for r in rule_set.rules if r.rule}),
            }
        except Exception as exc:
            traceback.print_exc()
            body = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(200, json.dumps(body, default=_json_default).encode(),
                   "application/json")

    # ------------------------------------------------------------ attendance
    def _attendance(self) -> None:
        """What is in the folder, before anything is analysed. Cheap enough to
        serve on page load, so the screen can open on the register."""
        try:
            foundation = preflight(CONFIG["client"])
            filings = load_filings(foundation.filings_root)
            body = {
                "client": {"client_id": foundation.client.client_id,
                           "name": foundation.client.display_name,
                           "pan": foundation.client.pan,
                           "entity_type": foundation.client.entity_type,
                           "declared": foundation.client.declared,
                           "source": foundation.client.source,
                           "notes": foundation.client.notes},
                "folder": str(foundation.filings_root.relative_to(ROOT)),
                "attendance": filings.attendance.as_dict(),
                "filings": [f.as_dict() for f in filings.filings],
                "warnings": foundation.warnings,
                "clients": clients_available(),
            }
        except Exception as exc:
            traceback.print_exc()
            body = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(200, json.dumps(body, default=_json_default).encode(),
                   "application/json")

    # -------------------------------------------------------------- register
    def _register(self) -> None:
        """The durable register: matters, their decisions, the queued enquiries
        and the run history. Served whether or not a run has happened in this
        session — that is the point of persisting it."""
        body = store_register(CONFIG["client"])
        body["store"] = store_health().as_dict()
        body["client"] = CONFIG["client"]
        self._send(200, json.dumps(body, default=_json_default).encode(),
                   "application/json")

    def _matter(self, key: str) -> None:
        self._send(200, json.dumps(matter_detail(key), default=_json_default).encode(),
                   "application/json")

    def _decide(self) -> None:
        """Record a decision about a matter. Appends; never edits."""
        payload = self._body()
        if payload is None:
            return
        result = add_disposition(
            matter_key=str(payload.get("matter_key", "")),
            action=str(payload.get("action", "")),
            actor=str(payload.get("actor", "") or "unnamed"),
            note=str(payload.get("note", "")),
            amount=payload.get("amount"),
            run_id=(SESSION.report or {}).get("manifest", {})
                   .get("persistence", {}).get("run_id"))
        self._send(200 if result.get("ok") else 400,
                   json.dumps(result, default=_json_default).encode(),
                   "application/json")

    def _enquiry(self) -> None:
        payload = self._body()
        if payload is None:
            return
        try:
            enquiry_id = int(payload.get("id"))
        except (TypeError, ValueError):
            self._send(400, b'{"ok":false,"reason":"no enquiry id"}',
                       "application/json")
            return
        result = update_enquiry(
            enquiry_id=enquiry_id, status=str(payload.get("status", "")),
            actor=str(payload.get("actor", "")),
            answer=str(payload.get("answer", "")),
            document_ref=str(payload.get("document_ref", "")),
            channel=str(payload.get("channel", "")))
        self._send(200 if result.get("ok") else 400,
                   json.dumps(result, default=_json_default).encode(),
                   "application/json")

    def _body(self):
        try:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, b'{"ok":false,"reason":"malformed request"}',
                       "application/json")
            return None

    # ------------------------------------------------------------------ chat
    def _chat(self) -> None:
        try:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, b'{"ok":false,"reason":"malformed request"}',
                       "application/json")
            return

        question = str(payload.get("question", ""))
        try:
            matrix, trends = SESSION.load(CONFIG["client"])
            client = make_client()
            answer = ask(question, matrix, trends,
                         [SESSION_finding(e) for e in SESSION.findings], client)
            body = answer.as_dict()
            budget = client.budget.manifest()
            body["budget"] = budget
            spend = budget.get("cost", {})
            LEDGER.record(spend.get("total_usd", 0.0), 0,
                          spend.get("billed_calls", 0), spend.get("cached_calls", 0),
                          kind="question")
            body["run_charge"] = Ledger.charge(spend.get("total_usd", 0.0))
            body["session_charge"] = LEDGER.session()
            body["rebuilt"] = SESSION.rebuilt
            if answer.ok:
                SESSION.qa.append({"question": question, "answer": answer.answer,
                                   "blocks": answer.blocks,
                                   "references": answer.references,
                                   "at": datetime.now().strftime("%H:%M")})
        except Exception as exc:
            traceback.print_exc()
            body = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        self._send(200, json.dumps(body, default=_json_default).encode(),
                   "application/json")

    # ----------------------------------------------------------------- routes
    def do_POST(self) -> None:                                   # noqa: N802
        route = self.path.split("?")[0]
        if route == "/api/chat":
            self._chat()
        elif route == "/api/disposition":
            self._decide()
        elif route == "/api/enquiry":
            self._enquiry()
        elif route == "/api/client":
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                wanted = str(payload.get("client_id", "")).strip()
                names = {c["client_id"] for c in clients_available()}
                if wanted not in names:
                    self._send(404, json.dumps(
                        {"ok": False, "reason": f"no folder for {wanted!r}"}).encode(),
                        "application/json")
                    return
                CONFIG["client"] = wanted
                SESSION.__init__()
                self._send(200, json.dumps({"ok": True, "client_id": wanted}).encode(),
                           "application/json")
            except (ValueError, json.JSONDecodeError):
                self._send(400, b'{"ok":false}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_GET(self) -> None:                                    # noqa: N802
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            self._static("index.html")
        elif route == "/api/run":
            self._stream()
        elif route == "/api/rules":
            self._rules()
        elif route == "/api/attendance":
            self._attendance()
        elif route == "/api/register":
            self._register()
        elif route == "/api/matter":
            from urllib.parse import parse_qs, urlparse
            key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
            self._matter(key)
        elif route == "/api/status":
            settings = refresh()
            self._send(200, json.dumps({
                "client": CONFIG["client"],
                "clients": clients_available(),
                "ai_available": settings.ai_available,
                "ai_reason": settings.unavailable_reason(),
                "model": settings.model if settings.ai_available else None,
                "key": settings.redacted_key, "key_source": settings.key_source,
                "run_mode": settings.run_mode,
                "max_model_calls": settings.max_model_calls,
                "session_charge": LEDGER.session(),
                "report_ready": SESSION.report is not None,
                "questions_asked": len(SESSION.qa),
                "store": store_health().as_dict(),
            }).encode(), "application/json")
        elif route.lstrip("/") in {"app.css", "app.js", "_charts_preview.html"}:
            self._static(route.lstrip("/"))
        else:
            self._send(404, b"not found", "text/plain")


class _F:
    """The shape `ai.chat` expects of a finding, built from a stream event."""
    def __init__(self, event):
        d = event
        self.rule_id = d.get("rule_id", "")
        self.instance_id = d.get("instance_id", "")
        self.verdict = d.get("verdict", "")
        self.statement = d.get("statement", "")
        self.facts_used = d.get("facts_used", []) or []


def SESSION_finding(event):                                      # noqa: N802
    return _F(event)


def main() -> int:
    ap = argparse.ArgumentParser(prog="tfa.server")
    ap.add_argument("--port", type=int, default=8814)
    ap.add_argument("--client", default=None,
                    help="client folder to open with; defaults to the first one found")
    args = ap.parse_args()

    # Line-buffer the console: redirected to a file, Python blocks stdout in
    # 8KB chunks and the startup banner — which says whether anything is being
    # recorded — sits invisible in a buffer until the process ends.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    available = clients_available()
    if args.client:
        CONFIG["client"] = args.client
    elif available:
        CONFIG["client"] = available[0]["client_id"]

    settings = refresh()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"TFA analysis service  →  http://127.0.0.1:{args.port}")
    print(f"client                →  {CONFIG['client']}")
    print(f"filings               →  {settings.filings_dir}")
    if not available:
        print("  (no client folders yet: create filings/<CLIENT-CODE>/ and drop the "
              "JSON files in)")
    else:
        print("clients on disk       →  " + ", ".join(
            f"{c['client_id']} ({c['files']} files"
            + ("" if c["declared"] else ", no context declared") + ")"
            for c in available))
    state = store_health()
    if state.enabled:
        try:
            applied = store_migrate(verbose=False)
        except Exception as exc:
            applied = []
            print(f"register              →  schema not applied: {exc}")
        print(f"register              →  {state.server} at {redacted_db()}"
              + (f"  ({len(applied)} migration(s) applied)" if applied else ""))
    else:
        print("register              →  not recording: " + state.reason)
        print("                         start one with  "
              "python3 -m tfa.store.local start")
    print("model judgment        →  " + (
        f"{settings.model}  (key {settings.redacted_key} from {settings.key_source})"
        if settings.ai_available else
        "unavailable — put your key in the 'env' file at the project root, then press "
        "Run (the file is re-read on every run)"))
    print("press ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
