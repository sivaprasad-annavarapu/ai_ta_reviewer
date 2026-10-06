#!/usr/bin/env python3
"""
AWF — the canonical workflow menu, identical in every app repo (AWF kit v2).

Everything app-specific lives in deploy/app.conf; this file is never edited
per repo. Source of truth: ~/GRCKEY/awf-kit/awf.py — fix it there, re-copy.
Rules: ~/GRCKEY/AWF_CONVENTIONS.md.  (AnyGRC keeps its own, older menu.)

    python3 awf.py        # menu
    python3 awf.py 12     # run one option directly

The canonical list (same number, same meaning, everywhere):
     1 Restart servers (local, same ports as AWS)   10 Commit & push
     2 Stop servers                                 11 Pull
     3 Status (local + AWS)                         12 DEPLOY to AWS
     4 Tail logs                                    13 AWS status + logs
     5 First-time setup                             14 git log
     6 Check environment                            15 Explore server DB (read-only)
     7 Recover (wipe venv/node_modules, restart)    16 Migration status (server DB)
     8 DB tunnel to the server database             17 Backend tests
     9 Git status                                   18 E2E tests
    19+ app extras, declared in deploy/app.conf (EXTRA_19_LABEL / EXTRA_19_CMD …)
"""
import datetime
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)


def _conf():
    out = {}
    for raw in (ROOT / "deploy" / "app.conf").read_text().splitlines():
        line = raw.split(" #")[0].strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


CONF = _conf()
APP = CONF["APP"]
GRCKEY = Path.home() / "GRCKEY"
KIT = GRCKEY / "awf-kit"
ENV_FILE = Path(os.getenv("AWF_ENV_FILE", GRCKEY / "envs" / f"{APP}.env"))
SSH_KEY = os.getenv("AWF_SSH_KEY", str(GRCKEY / "anyaudit_ai_dev_pem_key 2.pem"))
SSH_HOST = os.getenv("AWF_SSH_HOST", "65.0.35.71")
SSH_TARGET = f"{os.getenv('AWF_SSH_USER', 'ubuntu')}@{SSH_HOST}"
PUBLIC_PORT = CONF["PUBLIC_PORT"]
LOCAL_PORT = CONF.get("LOCAL_PORT") or PUBLIC_PORT
PUBLIC_URL = f"https://{SSH_HOST}:{PUBLIC_PORT}"
VENV = ROOT / CONF.get("LOCAL_VENV", ".venv")
VENV_PY = VENV / "bin" / "python"
REQS = CONF.get("REQUIREMENTS", "requirements.txt")
HAS_DB = CONF.get("DB") == "1"
HAS_FRONTEND = (ROOT / "frontend" / "package.json").is_file()
PYTHON = "python3.12"   # matches the server (Ubuntu 24.04)

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
CYAN, GREEN, YELLOW, RED = "\033[36m", "\033[32m", "\033[33m", "\033[31m"


def run(cmd):
    print(f"{DIM}$ {cmd}{RESET}")
    return subprocess.call(cmd, shell=True, executable="/bin/bash")


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    return input(f"{CYAN}{prompt}{suffix}: {RESET}").strip() or default


def remote(cmd):
    return run(f"ssh -i '{SSH_KEY}' -o ConnectTimeout=15 {SSH_TARGET} {cmd}")


def _env_keys():
    keys = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text().splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                k, _, v = raw.partition("=")
                keys[k.strip()] = v.strip()
    return keys


def _db_name():
    prefix = CONF.get("DB_PREFIX") or APP.upper()
    return _env_keys().get(f"{prefix}_DB_NAME", APP)


def _is_git():
    return subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True).returncode == 0


def _branch():
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _dirty():
    return bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())


def _need_git():
    if _is_git():
        return True
    print(f"{YELLOW}{ROOT.name} is not a git repository yet — git options wait until it is "
          f"connected. Deploy (12) does not need git.{RESET}")
    return False


# ── 1–4 · Servers (local, on the AWS port numbers) ──────────────────────────

def restart_servers():
    """1 · Restart local servers on the same ports as AWS (stop + start)."""
    return run("./start-all.sh")


def stop_servers():
    """2 · Stop local servers."""
    return run("./stop-all.sh")


def status():
    """3 · Local and AWS status."""
    lp = subprocess.run(f"curl -s -o /dev/null -m 3 http://127.0.0.1:{LOCAL_PORT}/", shell=True).returncode == 0
    print(f"  local : {GREEN + 'up' if lp else YELLOW + 'down'}{RESET}  http://localhost:{LOCAL_PORT}")
    if HAS_DB:
        t = subprocess.run(f"'{KIT}/db-tunnel.sh' --check", shell=True, capture_output=True).returncode == 0
        print(f"  db    : {GREEN + 'tunnel up' if t else YELLOW + 'tunnel down'}{RESET}  server database '{_db_name()}'")
    code = subprocess.run(f"curl -s -o /dev/null -w '%{{http_code}}' -m 8 {PUBLIC_URL}/",
                          shell=True, capture_output=True, text=True).stdout
    print(f"  AWS   : {GREEN if code == '200' else YELLOW}HTTP {code or '—'}{RESET}  {PUBLIC_URL}")
    return 0


def tail_logs():
    """4 · Tail the local logs."""
    return run("tail -f logs/*.log")


# ── 5–8 · Setup ──────────────────────────────────────────────────────────────

def first_time_setup():
    """5 · Create the venv (Python 3.12, as on the server), install deps (+ npm)."""
    if not shutil.which(PYTHON):
        print(f"{RED}{PYTHON} not found on PATH.{RESET}")
        return 1
    if not VENV_PY.exists():
        if VENV.exists():
            shutil.rmtree(VENV)
        if run(f"{PYTHON} -m venv '{VENV}'") != 0:
            return 1
    run(f"'{VENV_PY}' -m pip install -q --upgrade pip")
    rc = run(f"'{VENV_PY}' -m pip install -r {REQS}")
    if rc == 0 and HAS_FRONTEND:
        rc = run("cd frontend && npm install --no-audit --no-fund")
    if rc == 0:
        check_env()
    return rc


def check_env():
    """6 · Read-only pre-flight: keys, venv, gitignore, SSH, tunnel, server."""
    print(f"\n{BOLD}Environment check — {APP}{RESET}\n")
    good = True

    def line(ok, msg):
        nonlocal good
        good &= bool(ok)
        print(f"  {GREEN + '✓' if ok else RED + '✗'}{RESET} {msg}")

    keys = _env_keys()
    line(ENV_FILE.is_file(), f"key file {str(ENV_FILE).replace(str(Path.home()), '~')}")
    if ENV_FILE.is_file():
        line(oct(ENV_FILE.stat().st_mode)[-3:] == "600", "key file mode 600")
    for k in CONF.get("REQUIRED_KEYS", "").split():
        line(len(keys.get(k, "")) > 20, f"{k} set ({len(keys.get(k, ''))} chars)")
    ign = (ROOT / ".gitignore").read_text() if (ROOT / ".gitignore").is_file() else ""
    for f in (".env", "env"):
        if (ROOT / f).exists():
            line(re.search(rf"^/?{re.escape(f)}$", ign, re.M), f"{f} is gitignored")
            line(oct((ROOT / f).stat().st_mode)[-3:] == "600", f"{f} mode 600")
    py = subprocess.run(f"'{VENV_PY}' --version", shell=True, capture_output=True, text=True).stdout.strip()
    line(VENV_PY.exists(), f"venv {VENV.relative_to(ROOT)} ({py or 'missing — run 5'})")
    if HAS_FRONTEND:
        line((ROOT / "frontend" / "node_modules").is_dir(), "frontend/node_modules")
    k = Path(SSH_KEY)
    line(k.is_file() and oct(k.stat().st_mode)[-3:] in ("600", "400"), f"SSH key {k.name} (mode 600)")
    try:
        socket.create_connection((SSH_HOST, 22), timeout=4).close()
        line(True, f"server {SSH_HOST}:22 reachable")
    except OSError as e:
        line(False, f"server {SSH_HOST}:22 unreachable — {e}")
    if HAS_DB:
        t = subprocess.run(f"'{KIT}/db-tunnel.sh' --check", shell=True, capture_output=True).returncode == 0
        line(t, "tunnel to the server database (127.0.0.1:55432)" + ("" if t else " — run 8"))
    print(f"\n{GREEN + 'Environment ready.' if good else YELLOW + 'Some items need attention (above).'}{RESET}")
    return 0 if good else 1


def recover():
    """7 · Wipe venv (and node_modules), reinstall, restart."""
    run("./stop-all.sh")
    shutil.rmtree(VENV, ignore_errors=True)
    if HAS_FRONTEND:
        shutil.rmtree(ROOT / "frontend" / "node_modules", ignore_errors=True)
    rc = first_time_setup()
    return rc if rc else run("./start-all.sh")


def db_tunnel():
    """8 · Bring up (or check) the tunnel to the server database."""
    if not HAS_DB:
        print(f"{YELLOW}{APP} has no database.{RESET}")
        return 0
    return run(f"'{KIT}/db-tunnel.sh' --bg")


# ── 9–14 · Git and deploy ────────────────────────────────────────────────────

def git_status():
    """9 · git status."""
    return run("git status") if _need_git() else 0


def git_commit():
    """10 · Commit all changes and push the current branch."""
    if not _need_git():
        return 0
    branch = ask("Your branch", _branch())
    if not branch:
        return 1
    if branch != _branch() and run(f"git checkout {branch}") != 0:
        return 1
    if _dirty():
        msg = ask("Commit message")
        if not msg:
            print(f"{RED}Empty message — aborting.{RESET}")
            return 1
        run("git add -A")
        if run('git commit -m "{}"'.format(msg.replace('"', '\\"'))) != 0:
            return 1
    if run(f"git pull --no-edit origin {branch}") != 0:
        print(f"{RED}Pull produced conflicts — resolve them, then re-run 10.{RESET}")
        return 2
    return run(f"git push -u origin {branch}")


def git_pull():
    """11 · Pull origin/<branch> (auto-commits forgotten work first)."""
    if not _need_git():
        return 0
    branch = ask("Your branch", _branch())
    before = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if _dirty():
        run("git add -A")
        run('git commit -m "WIP before pulling"')
    if run(f"git pull --no-edit origin {branch}") != 0:
        print(f"{RED}Pull produced conflicts — resolve them, then run 10.{RESET}")
        return 2
    changed = subprocess.run(["git", "diff", "--name-only", before, "HEAD"], capture_output=True, text=True).stdout
    if Path(REQS).name in changed or "package.json" in changed:
        run(f"'{VENV_PY}' -m pip install -r {REQS}")
        if HAS_FRONTEND:
            run("cd frontend && npm install --no-audit --no-fund")
        return restart_servers()
    return 0


def deploy_aws():
    """12 · DEPLOY: build, ship, migrate, restart the servers ON AWS, verify the URL."""
    print(f"\n{RED}{BOLD}Deploy {APP} to AWS — {PUBLIC_URL}{RESET}")
    print(f"{DIM}Builds locally, ships by rsync (not GitHub), runs migrations, restarts "
          f"{APP}.service on the server, and checks the live URL.{RESET}")
    if ask("Type DEPLOY (uppercase) to continue") != "DEPLOY":
        print(f"{YELLOW}Aborted.{RESET}")
        return 1
    return run("./deploy-aws.sh")


def aws_status_logs():
    """13 · AWS: service state, last 40 log lines, deployed revision (read-only)."""
    return remote(f"'systemctl status {APP} --no-pager -n 0; sudo journalctl -u {APP} -n 40 --no-pager; "
                  f"cat /var/www/{APP}/DEPLOYED_REVISION'")


def git_log():
    """14 · Recent commits."""
    return run("git log --oneline -15") if _need_git() else 0


# ── 15–16 · Server database ──────────────────────────────────────────────────

def db_shell():
    """15 · Read-only psql on the server database."""
    if not HAS_DB:
        print(f"{YELLOW}{APP} has no database.{RESET}")
        return 0
    return remote(f"-t \"sudo -u postgres env PGOPTIONS='-c default_transaction_read_only=on' psql {_db_name()}\"")


def migration_status():
    """16 · Which migrations the server database has (read-only)."""
    cmd = CONF.get("MIGRATE_STATUS_CMD")
    if not cmd:
        print(f"{YELLOW}{APP} has no migrations.{RESET}")
        return 0
    if HAS_DB:
        subprocess.run(f"'{KIT}/db-tunnel.sh' --bg", shell=True, capture_output=True)
    keys = {k: v for k, v in _env_keys().items() if not k.startswith("SERVER_")}
    print(f"{DIM}$ {cmd}{RESET}")
    return subprocess.call(cmd, shell=True, executable="/bin/bash", env={**os.environ, **keys})


# ── 17–18 · Tests ────────────────────────────────────────────────────────────

def backend_tests():
    """17 · The repo's test command (TEST_CMD in deploy/app.conf)."""
    cmd = CONF.get("TEST_CMD")
    if not cmd:
        print(f"{YELLOW}No TEST_CMD in deploy/app.conf.{RESET}")
        return 0
    (ROOT / "logs").mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return run(f"{cmd} 2>&1 | tee logs/test-{stamp}.log")


def e2e_tests():
    """18 · Playwright E2E under tests/e2e (AWF_BASE_URL=local or AWS)."""
    if not (ROOT / "tests" / "e2e").is_dir():
        print(f"{YELLOW}No tests/e2e yet.{RESET}")
        return 0
    return run(f"'{VENV_PY}' -m pytest tests/e2e -v --headed --slowmo=400")


# ── Menu ─────────────────────────────────────────────────────────────────────

MENU = [
    ("Servers (local — same ports as AWS)", None),
    (f"Restart servers  (http://localhost:{LOCAL_PORT})", restart_servers),   # → 1
    ("Stop servers", stop_servers),                                           # → 2
    ("Status (local + AWS)", status),                                         # → 3
    ("Tail logs (Ctrl+C to exit)", tail_logs),                                # → 4
    ("Setup", None),
    ("First-time setup (venv on Python 3.12 + deps)", first_time_setup),      # → 5
    ("Check environment (read-only pre-flight)", check_env),                  # → 6
    ("Recover (wipe venv/node_modules, reinstall, restart)", recover),        # → 7
    ("DB tunnel to the server database", db_tunnel),                          # → 8
    ("Git and deploy", None),
    ("Git status", git_status),                                               # → 9
    ("Commit & push", git_commit),                                            # → 10
    ("Pull", git_pull),                                                       # → 11
    (f"DEPLOY to AWS  ({PUBLIC_URL})", deploy_aws),                           # → 12
    ("AWS status + logs (read-only)", aws_status_logs),                       # → 13
    ("git log (last 15)", git_log),                                           # → 14
    ("Server database", None),
    ("Explore server DB (read-only psql)", db_shell),                         # → 15
    ("Migration status (server DB, read-only)", migration_status),            # → 16
    ("Tests", None),
    ("Backend tests", backend_tests),                                         # → 17
    ("E2E tests (Playwright)", e2e_tests),                                    # → 18
]

# App extras: EXTRA_19_LABEL / EXTRA_19_CMD … in deploy/app.conf, numbered from 19.
_extras = sorted(int(m.group(1)) for k in CONF if (m := re.match(r"EXTRA_(\d+)_LABEL$", k)))
if _extras:
    MENU.append((f"{APP} extras", None))
    for n in _extras:
        cmd = CONF.get(f"EXTRA_{n}_CMD", "")
        MENU.append((CONF[f"EXTRA_{n}_LABEL"], (lambda c=cmd: run(c))))


def render_menu():
    print(f"\n{BOLD}{CYAN}{APP} — AWF{RESET}  {DIM}({_branch() or 'no git'} · {PUBLIC_URL}){RESET}\n")
    n, indices = 0, {}
    for label, action in MENU:
        if action is None:
            print(f"\n{BOLD}{YELLOW}── {label} ──{RESET}")
            continue
        n += 1
        indices[n] = action
        print(f"  {GREEN}{n:>2}{RESET}  {label}")
    print(f"\n  {GREEN} q{RESET}  Quit\n")
    return indices


def main():
    if len(sys.argv) > 1:
        indices = render_menu()
        try:
            return indices[int(sys.argv[1])]() or 0
        except (ValueError, KeyError):
            print(f"{RED}Invalid choice: {sys.argv[1]}{RESET}")
            return 1
    while True:
        indices = render_menu()
        choice = ask("Choose")
        if choice in ("q", "Q", "quit", "exit", ""):
            return 0
        try:
            indices[int(choice)]()
        except (ValueError, KeyError):
            print(f"{RED}Invalid choice: {choice}{RESET}")
        input(f"\n{DIM}Press Enter to return to menu...{RESET}")


if __name__ == "__main__":
    sys.exit(main())
