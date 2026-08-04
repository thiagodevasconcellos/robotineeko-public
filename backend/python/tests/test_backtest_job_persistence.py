import tempfile
import unittest
from pathlib import Path

from backend.python.app_state import state
from backend.python.services import workspace_store
from backend.python.strategy_backend import (
    BACKTEST_JOB_INTERRUPTED_ERROR,
    _build_backtest_job_payload,
    _load_backtest_job_from_store,
    _purge_expired_runtime_backtest_jobs,
)


class BacktestJobPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.original_db_path = workspace_store.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        workspace_store.DB_PATH = Path(self.temp_dir.name) / 'workspace-test.db'
        workspace_store.ensure_workspace_store()
        state.backtest_jobs.jobs.clear()
        state.backtest_jobs.job_threads.clear()
        state.backtest_jobs.sequence = 0
        state.backtest_jobs.last_job_id = None
        state.backtest_jobs.last_run_at = None
        state.backtest_jobs.last_error = None

    def tearDown(self):
        workspace_store.DB_PATH = self.original_db_path
        state.backtest_jobs.jobs.clear()
        state.backtest_jobs.job_threads.clear()
        self.temp_dir.cleanup()

    def test_workspace_backtest_job_crud_and_expiry(self):
        created = workspace_store.create_workspace_backtest_job(
            'user-1',
            'default',
            job_id='btjob_1',
            request={'symbol': 'EURUSD', 'timeframe': 'M15'},
            created_at=100.0,
        )

        self.assertEqual(created['id'], 'btjob_1')
        self.assertEqual(created['status'], 'queued')
        self.assertEqual(created['request']['symbol'], 'EURUSD')
        self.assertIsNone(created['expires_at'])

        running = workspace_store.update_workspace_backtest_job(
            'user-1',
            'default',
            'btjob_1',
            status='running',
            progress=0.35,
            phase='running_backtest',
            phase_label='Running backtest',
            detail='Executing strategy on the isolated market snapshot.',
            started_at=120.0,
        )
        self.assertEqual(running['status'], 'running')
        self.assertAlmostEqual(running['progress'], 0.35)
        self.assertEqual(running['phase'], 'running_backtest')
        self.assertIsNone(running['expires_at'])

        completed = workspace_store.update_workspace_backtest_job(
            'user-1',
            'default',
            'btjob_1',
            status='completed',
            progress=1.0,
            phase='completed',
            phase_label='Completed',
            detail='Backtest completed in the backend job runner.',
            result={'status': 'ok', 'rows': 42},
            finished_at=180.0,
        )
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['result']['rows'], 42)
        self.assertAlmostEqual(
            completed['expires_at'],
            180.0 + workspace_store.BACKTEST_JOB_TERMINAL_RETENTION_SECONDS,
        )

        not_yet_expired = workspace_store.purge_expired_workspace_backtest_jobs(
            'user-1',
            'default',
            now=completed['expires_at'] - 1,
        )
        self.assertEqual(not_yet_expired['deleted'], 0)
        self.assertIsNotNone(workspace_store.get_workspace_backtest_job('user-1', 'default', 'btjob_1'))

        expired = workspace_store.purge_expired_workspace_backtest_jobs(
            'user-1',
            'default',
            now=completed['expires_at'] + 1,
        )
        self.assertEqual(expired['deleted'], 1)
        self.assertIsNone(workspace_store.get_workspace_backtest_job('user-1', 'default', 'btjob_1'))

    def test_load_backtest_job_from_store_marks_orphaned_active_job_failed(self):
        workspace_store.create_workspace_backtest_job(
            'user-1',
            'default',
            job_id='btjob_orphan',
            request={'symbol': 'EURUSD'},
            created_at=50.0,
        )
        workspace_store.update_workspace_backtest_job(
            'user-1',
            'default',
            'btjob_orphan',
            status='running',
            progress=0.4,
            phase='running_backtest',
            phase_label='Running backtest',
            detail='Executing strategy on the isolated market snapshot.',
            started_at=55.0,
        )

        recovered = _load_backtest_job_from_store('user-1', 'default', 'btjob_orphan')

        self.assertEqual(recovered['status'], 'failed')
        self.assertEqual(recovered['phase'], 'failed')
        self.assertEqual(recovered['error'], BACKTEST_JOB_INTERRUPTED_ERROR)
        self.assertEqual(recovered['detail'], BACKTEST_JOB_INTERRUPTED_ERROR)
        self.assertIsNotNone(recovered['finished_at'])
        self.assertIsNotNone(recovered['expires_at'])

    def test_runtime_cleanup_drops_only_expired_terminal_jobs(self):
        ttl = workspace_store.BACKTEST_JOB_TERMINAL_RETENTION_SECONDS
        state.backtest_jobs.jobs['btjob_expired'] = {
            'id': 'btjob_expired',
            'status': 'completed',
            'finished_at': 10.0,
        }
        state.backtest_jobs.jobs['btjob_recent'] = {
            'id': 'btjob_recent',
            'status': 'completed',
            'finished_at': 100.0,
        }
        state.backtest_jobs.jobs['btjob_live'] = {
            'id': 'btjob_live',
            'status': 'running',
            'finished_at': None,
        }
        state.backtest_jobs.job_threads['btjob_expired'] = object()
        state.backtest_jobs.job_threads['btjob_recent'] = object()

        removed = _purge_expired_runtime_backtest_jobs(now=10.0 + ttl + 1.0)

        self.assertEqual(removed, ['btjob_expired'])
        self.assertNotIn('btjob_expired', state.backtest_jobs.jobs)
        self.assertNotIn('btjob_expired', state.backtest_jobs.job_threads)
        self.assertIn('btjob_recent', state.backtest_jobs.jobs)
        self.assertIn('btjob_live', state.backtest_jobs.jobs)

    def test_workspace_backtest_job_list_can_filter_without_loading_payloads(self):
        workspace_store.create_workspace_backtest_job(
            'user-1',
            'default',
            job_id='btjob_completed',
            request={'symbol': 'EURUSD'},
            status='completed',
            progress=1.0,
            phase='completed',
            phase_label='Completed',
            detail='Done.',
            result={'status': 'ok', 'rows': 10},
            created_at=200.0,
            finished_at=210.0,
        )
        workspace_store.create_workspace_backtest_job(
            'user-1',
            'default',
            job_id='btjob_running',
            request={'symbol': 'USDJPY'},
            status='running',
            progress=0.5,
            phase='running_backtest',
            phase_label='Running backtest',
            detail='Executing.',
            created_at=220.0,
        )

        completed_only = workspace_store.list_workspace_backtest_jobs(
            'user-1',
            'default',
            limit=10,
            statuses=['completed'],
        )

        self.assertEqual(len(completed_only), 1)
        self.assertEqual(completed_only[0]['id'], 'btjob_completed')
        self.assertFalse(completed_only[0]['request_loaded'])
        self.assertFalse(completed_only[0]['result_loaded'])

    def test_backtest_job_payload_summarizes_heavy_results_for_console_delivery(self):
        heavy_job = {
            'id': 'btjob_heavy',
            'status': 'completed',
            'progress': 1.0,
            'phase': 'completed',
            'phase_label': 'Completed',
            'detail': 'Done.',
            'result': {
                'status': 'ok',
                'rows': 100,
                'results': [
                    {'time': index, 'trade_net_pnl': 0.0, 'trade_cost': 0.0, 'long_entry_flag': 0, 'short_entry_flag': 0, 'long_exit_flag': 0, 'short_exit_flag': 0}
                    for index in range(100)
                ],
                'stats': {
                    'account_balance_series': list(range(5005)),
                    'drawdown_amount_series': list(range(5005)),
                },
                'trade_markers': [{'id': 'm1'}],
            },
        }
        heavy_job['result']['results'][10]['long_entry_flag'] = 1
        heavy_job['result']['results'][20]['short_exit_flag'] = 1
        heavy_job['result']['results'][20]['trade_cost'] = 12.5

        payload = _build_backtest_job_payload(heavy_job, include_result=True)

        self.assertEqual(payload['id'], 'btjob_heavy')
        self.assertTrue(payload['result']['summary_only'])
        self.assertEqual(payload['result']['full_result_rows'], 100)
        self.assertEqual(len(payload['result']['results']), 2)
        self.assertLessEqual(len(payload['result']['stats']['account_balance_series']), 4000)
        self.assertLessEqual(len(payload['result']['stats']['drawdown_amount_series']), 4000)


if __name__ == '__main__':
    unittest.main()
