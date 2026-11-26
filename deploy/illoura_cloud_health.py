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
BRIEF_SERVICE = "illoura-daily-brief.service"
BRIEF_DUE = (9, 0)  # Sydney time; the brief is sent at 08:15
BRIEF_CUTOFF = (12, 0)  # Sydney time; nothing is sent after this
PUBLICATION_LIMIT_SECONDS = 26 * 3600
# A morning that needed its one retry publishes later than usual; allow it while it works.
PUBLICATION_WORKING_LIMIT_SECONDS = 32 * 3600
# One failed call to Google must not read as lost authorisation.
MAIL_GRACE_SECONDS = 6 * 3600
RECEIPT_CLOCK_SKEW = timedelta(minutes=5)
TRANSIENT_MAIL_ERRORS = (
    "TransportError", "ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout",
    "ChunkedEncodingError", "TimeoutError",
)

SOURCE_NAMES = {'contract_api', 'aus_contract', 'aus_tender', 'planned_procurement',
                'standing_offer', 'buy_ict', 'act_tender', 'nsw_tender'}


def source_diagnostics(run):
    """Expose bounded source statuses, never log text, URLs, contacts or paths."""
    try:
        report = json.loads((run / 'source_refresh.json').read_text())
        rows = report['results']
        if not isinstance(rows, list):
            return {'source_report': 'unavailable'}
        statuses, failed = {}, set()
        for row in rows:
            if not isinstance(row, dict) or row.get('spider') not in SOURCE_NAMES:
                continue
            name = row['spider']
            status = row.get('status')
            statuses[name] = status if status in {'OK', 'FAIL', 'PARTIAL', 'TIMEOUT', 'ERROR'} else 'unknown'
            if status != 'OK' or row.get('crawl_failed') or row.get('errors'):
                failed.add(name)
        return {'sources': statuses, 'failed_sources': sorted(failed),
                'missing_sources': sorted(SOURCE_NAMES - statuses.keys())}
    except (OSError, ValueError, KeyError, TypeError):
        return {'source_report': 'unavailable'}
RUN_LIMIT_SECONDS = 5 * 3600  # a morning run normally finishes in about two hours
CERT = Path("/etc/letsencrypt/live/illoura-api/cert.pem")
# The certificate lives about 6.7 days and certbot renews it with roughly 2 days left,
# retrying twice a day; under one day left means renewal has been failing.
CERT_WARN_SECONDS = 24 * 3600


def research_freshness(path=Path('/var/lib/illoura-research-refresh/status.json')):
    """Bounded informational data; deliberately outside the failing checks map."""
    allowed_sources = {'abn', 'asic_companies', 'asic_business_names', 'acnc', 'agor', 'government_directory',
        'oric', 'jsa_ivi', 'jsa_projections', 'jsa_shortages', 'jsa_reos', 'jsa_tnv', 'aps_employment',
        'aged_care_star_ratings', 'rap_directory', 'wa_contracts', 'procurement', 'tender_feed'}
    statuses = {'latest_available', 'new_edition_pending', 'check_overdue', 'blocked', 'failed', 'manual_refresh_needed'}
    try:
        if path.stat().st_size > 16_384:
            raise ValueError('Summary exceeds bounds')
        value = json.loads(path.read_text(encoding='utf-8'))
        sources = value.get('freshness', {})
        if not isinstance(sources, dict):
            raise ValueError('Invalid summary')
        result = dict(informational_only=True, sources={k: v for k, v in sources.items()
                      if k in allowed_sources and isinstance(v, str) and v in statuses})
        if value.get('checked_at'):
            checked = datetime.fromisoformat(value['checked_at'])
            if checked.tzinfo is None:
                raise ValueError('Summary timestamp must include timezone')
            age = (datetime.now(timezone.utc) - checked).total_seconds()
            if age < -300:
                raise ValueError('Summary timestamp is in the future')
            result.update(checked_at=checked.isoformat(), summary_age_hours=round(max(0, age) / 3600, 1),
                          availability='check_overdue' if age > 48 * 3600 else 'available')
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(informational_only=True, sources={}, availability='unavailable')


def research_monitor(work=Path('/opt/illoura-routine'), now=None,
                     state=Path('/var/lib/illoura-market-refresh/status.json'),
                     config=Path('/etc/illoura-market/refresh.json')):
    """Independent research readiness; never changes procurement checks or sends mail."""
    if not config.is_file():
        return dict(status='ok', configured=False)
    now = now or datetime.now(timezone.utc)
    checks = {}
    try:
        value = json.loads(state.read_text(encoding='utf-8'))
        checked = datetime.fromisoformat(value['checked_at'])
        age = (now - checked).total_seconds()
        checks['refresh'] = dict(status='ok' if value.get('status') == 'complete'
                                and 0 <= age <= 32 * 3600 else 'attention',
                                last_result=value.get('status') if value.get('status') in
                                {'complete', 'failed', 'busy', 'disabled'} else 'unknown',
                                age_hours=round(age / 3600, 1))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        checks['refresh'] = dict(status='unavailable')
    try:
        package = work / 'market_research.json'
        if package.stat().st_size > 1_000_000:
            raise ValueError('Package exceeds bounds')
        value = json.loads(package.read_text(encoding='utf-8'))
        stamp = datetime.fromisoformat(value['generated_at'])
        age = (now - stamp).total_seconds()
        checks['package'] = dict(status='ok' if 0 <= age <= 32 * 3600 else 'attention',
                                age_hours=round(age / 3600, 1))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        checks['package'] = dict(status='unavailable')
    publisher = research_freshness()
    sources = publisher.get('sources', {})
    # Only a failed or overdue check means the refresher is broken. A publisher blocked behind a
    # bot challenge (WA), a monthly manual download that is due, or a pending edition are known
    # states that a person acts on; they stay listed in source_gaps but must not hold the alarm
    # in ALARM permanently, or it could never signal a new failure.
    failing = sorted(k for k, v in sources.items() if v in ('failed', 'check_overdue'))
    checks['publishers'] = dict(status='ok' if publisher.get('availability') == 'available'
                               and sources and not failing else 'attention',
                               failing=failing,
                               source_gaps=sorted(k for k, v in sources.items() if v != 'latest_available'))
    return dict(status='ok' if all(v['status'] == 'ok' for v in checks.values()) else 'attention',
                configured=True, checks=checks)


def cert_not_after(path):
    """Expiry of a PEM certificate, read with openssl (no extra Python packages)."""
    if not Path(path).exists():
        raise FileNotFoundError(path)
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
    except FileNotFoundError:
        return {"status": "missing"}  # nginx cannot serve HTTPS without it
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {"status": "unavailable"}
    status = "ok" if left > CERT_WARN_SECONDS else ("expiring" if left > 0 else "expired")
    return {"status": status, "hours_left": round(left / 3600, 1)}


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


def brief_service_active():
    """True while the morning service is collecting, composing or waiting to send."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", BRIEF_SERVICE],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() in ("active", "activating")


def run_in_progress(state, now):
    """A collection that is still within its normal running time is not a failure."""
    phase = str(state.get("status") or "")
    if not (phase.startswith("running") or phase == "building_evidence"):
        return False
    try:
        started = datetime.fromisoformat(state["started_at_utc"])
        return 0 <= (now - started).total_seconds() < RUN_LIMIT_SECONDS
    except (KeyError, TypeError, ValueError):
        return False  # includes a start time without a timezone


def last_brief_sent(work, now=None):
    """Newest successful send receipt across all runs (latest_run.json moves on at 06:15).

    Only evidence that can be trusted counts: a receipt object with sent true and a time
    that carries its timezone and is not in the future (a future-dated receipt would hide
    missed briefs for days).
    """
    now = now or datetime.now(timezone.utc)
    newest = None
    for receipt in (work / "reports").glob("daily_sweep_*/send_receipt.json"):
        try:
            value = json.loads(receipt.read_text())
            if not isinstance(value, dict) or value.get("sent") is not True:
                continue
            stamp = datetime.fromisoformat(value["recorded_at_utc"])
            if stamp.tzinfo is None or stamp > now + RECEIPT_CLOCK_SKEW:
                continue
            newest = stamp if newest is None or stamp > newest else newest
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return newest


def daily_brief(work, now, enabled, working=False):
    """By 09:00 Sydney each day there must be a brief sent that Sydney day.

    While the morning service is still working (a retry, a slow source) the brief can
    still go out until 12:00, so that is "pending", not a miss.
    """
    if enabled is not True:
        return {"status": "ok", "schedule": "off" if enabled is False else "unknown"}
    local = now.astimezone(SYDNEY)
    due = local.replace(hour=BRIEF_DUE[0], minute=BRIEF_DUE[1], second=0, microsecond=0)
    if local < due:
        due -= timedelta(days=1)
    sent = last_brief_sent(work, now)
    # On or after the due day: between today's 08:15 send and 09:00 the due day is still
    # yesterday, and today's receipt must not read as a miss (false alarm of 14 Nov 2026).
    on_time = sent is not None and sent.astimezone(SYDNEY).date() >= due.date()
    cutoff = due.replace(hour=BRIEF_CUTOFF[0], minute=BRIEF_CUTOFF[1])
    if not on_time and working and due <= local < cutoff:
        return {
            "status": "ok",
            "phase": "pending",
            "due_by_sydney": due.isoformat(timespec="minutes"),
            "cutoff_sydney": cutoff.isoformat(timespec="minutes"),
            "last_sent_utc": sent.isoformat(timespec="seconds") if sent else None,
        }
    return {
        "status": "ok" if on_time else "missed",
        "due_by_sydney": due.isoformat(timespec="minutes"),
        "last_sent_utc": sent.isoformat(timespec="seconds") if sent else None,
    }


def transient_mail_error(error):
    """Network trouble or a Google-side error, as opposed to a revoked or expired grant."""
    if type(error).__name__ in TRANSIENT_MAIL_ERRORS:
        return True
    status = getattr(getattr(error, "response", None), "status_code", None)
    return isinstance(status, int) and (status >= 500 or status == 429)


def verify_mailbox(token):
    """Address of the authorised sending mailbox, asked from Gmail."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession, Request

    creds = Credentials.from_authorized_user_file(str(token))
    if not creds.valid:
        creds.refresh(Request())
    session = AuthorizedSession(creds)
    try:
        response = session.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=30
        )
        response.raise_for_status()
        return response.json()["emailAddress"].lower()
    finally:
        session.close()


def mail_authorization(token, expected, now, previous=None, verify=verify_mailbox):
    try:
        address = verify(token)
    except Exception as error:
        result = {"status": "failed", "error_type": type(error).__name__}
        try:
            verified = previous["checks"]["mail_authorization"]["verified_at_utc"]
            age = (now - datetime.fromisoformat(verified)).total_seconds()
        except (KeyError, TypeError, ValueError):
            return result
        if transient_mail_error(error) and 0 <= age <= MAIL_GRACE_SECONDS:
            # Authorisation was confirmed recently and this error is not about the grant.
            return {**result, "status": "ok", "phase": "unverified", "verified_at_utc": verified}
        return result
    if address != expected:
        return {"status": "identity_mismatch"}
    return {"status": "ok", "verified_at_utc": now.isoformat(timespec="seconds")}


def check(
    work=Path("/opt/illoura-routine"),
    served=Path("/srv/illoura-data"),
    token=Path("/etc/illoura-routine/gmail-token.json"),
    brief_enabled=None,
    now=None,
    cert=CERT,
    read_cert_end=cert_not_after,
    brief_active=None,
    previous=None,
    verify_mail=verify_mailbox,
):
    now = now or datetime.now(timezone.utc)
    if brief_enabled is None:
        brief_enabled = brief_timer_enabled()
    if brief_active is None:
        brief_active = brief_service_active()
    working = bool(brief_active)
    checks = {}
    # The server always serves HTTPS, so a missing certificate is a failure, not "not
    # configured". cert=None switches the check off explicitly (tests only).
    if cert is not None:
        checks["certificate"] = certificate(cert, now, read_cert_end)

    def freshness(name, path, field):
        try:
            value = json.loads(path.read_text())
            stamp = value[field]
            age = (now - datetime.fromisoformat(stamp)).total_seconds()
            checks[name] = {
                "status": "ok" if 0 <= age <= PUBLICATION_LIMIT_SECONDS else "stale",
                "age_seconds": round(age),
                "at_utc": stamp,
            }
        except (OSError, ValueError, KeyError, TypeError):
            checks[name] = {"status": "unavailable"}

    freshness("publication", served / "current/manifest.json", "snapshot_finished_utc")
    try:
        state = json.loads((work / "latest_run.json").read_text())
        if not isinstance(state.get("status"), str):
            raise ValueError("Run state without a readable status")
        run = Path(state["run_dir"])
        if run.parent.resolve() != (work / "reports").resolve():
            raise ValueError("Unexpected run path")
        working = working or run_in_progress(state, now)
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
        checks['last_run'].update(source_diagnostics(run))
        if isinstance(state.get("source_gaps"), list) and state["source_gaps"]:
            # Sources that had a problem while the run still completed (the brief uses BuyICT only).
            checks["last_run"]["source_gaps"] = [str(name)[:40] for name in state["source_gaps"][:8]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Unreadable or wrongly shaped state is reported, never allowed to stop the poll.
        checks.setdefault("collection", {"status": "unavailable"})
        checks["last_run"] = {"status": "unavailable"}
    publication = checks["publication"]
    if (
        publication["status"] == "stale"
        and working
        and 0 <= publication["age_seconds"] <= PUBLICATION_WORKING_LIMIT_SECONDS
    ):
        # Today's publication is late because the morning run is still working.
        publication.update(status="ok", phase="in_progress")
    checks["daily_brief"] = daily_brief(work, now, brief_enabled, working)
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
        checks["mail_authorization"] = mail_authorization(
            token, expected, now, previous, verify_mail
        )
    ok = all(v["status"] == "ok" for v in checks.values())
    return {
        "status": "ok" if ok else "attention",
        "checked_at_utc": utc(),
        "checks": checks,
        "research_freshness": research_freshness(),
        "research_monitor": research_monitor(work, now),
        "email_sent": False,
        "monitoring_note": "Poll independently of this instance and Gmail to detect silence. A health file alone is not an external alert.",
    }


if __name__ == "__main__":
    path = Path("/var/lib/illoura-health/status.json")
    try:
        earlier = json.loads(path.read_text())
    except (OSError, ValueError):
        earlier = None
    result = check(previous=earlier)
    atomic_json(path, result)
    shutil.chown(path, group="illoura-api")
    path.chmod(0o640)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "ok" else 1)
