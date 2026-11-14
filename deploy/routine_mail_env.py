"""Create the routine's private mail settings from the currently deployed scripts.

Run once on the server, as root, by `illoura_aws.py set-mail-config --yes`, before the
repository version of the routine (which reads these settings instead of hard-coding them)
is deployed. The values never leave the server and are never printed: the deployed
send_gmail.py and compose_checked_brief.py already contain them, and this copies them into
/etc/illoura-routine/mail.env (root:scraper 0640). An existing file is never replaced.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets

SCRIPTS = Path("/opt/illoura-routine/scripts")
# Same rule as the runtime reader (routine/scripts/mail_config.py): a value this accepts
# must be accepted there, or set-mail-config would report success and the send fail later.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@" + _LABEL + r"(?:\." + _LABEL + r")+")
LINK_ID = re.compile(r"[A-Za-z0-9_.:-]{4,200}")


def extract(send_source, compose_source):
    """The three private values, from the two deployed files' literals."""
    found = re.search(r"SENDER='([^']+)';RECIPIENT='([^']+)'", send_source)
    link = re.search(r"'link_id':'([^']+)'", compose_source)
    if not found or not link:
        raise ValueError("deployed scripts no longer contain the mail literals")
    sender, recipient, link_id = found.group(1), found.group(2), link.group(1)
    if not (ADDRESS.fullmatch(sender) and ADDRESS.fullmatch(recipient) and LINK_ID.fullmatch(link_id)):
        raise ValueError("deployed mail literals are not plain values")
    return {
        "ILLOURA_MAIL_SENDER": sender,
        "ILLOURA_MAIL_RECIPIENT": recipient,
        "ILLOURA_MAIL_LINK_ID": link_id,
    }


def render(values):
    return "".join(f"{key}={value}\n" for key, value in values.items())


def write(target, text, group="scraper"):
    """Write once, atomically; an existing file is compared, never replaced."""
    target = Path(target)
    if target.exists():
        return "unchanged" if target.read_text(encoding="utf-8") == text else "differs_not_replaced"
    # A fresh, unpredictable name opened exclusively: a file or link planted at a fixed
    # temporary name can never be written through.
    temporary = target.with_name("." + target.name + "." + secrets.token_hex(8) + ".tmp")
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        if group is not None:
            import grp  # Linux only; tests pass group=None

            os.chown(temporary, 0, grp.getgrnam(group).gr_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    except BaseException:
        # Never leave a private copy behind under a hidden name.
        temporary.unlink(missing_ok=True)
        raise
    return "written"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", required=True, help="target mail.env path")
    args = parser.parse_args(argv)
    values = extract(
        (SCRIPTS / "send_gmail.py").read_text(encoding="utf-8"),
        (SCRIPTS / "compose_checked_brief.py").read_text(encoding="utf-8"),
    )
    status = write(args.write, render(values))
    print(json.dumps({"status": status, "keys": sorted(values)}))
    return 0 if status in ("written", "unchanged") else 3


if __name__ == "__main__":
    raise SystemExit(main())
