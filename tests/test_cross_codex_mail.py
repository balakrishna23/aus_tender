"""Migration and runtime mail configuration boundary tests."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = load('cross_mail_migration', 'deploy/routine_mail_env.py')
runtime = load('cross_mail_runtime', 'routine/scripts/mail_config.py')


class MailBoundaries(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.target = self.root / 'mail.env'

    def test_write_once_preserves_existing(self):
        """Rerunning migration must never silently change the delivery recipient."""
        self.assertEqual(migration.write(self.target, 'original', group=None), 'written')
        self.assertEqual(migration.write(self.target, 'original', group=None), 'unchanged')
        self.assertEqual(migration.write(self.target, 'replacement', group=None), 'differs_not_replaced')
        self.assertEqual(self.target.read_text(), 'original')

    def test_temporary_symlink_not_followed(self):
        """A planted temp-file link must not overwrite another private file."""
        victim = self.root / 'victim'
        victim.write_text('preserve')
        link = self.root / '.mail.env.tmp'
        try:
            link.symlink_to(victim)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable')
        try:
            migration.write(self.target, 'replacement', group=None)
        except (OSError, ValueError):
            pass
        self.assertEqual(victim.read_text(), 'preserve')

    def test_migrated_addresses_runtime_accepts(self):
        """Migration success must not produce a mail.env the sender rejects."""
        for address in ('sender@example.com', 'sender@-bad.example.com', 'sender@bad-.example.com'):
            source = "SENDER='" + address + "';RECIPIENT='reader@example.com'"
            try:
                values = migration.extract(source, "{'link_id':'fixture-link'}")
            except ValueError:
                continue
            self.target.write_text(migration.render(values), encoding='utf-8')
            with self.subTest(domain_case=('leading-hyphen' if '@-' in address else 'trailing-hyphen' if '-.' in address else 'valid')), patch.dict(os.environ, {'ILLOURA_MAIL_ENV': str(self.target)}, clear=True):
                self.assertEqual(runtime.load_mail_config(), (address.lower(), 'reader@example.com'))

    def test_environment_header_injection_rejected(self):
        """Configuration overrides must never inject additional mail headers."""
        for value in ('sender@example.com\nBcc: injected@example.com', 'Display <sender@example.com>', 'one@example.com,two@example.com', ''):
            with patch.dict(os.environ, {'ILLOURA_MAIL_ENV': str(self.target), 'ILLOURA_MAIL_SENDER': value, 'ILLOURA_MAIL_RECIPIENT': 'reader@example.com'}, clear=True):
                with self.assertRaises(ValueError):
                    runtime.load_mail_config()

    def test_file_environment_precedence(self):
        """Explicit private environment settings must override the migrated file."""
        self.target.write_text('ILLOURA_MAIL_SENDER=old@example.com\nILLOURA_MAIL_RECIPIENT=reader@example.com\nILLOURA_MAIL_LINK_ID=fixture-link\n')
        with patch.dict(os.environ, {'ILLOURA_MAIL_ENV': str(self.target), 'ILLOURA_MAIL_SENDER': 'new@example.com'}, clear=True):
            self.assertEqual(runtime.load_mail_config(), ('new@example.com', 'reader@example.com'))
            self.assertEqual(runtime.load_link_id(), 'fixture-link')
