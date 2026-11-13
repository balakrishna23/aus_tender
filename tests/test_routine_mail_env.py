"""set-mail-config helper: copies the deployed literals into mail.env without printing them."""

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("routine_mail_env", ROOT / "deploy/routine_mail_env.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

SEND = "SENDER='sender@example.com';RECIPIENT='team@example.org'\n"
COMPOSE = "    request = {'link_id':'conn_ABC123-x','from_address':package['from'],\n"


class MailEnvFromDeployed(unittest.TestCase):
    def test_extracts_the_three_values(self):
        self.assertEqual(helper.extract(SEND, COMPOSE), {
            "ILLOURA_MAIL_SENDER": "sender@example.com",
            "ILLOURA_MAIL_RECIPIENT": "team@example.org",
            "ILLOURA_MAIL_LINK_ID": "conn_ABC123-x",
        })

    def test_refuses_when_literals_are_gone_or_odd(self):
        for send, compose in (("SENDER=load()\n", COMPOSE), (SEND, "request = {'link_id':load_link_id()}"),
                              ("SENDER='a b@x.com';RECIPIENT='t@example.org'", COMPOSE),
                              (SEND, "{'link_id':'has space'}")):
            with self.assertRaises(ValueError):
                helper.extract(send, compose)

    def test_writes_once_and_never_replaces(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "mail.env"
            text = helper.render(helper.extract(SEND, COMPOSE))
            self.assertEqual(helper.write(target, text, group=None), "written")
            self.assertEqual(target.read_text(encoding="utf-8").splitlines()[0],
                             "ILLOURA_MAIL_SENDER=sender@example.com")
            self.assertEqual(helper.write(target, text, group=None), "unchanged")
            self.assertEqual(helper.write(target, "ILLOURA_MAIL_SENDER=other@example.com\n", group=None),
                             "differs_not_replaced")
            self.assertIn("sender@example.com", target.read_text(encoding="utf-8"))

    @unittest.skipIf(sys.platform == "win32", "file modes and chown are Linux-only")
    def test_mode_is_0640(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "mail.env"
            helper.write(target, "X=1\n", group=None)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_main_prints_keys_never_values(self):
        with tempfile.TemporaryDirectory() as temp:
            scripts = Path(temp) / "scripts"
            scripts.mkdir()
            (scripts / "send_gmail.py").write_text(SEND)
            (scripts / "compose_checked_brief.py").write_text(COMPOSE)
            output = io.StringIO()
            with patch.object(helper, "SCRIPTS", scripts), patch.object(helper, "write", return_value="written"), \
                    redirect_stdout(output):
                code = helper.main(["--write", str(Path(temp) / "mail.env")])
        self.assertEqual(code, 0)
        printed = output.getvalue()
        self.assertNotIn("example.com", printed)
        self.assertNotIn("conn_ABC123", printed)
        self.assertEqual(json.loads(printed)["status"], "written")


if __name__ == "__main__":
    unittest.main()
