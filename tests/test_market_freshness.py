"""Synthetic orchestration with an in-memory SQL boundary; never opens a socket."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from market_intelligence import freshness as f, research_refresh as r

NOW = datetime(2026, 11, 23, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, value=None, rows=()):
        self.value, self.rows = value, list(rows)

    def scalar(self): return self.value
    def scalars(self): return self.rows
    def mappings(self): return self
    def first(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)


class MemoryDB:
    def __init__(self):
        self.states, self.events = {}, []
        self.held = True

    def begin(self): return nullcontext(self)
    def connect(self): return nullcontext(self)
    def commit(self): pass

    def execute(self, statement, params=None):
        sql, p = str(statement), params or {}
        if 'pg_try_advisory_lock' in sql: return Result(self.held)
        if 'pg_advisory_unlock' in sql: return Result(True)
        if 'INSERT INTO market.research_refresh_attempts' in sql:
            self.events.append(dict(p, details=json.loads(p['details']), observed_at=NOW))
        elif 'INSERT INTO market.research_freshness' in sql:
            previous = self.states.get(p['source'], {})
            self.states[p['source']] = dict(source=p['source'], checked_at=p['now'],
                last_success_at=p['success'] or previous.get('last_success_at'), status=p['status'], details=json.loads(p['details']))
        elif 'FROM market.research_freshness' in sql:
            return Result(rows=[self.states[p['source']]] if p.get('source') in self.states else [] if p else self.states.values())
        elif "a.phase='acquired'" in sql:
            for e in reversed(self.events):
                if e['source'] == p['source'] and e['phase'] == 'acquired' and not any(x['run'] == e['run'] and x['phase'] == 'finished' for x in self.events):
                    return Result(rows=[dict(manifest=e['details']['manifest'], run_id=e['run'])])
        elif "phase='finished'" in sql:
            return Result(rows=[e['details'] for e in self.events if e['source'] == p['source'] and e['phase'] == 'finished'])
        elif "phase='received'" in sql:
            return Result(next((e['observed_at'] for e in self.events if e['source'] == p['source'] and e['phase'] == 'received' and e['details']['sha256'] == p['sha']), None))
        return Result()


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = MemoryDB()
        self.pointer = dict(id='snapshot', edition='2020-01', sha256='a' * 64)
        self.imported = patch.object(f, 'imported', side_effect=lambda *args: self.pointer)
        self.imported.start()
        self.addCleanup(self.imported.stop)

    def manifest(self, payload=b'fixture'):
        folder = self.root / 'raw/research-refresh/jsa_ivi' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        path = folder / 'ivi.xlsx'
        path.write_bytes(payload)
        manifest = folder / 'manifest.json'
        manifest.write_text(json.dumps(dict(status='complete', files=[dict(path=str(path), bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(), reference_month='2020-01')])))
        return manifest

    def test_old_publication_with_recent_check_is_latest_available(self):
        r.save_state(self.db, 'jsa_ivi', 'latest_available', {}, NOW, True)
        row = next(row for row in f.report(self.db, NOW)['sources'] if row['source'] == 'jsa_ivi')
        self.assertEqual(row['status'], 'latest_available')
        self.assertEqual(row['imported_edition'], '2020-01')
        self.assertEqual(next(row for row in f.report(self.db, NOW + timedelta(days=2))['sources'] if row['source'] == 'jsa_ivi')['status'], 'check_overdue')

    def test_publisher_correction_same_edition_imports_changed_hash(self):
        importer = Mock(return_value=dict(status='complete', snapshot_id='snapshot'))
        with patch.object(r, 'acquire', side_effect=[self.manifest(), self.manifest(b'corrected')]), patch.object(r, 'promote', importer):
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW)
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW + timedelta(days=1))
        self.assertEqual(importer.call_count, 2)
        self.assertEqual(self.db.states['jsa_ivi']['status'], 'latest_available')

    def test_unchanged_hash_skips_import_retains_raw(self):
        first, second = self.manifest(), self.manifest()
        with patch.object(r, 'acquire', side_effect=[first, second]), patch.object(r, 'promote', return_value=dict(status='complete', snapshot_id='snapshot')) as promote:
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW)
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW + timedelta(days=1))
        self.assertEqual(promote.call_count, 1)
        self.assertTrue(first.exists() and second.exists())

    def test_crash_after_acquisition_resumes_without_network(self):
        run = uuid.uuid4()
        manifest = self.manifest()
        r.event(self.db, run, 'jsa_ivi', 'acquired', dict(manifest=str(manifest)))
        with patch.object(r, 'acquire') as acquire, patch.object(r, 'promote', return_value=dict(status='unchanged', snapshot_id='snapshot')):
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW)
        acquire.assert_not_called()
        self.assertIsNone(r.resume_manifest(self.db, 'jsa_ivi'))
        self.assertEqual(self.db.states['jsa_ivi']['status'], 'latest_available')

    def test_failed_promotion_isolated_next_source_and_no_false_success(self):
        with patch.object(r, 'acquire', return_value=self.manifest()), patch.object(r, 'promote', side_effect=[ValueError('synthetic failure'), dict(status='complete', snapshot_id='snapshot')]):
            result = r.refresh(self.db, self.root, ['jsa_ivi', 'jsa_tnv'], NOW)
        self.assertEqual([s['status'] for s in result['sources']], ['failed', 'latest_available'])
        self.assertIsNone(self.db.states['jsa_ivi']['last_success_at'])
        self.assertEqual(self.db.states['jsa_ivi']['details']['imported_edition'], '2020-01')
        self.assertEqual(self.pointer['id'], 'snapshot')

    def test_import_not_promoted_cannot_be_reported_latest(self):
        with patch.object(r, 'acquire', return_value=self.manifest()), patch.object(r, 'promote', return_value=dict(status='complete', snapshot_id='older')):
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW)
        self.assertEqual(self.db.states['jsa_ivi']['status'], 'blocked')

    def test_corrupt_recovery_never_imported(self):
        path = self.manifest()
        path.with_name('ivi.xlsx').write_bytes(b'tampered')
        r.event(self.db, uuid.uuid4(), 'jsa_ivi', 'acquired', dict(manifest=str(path)))
        with patch.object(r, 'promote') as promote:
            r.refresh(self.db, self.root, ['jsa_ivi'], NOW)
        promote.assert_not_called()
        self.assertEqual(self.db.states['jsa_ivi']['status'], 'failed')

    def test_recovery_rejects_changed_normalised_bundle(self):
        path = self.manifest()
        bundle = path.parent / 'research.json'
        bundle.write_bytes(b'original')
        data = json.loads(path.read_text())
        data.update(bundle=str(bundle), bundle_bytes=8, bundle_sha256=r.file_hash(bundle))
        path.write_text(json.dumps(data))
        r.inspect_manifest(path, self.root / 'raw')
        bundle.write_bytes(b'modified')
        with self.assertRaises(ValueError):
            r.inspect_manifest(path, self.root / 'raw')

    def test_manual_sources_cannot_request_network(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')) as network:
            result = r.refresh(self.db, self.root, f.MANUAL, NOW)
        network.assert_not_called()
        self.assertTrue(all(row['status'] == 'manual_refresh_needed' for row in result['sources']))

    def test_recent_existing_manual_import_seeds_due_date(self):
        self.pointer['imported_at'] = NOW - timedelta(days=3)
        r.refresh(self.db, self.root, ['acnc'], NOW)
        self.assertEqual(self.db.states['acnc']['status'], 'latest_available')
        self.assertEqual(self.db.states['acnc']['last_success_at'], NOW - timedelta(days=3))
        r.refresh(self.db, self.root, ['acnc'], NOW + timedelta(days=28))
        self.assertEqual(self.db.states['acnc']['status'], 'manual_refresh_needed')

    def test_manual_receipt_manifest_processed_without_deleting_inbox(self):
        folder = self.root / 'inbox/acnc'
        folder.mkdir(parents=True)
        original = folder / 'release.csv'
        original.write_bytes(b'Name,ABN\nSynthetic,123\n')
        with patch.object(r, 'promote', return_value=dict(status='complete', snapshot_id='snapshot')) as promote:
            r.refresh(self.db, self.root, ['acnc'], NOW)
            r.refresh(self.db, self.root, ['acnc'], NOW + timedelta(days=1))
        self.assertEqual(promote.call_count, 1)
        self.assertTrue(original.exists())
        state = self.db.states['acnc']
        self.assertEqual(state['last_success_at'], NOW)
        self.assertTrue(f.due('acnc', state, NOW + timedelta(days=31)))
        manifest = next((self.root / 'raw').rglob('manifest.json'))
        item = json.loads(manifest.read_text())['files'][0]
        self.assertEqual(item['retrieved_at'], NOW.isoformat())
        self.assertEqual(item['licence'], 'Creative Commons Attribution 3.0 Australia')
        self.assertEqual(item['source_url'].split('/')[2], 'data.gov.au')

    def test_inbox_invalid_names_types_and_html(self):
        for name, body in [('bad.exe', b'a,b\n'), ('release.csv', b'<html>challenge</html>'), ('bad$.csv', b'a,b\n')]:
            path = self.root / name
            path.write_bytes(body)
            with self.assertRaises(ValueError): r.validate_inbox_file(path, 'acnc')

    def test_directory_inbox_contract_is_xml(self):
        path = self.root / 'directory.xml'
        path.write_bytes(b'<?xml version="1.0"?><response><item/></response>')
        r.validate_inbox_file(path, 'government_directory')
        with self.assertRaises(ValueError): r.validate_inbox_file(path, 'agor')

    def test_abn_multipart_requires_exact_ready_hashes(self):
        import zipfile
        folder = self.root / 'inbox/abn'
        folder.mkdir(parents=True)
        path = folder / 'release.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('extract.xml', '<ABR/>')
        with self.assertRaises(r.AcquisitionError):
            r.manual_acquire(self.db, 'abn', self.root, self.root / 'raw', NOW)
        (folder / 'READY').write_text(json.dumps({path.name: r.file_hash(path)}))
        manifest = r.manual_acquire(self.db, 'abn', self.root, self.root / 'raw', NOW)
        self.assertTrue(manifest.exists())
        self.assertTrue(path.exists())
        (folder / 'READY').write_text('{}')
        with self.assertRaises(ValueError):
            r.manual_acquire(self.db, 'abn', self.root, self.root / 'raw', NOW)

    def test_aged_care_imported_status_is_accepted(self):
        with patch.object(r, 'acquire', return_value=self.manifest()), patch.object(r, 'promote', return_value=dict(status='imported', snapshot_id='snapshot')):
            r.refresh(self.db, self.root, ['aged_care_star_ratings'], NOW)
        self.assertEqual(self.db.states['aged_care_star_ratings']['status'], 'latest_available')

    def test_database_lock_busy_does_no_acquisition(self):
        self.db.held = False
        with patch.object(r, 'acquire') as acquire:
            self.assertEqual(r.refresh(self.db, self.root, ['jsa_ivi'], NOW)['status'], 'busy')
            acquire.assert_not_called()

    def test_robots_and_waf_recorded_as_blocked(self):
        for message, reason in [('robots.txt disallows resource', 'robots'), ('Publisher WAF challenge', 'waf')]:
            with patch.object(r, 'acquire', side_effect=r.AcquisitionError(message)):
                r.refresh(self.db, self.root, ['jsa_ivi'], NOW)
            self.assertEqual(self.db.states['jsa_ivi']['details']['reason'], reason)

    def test_manual_import_failure_remains_visible_in_report(self):
        r.save_state(self.db, 'abn', 'failed', {'reason': 'SourceError'}, NOW)
        row = next(row for row in f.report(self.db, NOW)['sources'] if row['source'] == 'abn')
        self.assertEqual(row['status'], 'failed')


if __name__ == '__main__': unittest.main()
