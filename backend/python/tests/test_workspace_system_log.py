import tempfile
import unittest
from pathlib import Path

from backend.python.services import workspace_store


class WorkspaceSystemLogStoreTest(unittest.TestCase):
    def setUp(self):
        self.original_db_path = workspace_store.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        workspace_store.DB_PATH = Path(self.temp_dir.name) / 'workspace-test.db'
        workspace_store.ensure_workspace_store()

    def tearDown(self):
        workspace_store.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_append_entries_creates_active_session_and_normalizes_timestamps(self):
        appended = workspace_store.append_workspace_system_log_entries(
            'user-1',
            'default',
            entries=[
                {
                    'client_entry_id': 'entry-1',
                    'message': 'Strategy · Applied successfully.',
                    'level': 'success',
                    'source': 'console_ui',
                    'scope': 'strategy',
                    'category': 'lifecycle',
                    'context': {'request_id': 'req-1'},
                    'created_at': 1_710_000_000_123,
                },
            ],
        )

        self.assertEqual(appended['session']['status'], 'active')
        self.assertEqual(len(appended['entries']), 1)
        self.assertEqual(appended['entries'][0]['client_entry_id'], 'entry-1')
        self.assertAlmostEqual(appended['entries'][0]['created_at'], 1_710_000_000.123, places=3)
        self.assertEqual(appended['entries'][0]['context']['request_id'], 'req-1')

        entries = workspace_store.list_workspace_system_log_entries(
            'user-1',
            'default',
            appended['session']['id'],
            limit=20,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['client_entry_id'], 'entry-1')

    def test_append_entries_deduplicates_by_client_entry_id(self):
        first = workspace_store.append_workspace_system_log_entries(
            'user-1',
            'default',
            entries=[
                {
                    'client_entry_id': 'entry-1',
                    'message': 'Batch · Started backend batch.',
                },
            ],
        )

        second = workspace_store.append_workspace_system_log_entries(
            'user-1',
            'default',
            session_id=first['session']['id'],
            entries=[
                {
                    'client_entry_id': 'entry-1',
                    'message': 'Batch · Started backend batch.',
                },
            ],
        )

        entries = workspace_store.list_workspace_system_log_entries(
            'user-1',
            'default',
            first['session']['id'],
            limit=20,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(second['entries'][0]['client_entry_id'], 'entry-1')

    def test_start_new_session_archives_previous_active_session(self):
        first = workspace_store.append_workspace_system_log_entries(
            'user-1',
            'default',
            entries=[
                {
                    'client_entry_id': 'entry-1',
                    'message': 'Research · Queued backend batch.',
                },
            ],
        )

        started = workspace_store.start_workspace_system_log_session(
            'user-1',
            'default',
            label='System log · follow-up',
            source='test-suite',
            metadata={'reason': 'manual_start'},
        )

        self.assertEqual(started['archived_session_ids'], [first['session']['id']])
        self.assertEqual(started['session']['status'], 'active')
        self.assertEqual(started['session']['label'], 'System log · follow-up')

        sessions = workspace_store.list_workspace_system_log_sessions('user-1', 'default', limit=10)
        self.assertEqual(len(sessions), 2)
        archived = next(session for session in sessions if session['id'] == first['session']['id'])
        self.assertEqual(archived['status'], 'archived')
        self.assertIsNotNone(archived['closed_at'])

    def test_purge_workspace_system_log_removes_entries_and_sessions(self):
        first = workspace_store.append_workspace_system_log_entries(
            'guest-user',
            'default',
            entries=[
                {
                    'client_entry_id': 'guest-entry-1',
                    'message': 'Backtester · Guest demo stale error.',
                    'level': 'error',
                },
            ],
        )
        workspace_store.append_workspace_system_log_entries(
            'other-user',
            'default',
            entries=[
                {
                    'client_entry_id': 'owner-entry-1',
                    'message': 'Owner log remains.',
                },
            ],
        )

        deleted = workspace_store.purge_workspace_system_log('guest-user', 'default')

        self.assertEqual(deleted['entries_deleted'], 1)
        self.assertEqual(deleted['sessions_deleted'], 1)
        self.assertEqual(
            workspace_store.list_workspace_system_log_entries(
                'guest-user',
                'default',
                first['session']['id'],
                limit=20,
            ),
            [],
        )
        self.assertEqual(len(workspace_store.list_workspace_system_log_sessions('other-user', 'default', limit=10)), 1)


if __name__ == '__main__':
    unittest.main()
