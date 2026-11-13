"""Read-only operational checks. Never sends mail; failures are safe to poll externally."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo
from illoura_cloud_ops import atomic_json, utc

SYDNEY = ZoneInfo("Australia/Sydney")
BRIEF_TIMER = "illoura-daily-brief.timer"
BRIEF_DUE = (9, 0)  # Sydney time; the brief is sent at 08:15
RUN_LIMIT_SECONDS = 5 * 3600  # a morning run normally finishes in about two hours
CERT = Path("/etc/letsencrypt/live/illoura-api/cert.pem")
# The certificate lives about 6.7 days and certbot renews it with roughly 2 days left,
# retrying twice a day; under one day left means renewal has been failing.
CERT_WARN_SECONDS = 24 * 3600


def cert_not_after(path):
    """Expiry of a PEM certificate, read with openssl (no extra Python packages)."""
    out = subprocess.run(
        ["openssl", "x509", "-enddate", "-noout", "-in", str(path)],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    text = " ".join(out.strip().split("=", 1)[1].split())
    return datetime.strptime(text, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def certificate(path, now, read_end=cert_not_after):
    """Warn a day before the HTTPS certificate expires, long before the external watch notices."""
    try:
        left = (read_end(path) - now).total_seconds()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {"status": "unavailable"}
    status = "ok" if left > CERT_WARN_SECONDS else ("expiring" if left > 0 else "expired")
    return {"status": status, "hours_left": round(left / 3600, 1)}


def https_installed(path):
    """The certificate exists (or cannot even be looked at, which certificate() reports)."""
    try:
        return Path(path).exists()
    except OSError:
        return True


def brief_timer_enabled():
    """True/False from systemd; None when it cannot be asked (for example in tests)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", BRIEF_TIMER],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() == "enabled"


def run_in_progress(state, now):
    """A collection that is still within its normal running time is not a failure."""
    phase = str(state.get("status") or "")
    if not (phase.startswith("running") or phase == "building_evidence"):
        return False
    try:
        started = datetime.fromisoformat(state["started_at_utc"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= (now - started).total_seconds() < RUN_LIMIT_SECONDS


def last_brief_sent(work):
    """Newest successful send receipt across all runs (latest_run.json moves on at 06:15)."""
    newest = None
    for receipt in (work / "reports").glob("daily_sweep_*/send_receipt.json"):
        try:
            value = json.loads(receipt.read_text())
            if value.get("sent") is True:
                stamp = datetime.fromisoformat(value["recorded_at_utc"])
                newest = stamp if newest is None or stamp > newest else newest
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return newest


def daily_brief(work, now, enabled):
    """By 09:00 Sydney each day there must be a brief sent that Sydney day."""
    if enabled is not True:
        return {"status": "ok", "schedule": "off" if enabled is False else "unknown"}
    local = now.astimezone(SYDNEY)
    due = local.replace(hour=BRIEF_DUE[0], minute=BRIEF_DUE[1], second=0, microsecond=0)
    if local < due:
        due -= timedelta(days=1)
    sent = last_brief_sent(work)
    on_time = sent is not None and sent.astimezone(SYDNEY).date() == due.date()
    return {
        "status": "ok" if on_time else "missed",
        "due_by_sydney": due.isoformat(timespec="minutes"),
        "last_sent_utc": sent.isoformat(timespec="seconds") if sent else None,
    }


def check(
    work=Path("/opt/illoura-routine"),
    served=Path("/srv/illoura-data"),
    token=Path("/etc/illoura-routine/gmail-token.json"),
    brief_enabled=None,
    now=None,
    cert=CERT,
    read_cert_end=cert_not_after,
):
    now = now or datetime.now(timezone.utc)
    if brief_enabled is None:
        brief_enabled = brief_timer_enabled()
    checks = {}
    # Only where HTTPS is installed (the server); nginx cannot serve without this file.
    if https_installed(cert):
        checks["certificate"] = certificate(cert, now, read_cert_end)

    def freshness(name, path, field):
        try:
            value = json.loads(path.read_text())
            stamp = value[field]
            age = (now - datetime.fromisoformat(stamp)).total_seconds()
            checks[name] = {
                "status": "ok" if 0 <= age <= 26 * 3600 else "stale",
                "age_seconds": round(age),
                "at_utc": stamp,
            }
        except (OSError, ValueError, KeyError, TypeError):
            checks[name] = {"status": "unavailable"}

    freshness("publication", served / "current/manifest.json", "snapshot_finished_utc")
    try:
        state = json.loads((work / "latest_run.json").read_text())
        run = Path(state["run_dir"])
        if run.parent.resolve() != (work / "reports").resolve():
            raise ValueError("Unexpected run path")
        if run_in_progress(state, now):
            # Today's run has not produced evidence yet; publication freshness still
            # covers the age of the data being served.
            checks["collection"] = {"status": "ok", "phase": "in_progress"}
        else:
            freshness("collection", run / "newsletter_evidence.json", "generated_at_utc")
        checks["last_run"] = {
            "status": "ok"
            if state.get("status")
            in [
                "ready_for_editorial_review",
                "reviewed_blocked_mailbox_authorization",
                "sent",
            ]
            or run_in_progress(state, now)
            else "attention",
            "phase": state.get("status"),
        }
    except (OSError, ValueError, KeyError):
        checks["last_run"] = {"status": "unavailable"}
    checks["daily_brief"] = daily_brief(work, now, brief_enabled)
    coordinator = work / "coordinator_status.json"
    if coordinator.exists():
        try:
            value = json.loads(coordinator.read_text())
            elapsed = (
                now - datetime.fromisoformat(value["started_at_utc"])
            ).total_seconds()
            healthy = value.get("status") == "completed" or (
                value.get("status") == "running" and 0 <= elapsed < RUN_LIMIT_SECONDS
            )
            checks["coordinator"] = {
                "status": "ok" if healthy else "attention",
                "phase": value.get("status"),
            }
        except (OSError, ValueError, KeyError, TypeError):
            checks["coordinator"] = {"status": "unavailable"}
    disk = shutil.disk_usage(served)
    checks["disk"] = {
        "status": "ok"
        if disk.free > 10 * 1024**3 and disk.free / disk.total > 0.15
        else "low",
        "free_bytes": disk.free,
        "total_bytes": disk.total,
    }
    retention = served / "retention.json"
    if retention.exists():
        try:
            value = json.loads(retention.read_text(encoding="utf-8"))
            checks["retention"] = {
                "status": "ok" if value.get("status") == "ok" else "attention",
                "checked_at_utc": value.get("checked_at_utc"),
                "reclaimed_bytes": value.get("reclaimed_bytes", 0),
                "observed_free_bytes_change": value.get("observed_free_bytes_change"),
                "retained_generation_bytes": value.get("retained_generation_bytes"),
                "retained_backup_bytes": value.get("retained_backup_bytes"),
                "deleted_directories": len(value.get("deleted", [])),
                "errors": len(value.get("errors", [])),
            }
        except (OSError, ValueError, KeyError, TypeError):
            checks["retention"] = {"status": "unavailable"}
    # The expected sending mailbox is private server configuration, never source code.
    expected = os.environ.get("ILLOURA_GMAIL_SENDER", "").strip().lower()
    if not expected:
        checks["mail_authorization"] = {
            "status": "unconfigured",
            "detail": "Set ILLOURA_GMAIL_SENDER in /etc/illoura-ops/health.env",
        }
    else:
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import AuthorizedSession, Request

            creds = Credentials.from_authorized_user_file(str(token))
            if not creds.valid:
                creds.refresh(Request())
            session = AuthorizedSession(creds)
            response = session.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=30
            )
            response.raise_for_status()
            checks["mail_authorization"] = {
                "status": "ok"
                if response.json()["emailAddress"].lower() == expected
                else "identity_mismatch"
            }
            session.close()
        except Exception as error:
            checks["mail_authorization"] = {
                "status": "failed",
                "error_type": type(error).__name__,
            }
    ok = all(v["status"] == "ok" for v in checks.values())
    return {
        "status": "ok" if ok else "attention",
        "checked_at_utc": utc(),
        "checks": checks,
        "email_sent": False,
        "monitoring_note": "Poll independently of this instance and Gmail to detect silence. A health file alone is not an external alert.",
    }


if __name__ == "__main__":
    result = check()
    path = Path("/var/lib/illoura-health/status.json")
    atomic_json(path, result)
    shutil.chown(path, group="illoura-api")
    path.chmod(0o640)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "ok" else 1)
