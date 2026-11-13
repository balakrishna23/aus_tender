import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('routine_mail_config', ROOT/'routine/scripts/mail_config.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class MailConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'mail.env'
        env = patch.dict(os.environ, {'ILLOURA_MAIL_ENV': str(self.path)}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_environment_and_sender_case(self):
        with patch.dict(os.environ, ILLOURA_MAIL_SENDER='Sender@EXAMPLE.COM',
                        ILLOURA_MAIL_RECIPIENT='Reader@example.org'):
            self.assertEqual(config.load_mail_config(), ('sender@example.com', 'Reader@example.org'))

    def test_private_file_and_override(self):
        self.path.write_text('# private settings\nILLOURA_MAIL_SENDER=file@example.com\n'
                             'ILLOURA_MAIL_RECIPIENT=reader@example.org\nILLOURA_MAIL_LINK_ID=example-link\n')
        with patch.dict(os.environ, ILLOURA_MAIL_SENDER='env@example.com'):
            self.assertEqual(config.load_mail_config(), ('env@example.com', 'reader@example.org'))
            self.assertEqual(config.load_link_id(), 'example-link')

    def test_missing(self):
        with self.assertRaisesRegex(ValueError, 'ILLOURA_MAIL_SENDER'):
            config.load_mail_config()
        with patch.dict(os.environ, ILLOURA_MAIL_SENDER='sender@example.com'):
            with self.assertRaisesRegex(ValueError, 'ILLOURA_MAIL_RECIPIENT'):
                config.load_mail_config()
        with self.assertRaisesRegex(ValueError, 'ILLOURA_MAIL_LINK_ID'):
            config.load_link_id()

    def test_malformed(self):
        for value in ('missing-domain', 'a@example.com\nBcc: b@example.org',
                      'A <a@example.com>', 'a@example.com,b@example.org', 'a@-bad.com', ''):
            with self.subTest(value=value), patch.dict(os.environ,
                    ILLOURA_MAIL_SENDER=value, ILLOURA_MAIL_RECIPIENT='reader@example.org'):
                with self.assertRaises(ValueError):
                    config.load_mail_config()
        self.path.write_text('bad configuration line')
        with self.assertRaises(ValueError):
            config.load_mail_config()

    def test_no_private_addresses_in_routine(self):
        pattern = re.compile(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')
        for path in (ROOT/'routine').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                for match in pattern.finditer(path.read_text(encoding='utf-8')):
                    self.assertIn(match.group(1).lower(), ('example.com', 'example.org'), str(path))
