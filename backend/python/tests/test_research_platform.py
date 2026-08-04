import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.python.app_state import state
from backend.python.services import research_service
from backend.python.services import workspace_store
from backend.python.strategy_backend import (
    build_walkforward_train_test_pairs,
    ensure_market_regime_indicator_payload,
    normalize_research_chart_context,
)
from backend.python import workspace_backend


class ResearchPlatformTest(unittest.TestCase):
    def setUp(self):
        self.original_db_path = workspace_store.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        workspace_store.DB_PATH = Path(self.temp_dir.name) / 'workspace-test.db'
        workspace_store.ensure_workspace_store()
        state.research.active_jobs.clear()
        state.research.active_batches.clear()
        state.research.job_threads.clear()
        state.research.runtime_heartbeat_threads.clear()
        state.research.recent_events = []
        state.research.last_run_at = None
        state.research.last_error = None

    def tearDown(self):
        workspace_store.DB_PATH = self.original_db_path
        state.research.active_jobs.clear()
        state.research.active_batches.clear()
        state.research.job_threads.clear()
        state.research.runtime_heartbeat_threads.clear()
        self.temp_dir.cleanup()

    def test_workspace_research_run_crud(self):
        created = workspace_store.create_workspace_research_run(
            'user-1',
            'default',
            run_type='walkforward_study',
            side='long',
            run_name='walkforward study · long',
            version='v1',
            best_id='preset-a',
            best_label='Preset A',
            comparison_count=3,
            run_label='WF Long',
            run_notes='Initial run',
            pinned=True,
            payload={'foo': 'bar'},
        )

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['type'], 'walkforward_study')
        self.assertEqual(created['side'], 'long')
        self.assertEqual(created['run_name'], 'walkforward study · long')

        runs = workspace_store.list_workspace_research_runs('user-1', 'default', limit=20)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['id'], created['id'])
        self.assertEqual(runs[0]['payload']['foo'], 'bar')
        self.assertEqual(runs[0]['best_label'], 'Preset A')
        self.assertEqual(runs[0]['run_label'], 'WF Long')
        self.assertEqual(runs[0]['run_notes'], 'Initial run')
        self.assertTrue(runs[0]['pinned'])
        self.assertTrue(created['payload_loaded'])
        self.assertGreater(created['payload_size_bytes'], 0)

        updated = workspace_store.update_workspace_research_run(
            'user-1',
            'default',
            created['id'],
            run_label='WF Long Updated',
            run_notes='Updated note',
            pinned=False,
        )
        self.assertEqual(updated['run_label'], 'WF Long Updated')
        self.assertEqual(updated['run_notes'], 'Updated note')
        self.assertFalse(updated['pinned'])

        deleted = workspace_store.delete_workspace_research_run('user-1', 'default', created['id'])
        self.assertEqual(deleted['id'], created['id'])

        runs_after_delete = workspace_store.list_workspace_research_runs('user-1', 'default', limit=20)
        self.assertEqual(runs_after_delete, [])

    def test_workspace_saved_portfolio_crud(self):
        created = workspace_store.create_workspace_saved_portfolio(
            'user-1',
            'default',
            label='Carteira Londres',
            source='manual',
            notes='Primeira carteira',
            is_favorite=True,
            capital_model={'initialBalance': 10000},
            portfolio={
                'id': 'portfolio-london',
                'label': 'Carteira Londres',
                'enabled': True,
                'capitalMode': 'equity_percent',
                'capitalValue': 0.4,
                'rebalanceMode': 'static',
                'pipelines': [
                    {
                        'id': 'pipeline-1',
                        'label': 'London',
                        'enabled': True,
                        'portfolioMode': 'parallel_sleeves',
                        'entries': [
                            {
                                'id': 'entry-1',
                                'label': 'Setup A',
                                'enabled': True,
                                'symbol': 'USDSEK',
                                'timeframe': 'M15',
                                'volumeMode': 'fixed_volume',
                                'fixedVolume': 0.02,
                                'strategy': {'long': {}, 'short': {}, 'other': {}},
                            },
                        ],
                    },
                ],
            },
        )

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['label'], 'Carteira Londres')
        self.assertTrue(created['is_favorite'])
        self.assertEqual(created['capitalModel']['initialBalance'], 10000)
        self.assertEqual(created['portfolio']['pipelines'][0]['entries'][0]['symbol'], 'USDSEK')

        rows = workspace_store.list_workspace_saved_portfolios('user-1', 'default', limit=20)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], created['id'])
        self.assertEqual(rows[0]['portfolio']['pipelines'][0]['label'], 'London')

        updated = workspace_store.update_workspace_saved_portfolio(
            'user-1',
            'default',
            created['id'],
            label='Carteira Londres v2',
            notes='Atualizada',
            is_favorite=False,
            portfolio={
                'id': 'portfolio-london',
                'label': 'Carteira Londres v2',
                'enabled': True,
                'capitalMode': 'fixed_amount',
                'capitalValue': 2500,
                'rebalanceMode': 'static',
                'pipelines': [
                    {
                        'id': 'pipeline-1',
                        'label': 'London v2',
                        'enabled': True,
                        'portfolioMode': 'shared_pipe',
                        'entries': [],
                    },
                ],
            },
        )
        self.assertEqual(updated['label'], 'Carteira Londres v2')
        self.assertFalse(updated['is_favorite'])
        self.assertEqual(updated['portfolio']['capitalMode'], 'fixed_amount')
        self.assertEqual(updated['portfolio']['pipelines'][0]['portfolioMode'], 'shared_pipe')

        deleted = workspace_store.delete_workspace_saved_portfolio('user-1', 'default', created['id'])
        self.assertEqual(deleted['id'], created['id'])
        self.assertEqual(
            workspace_store.list_workspace_saved_portfolios('user-1', 'default', limit=20),
            [],
        )

    def test_workspace_research_run_summary_and_detail_reads(self):
        created = workspace_store.create_workspace_research_run(
            'user-1',
            'default',
            run_type='strategy_pipeline',
            side=None,
            run_name='summary test',
            version='v1',
            best_id='candidate-a',
            best_label='Candidate A',
            comparison_count=4,
            payload={
                'status': 'ok',
                'pipeline': {
                    'label': 'Summary test',
                    'results': [{'time': i, 'pnl': i * 2} for i in range(5)],
                },
            },
        )

        summary_runs = workspace_store.list_workspace_research_runs(
            'user-1',
            'default',
            limit=20,
            include_payload=False,
        )
        self.assertEqual(len(summary_runs), 1)
        self.assertFalse(summary_runs[0]['payload_loaded'])
        self.assertGreater(summary_runs[0]['payload_size_bytes'], 0)
        self.assertNotIn('payload', summary_runs[0])

        detail = workspace_store.get_workspace_research_run('user-1', 'default', created['id'])
        self.assertTrue(detail['payload_loaded'])
        self.assertEqual(detail['payload']['pipeline']['label'], 'Summary test')
        self.assertEqual(len(detail['payload']['pipeline']['results']), 5)

        detail_summary = workspace_store.get_workspace_research_run(
            'user-1',
            'default',
            created['id'],
            include_payload=False,
        )
        self.assertFalse(detail_summary['payload_loaded'])
        self.assertNotIn('payload', detail_summary)

    def test_workspace_research_run_preserves_portfolio_analytics_payload(self):
        created = workspace_store.create_workspace_research_run(
            'user-1',
            'default',
            run_type='preset_compare',
            side=None,
            run_name='portfolio compare',
            version='v1',
            best_id='portfolio-a',
            best_label='Portfolio A',
            comparison_count=2,
            payload={
                'status': 'ok',
                'comparisons': [
                    {
                        'id': 'portfolio-a',
                        'label': 'Portfolio A',
                        'summary': {
                            'net_pnl': 12.0,
                            'portfolio_analytics': {
                                'max_concurrent_strategies': 2,
                                'pairwise': [
                                    {'left_strategy_id': 'a', 'right_strategy_id': 'b', 'correlation': -0.4},
                                ],
                            },
                        },
                    },
                ],
            },
        )

        runs = workspace_store.list_workspace_research_runs('user-1', 'default', limit=20)
        self.assertEqual(runs[0]['id'], created['id'])
        self.assertEqual(
            runs[0]['payload']['comparisons'][0]['summary']['portfolio_analytics']['pairwise'][0]['left_strategy_id'],
            'a',
        )

    def test_workspace_research_job_crud(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
            run_label='Night batch',
            run_notes='First queued job',
        )

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['job_type'], 'preset_compare')
        self.assertEqual(created['status'], 'queued')
        self.assertEqual(created['run_label'], 'Night batch')
        self.assertEqual(created['request']['presets'][0]['id'], 'a')

        listed = workspace_store.list_workspace_research_jobs('user-1', 'default', limit=20)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], created['id'])

        updated = workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.4,
            phase='compare',
            phase_label='Preset Compare',
            detail='Running comparison',
            cancel_requested=True,
        )
        self.assertEqual(updated['status'], 'running')
        self.assertAlmostEqual(updated['progress'], 0.4)
        self.assertEqual(updated['phase'], 'compare')
        self.assertTrue(updated['cancel_requested'])

        deleted = workspace_store.delete_workspace_research_job('user-1', 'default', created['id'])
        self.assertEqual(deleted['id'], created['id'])

        listed_after_delete = workspace_store.list_workspace_research_jobs('user-1', 'default', limit=20)
        self.assertEqual(listed_after_delete, [])

    def test_workspace_research_job_summary_and_detail_reads(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={
                'id': 'pipeline-1',
                'label': 'Pipeline 1',
                'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 900},
                'strategy': {'long': {'openIf': 'close[0] > open[0]'}},
                'backtest': {'initialBalance': 10000},
                'researchPlan': {'kind': 'preset_compare'},
            },
            run_label='Pipeline 1',
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='completed',
            result={
                'status': 'ok',
                'job_type': 'strategy_pipeline',
                'pipeline': {
                    'label': 'Pipeline 1',
                    'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 900},
                    'request': {
                        'strategy': {'long': {'openIf': 'close[0] > open[0]'}},
                        'backtest': {'initialBalance': 10000},
                    },
                    'stats': {'net_pnl': 12.5, 'win_rate': 0.61},
                    'results': [{'pnl': 1.0}],
                },
                'research': {'status': 'ok'},
            },
        )

        summary_jobs = workspace_store.list_workspace_research_jobs(
            'user-1',
            'default',
            limit=20,
            include_payload=False,
        )
        self.assertEqual(len(summary_jobs), 1)
        self.assertTrue(summary_jobs[0]['request_loaded'])
        self.assertFalse(summary_jobs[0]['result_loaded'])
        self.assertGreater(summary_jobs[0]['request_size_bytes'], 0)
        self.assertGreater(summary_jobs[0]['result_size_bytes'], 0)
        self.assertEqual(summary_jobs[0]['request']['chart']['symbol'], 'EURUSD')
        self.assertEqual(summary_jobs[0]['result']['pipeline']['chart']['timeframe'], 'M15')
        self.assertEqual(summary_jobs[0]['result']['pipeline']['stats']['net_pnl'], 12.5)
        self.assertNotIn('request', summary_jobs[0]['result']['pipeline'])

        detail = workspace_store.get_workspace_research_job('user-1', 'default', created['id'])
        self.assertTrue(detail['result_loaded'])
        self.assertEqual(detail['result']['pipeline']['request']['backtest']['initialBalance'], 10000)
        self.assertEqual(len(detail['result']['pipeline']['results']), 1)

        detail_summary = workspace_store.get_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            include_payload=False,
        )
        self.assertFalse(detail_summary['result_loaded'])
        self.assertNotIn('request', detail_summary['result']['pipeline'])

    def test_touch_workspace_research_entities_updates_timestamp(self):
        created_job = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )
        created_batch = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Touch test batch',
            request={'jobs': []},
        )

        touched_job = workspace_store.touch_workspace_research_job(
            'user-1',
            'default',
            created_job['id'],
            updated_at=1234.5,
        )
        touched_batch = workspace_store.touch_workspace_research_batch(
            'user-1',
            'default',
            created_batch['id'],
            updated_at=2345.6,
        )

        self.assertEqual(touched_job['updated_at'], 1234.5)
        self.assertEqual(touched_batch['updated_at'], 2345.6)

    def test_workspace_research_batch_summary_and_detail_reads(self):
        created = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Night batch',
            request={
                'jobs': [
                    {'job_type': 'strategy_pipeline', 'id': 'seed-1', 'label': 'Seed 1'},
                ],
            },
        )
        workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            created['id'],
            status='completed',
            result={
                'jobs': [
                    {
                        'job_id': 11,
                        'run_label': 'Seed 1',
                        'status': 'completed',
                        'run_id': 22,
                        'benchmark_id': 33,
                        'benchmark_label': 'Seed benchmark',
                        'detail': 'Completed',
                        'error': '',
                        'result': {
                            'pipeline': {
                                'chart': {'symbol': 'EURUSD', 'timeframe': 'M15'},
                                'stats': {'net_pnl': 12.5},
                            },
                        },
                    },
                ],
            },
        )

        summary_batches = workspace_store.list_workspace_research_batches(
            'user-1',
            'default',
            limit=20,
            include_payload=False,
        )
        self.assertEqual(len(summary_batches), 1)
        self.assertTrue(summary_batches[0]['request_loaded'])
        self.assertFalse(summary_batches[0]['result_loaded'])
        self.assertGreater(summary_batches[0]['request_size_bytes'], 0)
        self.assertGreater(summary_batches[0]['result_size_bytes'], 0)
        self.assertEqual(summary_batches[0]['request']['jobs'][0]['label'], 'Seed 1')
        self.assertEqual(summary_batches[0]['result']['jobs'][0]['job_id'], 11)
        self.assertEqual(summary_batches[0]['result']['jobs'][0]['status'], 'completed')
        self.assertNotIn('result', summary_batches[0]['result']['jobs'][0])

        detail = workspace_store.get_workspace_research_batch('user-1', 'default', created['id'])
        self.assertTrue(detail['result_loaded'])
        self.assertEqual(detail['result']['jobs'][0]['result']['pipeline']['stats']['net_pnl'], 12.5)

        detail_summary = workspace_store.get_workspace_research_batch(
            'user-1',
            'default',
            created['id'],
            include_payload=False,
        )
        self.assertFalse(detail_summary['result_loaded'])
        self.assertNotIn('result', detail_summary['result']['jobs'][0])

    def test_workspace_research_job_preserves_portfolio_compare_request(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={
                'baseline': {
                    'id': 'baseline',
                    'label': 'Baseline',
                    'strategy': {'long': {'openIf': 'base'}},
                    'strategies': [
                        {
                            'id': 'helper-a',
                            'label': 'Helper A',
                            'priority': 1,
                            'enabled': True,
                            'strategy': {'short': {'openIf': 'helper'}},
                        },
                    ],
                },
                'presets': [
                    {
                        'id': 'candidate',
                        'label': 'Candidate',
                        'strategy': {'long': {'openIf': 'cand'}},
                        'strategies': [
                            {
                                'id': 'helper-b',
                                'label': 'Helper B',
                                'priority': 1,
                                'enabled': True,
                                'strategy': {'short': {'openIf': 'helper-b'}},
                            },
                        ],
                    },
                ],
            },
            run_label='Portfolio compare',
        )

        self.assertEqual(created['request']['baseline']['strategies'][0]['id'], 'helper-a')
        self.assertEqual(created['request']['presets'][0]['strategies'][0]['id'], 'helper-b')

    @patch('backend.python.services.research_service.create_workspace_research_run')
    @patch('backend.python.services.research_service.execute_preset_compare_request')
    @patch('backend.python.services.research_service.evaluate_strategy_request_in_context')
    def test_strategy_pipeline_reuses_pipeline_summary_as_compare_baseline(
        self,
        mock_evaluate_strategy,
        mock_execute_compare,
        mock_create_run,
    ):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={
                'label': 'Pipeline job',
                'chart': {
                    'symbol': 'EURUSD',
                    'timeframe': 'M15',
                    'bars': 6000,
                    'indicators': [{'name': 'EMA', 'params': ['close', 21]}],
                },
                'strategy': {'long': {'openIf': 'close[0] > open[0]'}},
                'backtest': {},
                'researchPlan': {
                    'kind': 'preset_compare',
                    'payload': {
                        'presets': [
                            {
                                'id': 'cand',
                                'label': 'Candidate',
                                'strategy': {'long': {'openIf': 'close[0] > high[1]'}},
                            },
                        ],
                    },
                },
            },
        )
        mock_evaluate_strategy.return_value = {
            'status': 'ok',
            'stats': {
                'net_pnl': 42.0,
                'expectancy_per_trade': 1.2,
                'max_drawdown': -5.0,
                'max_drawdown_pct': -0.5,
                'n_trades': 8,
                'strategy_count': 1,
            },
            'serialized_results': [],
            'strategy_view_meta': {},
            'applied_indicators': [],
            'available_columns': ['time', 'open', 'high', 'low', 'close', 'volume'],
            'available_column_details': [],
        }
        mock_execute_compare.return_value = {
            'status': 'ok',
            'baseline': {
                'id': 'baseline',
                'label': 'Baseline',
                'summary': {'net_pnl': 42.0},
            },
            'comparisons': [],
            'best_preset_id': None,
        }
        mock_create_run.return_value = {'id': 1}

        research_service._run_strategy_pipeline_job(
            'user-1',
            'default',
            int(created['id']),
            created['request'],
        )

        self.assertEqual(mock_execute_compare.call_count, 1)
        self.assertEqual(
            mock_execute_compare.call_args.kwargs.get('baseline_summary_override'),
            {
                'net_pnl': 42.0,
                'win_rate': None,
                'expectancy_per_trade': 1.2,
                'max_drawdown': -5.0,
                'max_drawdown_pct': -0.5,
                'n_trades': 8,
                'strategy_count': 1,
                'portfolio_event_counts': {},
                'portfolio_strategy_stats': [],
                'portfolio_analytics': {},
                'regime_summary': [],
                'regime_stability_summary': [],
            },
        )

    def test_research_job_runtime_feed_state_merges_into_api_response(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )
        running = workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.2,
            phase='research',
            phase_label='Research',
            detail='Running compare',
            started_at=100.0,
        )

        key = research_service._build_job_key('user-1', 'default', int(created['id']))
        state.research.job_threads[key] = type('AliveThread', (), {'is_alive': lambda self: True})()
        state.research.active_jobs[key] = {
            **running,
            'heartbeat_at': time.time(),
            'updated_at': time.time(),
        }

        hydrated = research_service.get_research_job('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'running')
        self.assertIn(hydrated['data_feed_status'], {'receiving', 'waiting'})
        self.assertIn('heartbeat_age_seconds', hydrated)
        self.assertIn('worker_alive', hydrated)

    def test_research_batch_runtime_feed_state_merges_into_api_response(self):
        created = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Night batch',
            request={'jobs': []},
        )
        running = workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.4,
            phase='running_job',
            phase_label='Running 1/1',
            detail='Executing child job 1 of 1.',
            started_at=100.0,
        )

        key = research_service._build_batch_key('user-1', 'default', int(created['id']))
        state.research.job_threads[key] = type('AliveThread', (), {'is_alive': lambda self: True})()
        state.research.active_batches[key] = {
            **running,
            'heartbeat_at': time.time(),
            'updated_at': time.time(),
        }

        hydrated = research_service.get_research_batch('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'running')
        self.assertIn(hydrated['data_feed_status'], {'receiving', 'waiting'})
        self.assertIn('heartbeat_age_seconds', hydrated)
        self.assertIn('worker_alive', hydrated)

    def test_research_feed_state_flags_stale_worker(self):
        payload = {
            'status': 'running',
            'phase': 'research',
            'started_at': time.time() - 60.0,
            'updated_at': time.time() - 40.0,
            'heartbeat_at': time.time() - 40.0,
        }

        feed_state = research_service._derive_research_feed_state(payload, worker_alive=False)
        self.assertEqual(feed_state['data_feed_status'], 'stale')
        self.assertTrue(feed_state['auto_sanitize_recommended'])

    def test_reconcile_auto_sanitizes_stale_research_job(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.5,
            phase='research',
            phase_label='Research',
            detail='Running compare',
            started_at=time.time() - 60.0,
        )

        key = research_service._build_job_key('user-1', 'default', int(created['id']))
        state.research.active_jobs[key] = {
            'id': created['id'],
            'status': 'running',
            'phase': 'research',
            'updated_at': time.time() - 40.0,
            'heartbeat_at': time.time() - 40.0,
            'started_at': time.time() - 60.0,
            'cancel_requested': False,
        }

        research_service._reconcile_stale_research_runtime('user-1', 'default')
        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'failed')
        self.assertIn('auto-sanitized', hydrated['detail'])

    def test_list_research_jobs_does_not_auto_sanitize_stale_runtime(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.5,
            phase='research',
            phase_label='Research',
            detail='Running compare',
            started_at=time.time() - 60.0,
        )

        key = research_service._build_job_key('user-1', 'default', int(created['id']))
        state.research.active_jobs[key] = {
            'id': created['id'],
            'status': 'running',
            'phase': 'research',
            'updated_at': time.time() - 40.0,
            'heartbeat_at': time.time() - 40.0,
            'started_at': time.time() - 60.0,
            'cancel_requested': False,
        }

        research_service.list_research_jobs('user-1', 'default')
        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'running')

    def test_reconcile_auto_sanitizes_stale_research_batch(self):
        created = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Night batch',
            request={'jobs': []},
        )
        workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.5,
            phase='running_job',
            phase_label='Running 1/1',
            detail='Executing child job 1 of 1.',
            started_at=time.time() - 60.0,
        )

        key = research_service._build_batch_key('user-1', 'default', int(created['id']))
        state.research.active_batches[key] = {
            'id': created['id'],
            'status': 'running',
            'phase': 'running_job',
            'updated_at': time.time() - 40.0,
            'heartbeat_at': time.time() - 40.0,
            'started_at': time.time() - 60.0,
            'cancel_requested': False,
            'total_jobs': 0,
            'result': {'jobs': []},
        }

        research_service._reconcile_stale_research_runtime('user-1', 'default')
        hydrated = workspace_store.get_workspace_research_batch('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'failed')
        self.assertIn('auto-sanitized', hydrated['detail'])

    def test_reconcile_keeps_recently_queued_research_job_during_startup_grace(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )

        research_service._reconcile_stale_research_runtime('user-1', 'default')
        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'queued')

    def test_reconcile_keeps_recently_queued_research_batch_during_startup_grace(self):
        created = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Queued batch',
            request={'jobs': [{'job_type': 'strategy_pipeline'}]},
        )

        research_service._reconcile_stale_research_runtime('user-1', 'default')
        hydrated = workspace_store.get_workspace_research_batch('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'queued')

    def test_reconcile_prefetches_research_snapshots_once_per_pass(self):
        child_job = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            child_job['id'],
            status='running',
            phase='starting',
            phase_label='Starting',
            detail='Spinning up worker.',
            started_at=time.time() - 2.0,
        )

        batch = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Prefetch batch',
            request={'jobs': [{'job_type': 'preset_compare'}]},
        )
        workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            batch['id'],
            status='running',
            phase='starting',
            phase_label='Starting',
            detail='Starting batch worker.',
            current_job_id=child_job['id'],
            started_at=time.time() - 2.0,
        )

        with patch.object(research_service, 'list_workspace_research_jobs', wraps=research_service.list_workspace_research_jobs) as mock_list_jobs:
            with patch.object(research_service, 'list_workspace_research_batches', wraps=research_service.list_workspace_research_batches) as mock_list_batches:
                research_service._reconcile_stale_research_runtime('user-1', 'default')

        self.assertEqual(mock_list_jobs.call_count, 1)
        self.assertEqual(mock_list_batches.call_count, 1)

    def test_reconcile_keeps_recently_updated_running_research_job_without_thread(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 600}},
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.08,
            phase='backtest',
            phase_label='Backtest',
            detail='Running isolated backtest.',
            started_at=time.time() - 3.0,
        )

        research_service._reconcile_stale_research_runtime('user-1', 'default')
        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'running')

    def test_explicit_reconcile_reports_changed_entities(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='preset_compare',
            request={'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            created['id'],
            status='running',
            progress=0.5,
            phase='research',
            phase_label='Research',
            detail='Running compare',
            started_at=time.time() - 60.0,
        )

        key = research_service._build_job_key('user-1', 'default', int(created['id']))
        state.research.active_jobs[key] = {
            'id': created['id'],
            'status': 'running',
            'phase': 'research',
            'updated_at': time.time() - 40.0,
            'heartbeat_at': time.time() - 40.0,
            'started_at': time.time() - 60.0,
            'cancel_requested': False,
        }

        summary = research_service.reconcile_research_runtime('user-1', 'default')
        self.assertEqual(summary['changed_job_count'], 1)
        self.assertEqual(summary['changed_jobs'][0]['id'], created['id'])
        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', created['id'])
        self.assertEqual(hydrated['status'], 'failed')

    def test_reconcile_marks_batch_failed_when_current_child_failed_and_worker_is_gone(self):
        created_batch = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Restarted batch',
            request={'jobs': [{'job_type': 'strategy_pipeline'}, {'job_type': 'strategy_pipeline'}]},
        )
        failed_child = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={'chart': {'symbol': 'EURUSD', 'timeframe': 'M5', 'bars': 1000}},
            run_label='02 · Retest Hold',
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            failed_child['id'],
            status='failed',
            progress=1.0,
            phase='failed',
            phase_label='Failed',
            detail='Research job was interrupted after backend restart.',
            error='Research worker was not running in this backend process.',
            finished_at=time.time() - 1.0,
        )
        workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            created_batch['id'],
            status='running',
            progress=0.125,
            phase='running_job',
            phase_label='Running 2/8',
            detail='Executing child job 2 of 8: 02 · Retest Hold.',
            total_jobs=8,
            completed_jobs=1,
            failed_jobs=0,
            cancelled_jobs=0,
            current_job_id=failed_child['id'],
            started_at=time.time() - 10.0,
        )

        key = research_service._build_batch_key('user-1', 'default', int(created_batch['id']))
        state.research.active_batches[key] = {
            'id': created_batch['id'],
            'status': 'running',
            'phase': 'running_job',
            'phase_label': 'Running 2/8',
            'detail': 'Executing child job 2 of 8: 02 · Retest Hold.',
            'updated_at': time.time(),
            'heartbeat_at': time.time(),
            'started_at': time.time() - 10.0,
            'cancel_requested': False,
            'current_job_id': failed_child['id'],
            'total_jobs': 8,
            'completed_jobs': 1,
            'failed_jobs': 0,
            'cancelled_jobs': 0,
            'result': {'jobs': []},
        }

        research_service._reconcile_stale_research_runtime('user-1', 'default')

        hydrated = workspace_store.get_workspace_research_batch('user-1', 'default', created_batch['id'])
        self.assertEqual(hydrated['status'], 'failed')
        self.assertEqual(hydrated['phase_label'], 'Failed')
        self.assertGreaterEqual(int(hydrated['failed_jobs'] or 0), 1)
        self.assertIn('backend restart', str(hydrated['detail'] or '').lower())

    def test_reconcile_keeps_batch_child_job_alive_when_batch_worker_is_alive(self):
        created_batch = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Owning batch',
            request={'jobs': [{'job_type': 'strategy_pipeline'}]},
        )
        child_job = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 600}},
            run_label='Job A',
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            child_job['id'],
            status='running',
            progress=0.1,
            phase='backtest',
            phase_label='Backtest',
            detail='Running isolated backtest.',
            started_at=time.time() - 3.0,
        )
        workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            created_batch['id'],
            status='running',
            progress=0.0,
            phase='running_job',
            phase_label='Running 1/1',
            detail='Executing child job 1 of 1.',
            current_job_id=child_job['id'],
            total_jobs=1,
            started_at=time.time() - 3.0,
        )

        batch_key = research_service._build_batch_key('user-1', 'default', int(created_batch['id']))
        state.research.active_batches[batch_key] = {
            'id': created_batch['id'],
            'status': 'running',
            'phase': 'running_job',
            'phase_label': 'Running 1/1',
            'detail': 'Executing child job 1 of 1.',
            'current_job_id': child_job['id'],
            'updated_at': time.time(),
            'heartbeat_at': time.time(),
            'started_at': time.time() - 3.0,
            'cancel_requested': False,
            'total_jobs': 1,
        }
        state.research.job_threads[batch_key] = type('AliveThread', (), {'is_alive': lambda self: True})()

        research_service._reconcile_stale_research_runtime('user-1', 'default')

        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', child_job['id'])
        self.assertEqual(hydrated['status'], 'running')

    def test_reconcile_defers_batch_child_job_finalization_to_parent_batch(self):
        created_batch = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Owning batch without runtime state',
            request={'jobs': [{'job_type': 'strategy_pipeline'}]},
        )
        child_job = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 600}},
            run_label='Job A',
        )
        workspace_store.update_workspace_research_job(
            'user-1',
            'default',
            child_job['id'],
            status='running',
            progress=0.1,
            phase='backtest',
            phase_label='Backtest',
            detail='Running isolated backtest.',
            started_at=time.time() - 10.0,
        )
        workspace_store.update_workspace_research_batch(
            'user-1',
            'default',
            created_batch['id'],
            status='running',
            progress=0.0,
            phase='running_job',
            phase_label='Running 1/1',
            detail='Executing child job 1 of 1.',
            current_job_id=child_job['id'],
            total_jobs=1,
            started_at=time.time() - 10.0,
        )

        research_service._reconcile_stale_research_runtime('user-1', 'default')

        hydrated = workspace_store.get_workspace_research_job('user-1', 'default', child_job['id'])
        self.assertEqual(hydrated['status'], 'running')

    def test_research_worker_registers_current_thread_for_batch_child_jobs(self):
        created = workspace_store.create_workspace_research_job(
            'user-1',
            'default',
            job_type='strategy_pipeline',
            request={'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 600}},
            run_label='Thread registration check',
        )
        job_key = research_service._build_job_key('user-1', 'default', int(created['id']))
        observed = {}
        original = research_service._run_strategy_pipeline_job

        def fake_run(user_id, workspace_id, job_id, request_payload):
            observed['registered_thread'] = state.research.job_threads.get(job_key)
            observed['current_thread'] = threading.current_thread()
            research_service._update_job(
                user_id,
                workspace_id,
                job_id,
                status='completed',
                progress=1.0,
                phase='completed',
                phase_label='Completed',
                detail='Synthetic pipeline finished.',
                finished_at=time.time(),
                result={'status': 'ok', 'job_type': 'strategy_pipeline', 'pipeline': {}, 'research': None},
            )

        research_service._run_strategy_pipeline_job = fake_run
        try:
            research_service._research_worker('strategy_pipeline', 'user-1', 'default', int(created['id']), created['request'])
        finally:
            research_service._run_strategy_pipeline_job = original

        self.assertIs(observed.get('registered_thread'), observed.get('current_thread'))

    def test_research_batch_worker_registers_child_job_thread_before_worker_call(self):
        created_batch = workspace_store.create_workspace_research_batch(
            'user-1',
            'default',
            label='Thread registration batch',
            request={'jobs': [{'job_type': 'strategy_pipeline'}]},
        )
        observed = {}
        original = research_service._research_worker

        def fake_research_worker(job_type, user_id, workspace_id, job_id, request_payload):
            job_key = research_service._build_job_key(user_id, workspace_id, int(job_id))
            observed['registered_thread'] = state.research.job_threads.get(job_key)
            observed['current_thread'] = threading.current_thread()
            research_service._update_job(
                user_id,
                workspace_id,
                int(job_id),
                status='completed',
                progress=1.0,
                phase='completed',
                phase_label='Completed',
                detail='Synthetic child job finished.',
                finished_at=time.time(),
            )

        research_service._research_worker = fake_research_worker
        try:
            research_service._research_batch_worker(
                'user-1',
                'default',
                int(created_batch['id']),
                {
                    'jobs': [
                        {
                            'job_type': 'strategy_pipeline',
                            'request': {
                                'id': 'job-a',
                                'label': 'Job A',
                                'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 600},
                                'strategy': {},
                                'backtest': {},
                            },
                            'run_label': 'Job A',
                            'run_notes': '',
                        },
                    ],
                },
            )
        finally:
            research_service._research_worker = original

        self.assertIs(observed.get('registered_thread'), observed.get('current_thread'))

    def test_workspace_research_campaign_crud(self):
        created = workspace_store.create_workspace_research_campaign(
            'user-1',
            'default',
            label='Nightly Suite',
            description='Runs the standard overnight research bundle.',
            request={
                'jobs': [
                    {
                        'job_type': 'preset_compare',
                        'request': {'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}]},
                        'run_label': 'Preset compare',
                        'run_notes': 'Campaign job',
                    },
                ],
            },
        )

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['label'], 'Nightly Suite')
        self.assertEqual(created['request']['jobs'][0]['job_type'], 'preset_compare')

        listed = workspace_store.list_workspace_research_campaigns('user-1', 'default', limit=20)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], created['id'])

        updated = workspace_store.update_workspace_research_campaign(
            'user-1',
            'default',
            created['id'],
            label='Nightly Suite v2',
            description='Updated campaign',
        )
        self.assertEqual(updated['label'], 'Nightly Suite v2')
        self.assertEqual(updated['description'], 'Updated campaign')

        deleted = workspace_store.delete_workspace_research_campaign('user-1', 'default', created['id'])
        self.assertEqual(deleted['id'], created['id'])

        listed_after_delete = workspace_store.list_workspace_research_campaigns('user-1', 'default', limit=20)
        self.assertEqual(listed_after_delete, [])

    def test_workspace_research_campaign_summary_and_detail_reads(self):
        created = workspace_store.create_workspace_research_campaign(
            'user-1',
            'default',
            label='Summary Campaign',
            description='Summary/detail coverage.',
            request={
                'jobs': [
                    {
                        'job_type': 'preset_compare',
                        'request': {
                            'presets': [{'id': 'a', 'label': 'A', 'strategy': {}}],
                        },
                    },
                ],
                'batch_jobs': [
                    {
                        'id': 'batch-job-1',
                        'label': 'Batch Job 1',
                        'chart': {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 600},
                        'strategy': {'long': {'openIf': 'True'}},
                    },
                ],
                'shared_features': [{'key': 'rsi', 'label': 'RSI'}],
                'options': {
                    'researchMode': 'preset_compare',
                    'barsOverride': 4000,
                },
            },
        )

        summary_campaigns = workspace_store.list_workspace_research_campaigns(
            'user-1',
            'default',
            limit=20,
            include_payload=False,
        )
        self.assertEqual(len(summary_campaigns), 1)
        self.assertFalse(summary_campaigns[0]['request_loaded'])
        self.assertGreater(summary_campaigns[0]['request_size_bytes'], 0)
        self.assertEqual(summary_campaigns[0]['job_count'], 1)
        self.assertEqual(summary_campaigns[0]['batch_job_count'], 1)
        self.assertEqual(summary_campaigns[0]['request']['options']['researchMode'], 'preset_compare')
        self.assertEqual(summary_campaigns[0]['request']['shared_features'][0]['key'], 'rsi')
        self.assertNotIn('jobs', summary_campaigns[0]['request'])
        self.assertNotIn('batch_jobs', summary_campaigns[0]['request'])

        detail = workspace_store.get_workspace_research_campaign('user-1', 'default', created['id'])
        self.assertTrue(detail['request_loaded'])
        self.assertEqual(detail['job_count'], 1)
        self.assertEqual(detail['batch_job_count'], 1)
        self.assertEqual(detail['request']['batch_jobs'][0]['label'], 'Batch Job 1')

        detail_summary = workspace_store.get_workspace_research_campaign(
            'user-1',
            'default',
            created['id'],
            include_payload=False,
        )
        self.assertFalse(detail_summary['request_loaded'])
        self.assertEqual(detail_summary['job_count'], 1)
        self.assertEqual(detail_summary['batch_job_count'], 1)
        self.assertNotIn('jobs', detail_summary['request'])
        self.assertNotIn('batch_jobs', detail_summary['request'])

    def test_workspace_research_campaign_preserves_portfolio_jobs(self):
        created = workspace_store.create_workspace_research_campaign(
            'user-1',
            'default',
            label='Portfolio Suite',
            description='Runs a portfolio preset compare.',
            request={
                'jobs': [
                    {
                        'job_type': 'strategy_pipeline',
                        'request': {
                            'id': 'portfolio-job',
                            'label': 'Portfolio Job',
                            'chart': {'symbol': 'EURUSD', 'timeframe': 'M1', 'bars': 4000},
                            'strategy': {'long': {'openIf': 'lead'}},
                            'strategies': [
                                {
                                    'id': 'helper-1',
                                    'label': 'Helper 1',
                                    'priority': 1,
                                    'enabled': True,
                                    'strategy': {'short': {'openIf': 'helper-1'}},
                                },
                            ],
                            'researchPlan': {
                                'kind': 'preset_compare',
                                'payload': {
                                    'baseline': {
                                        'id': 'baseline',
                                        'label': 'Baseline',
                                        'strategy': {'long': {'openIf': 'lead'}},
                                        'strategies': [
                                            {
                                                'id': 'helper-1',
                                                'label': 'Helper 1',
                                                'priority': 1,
                                                'enabled': True,
                                                'strategy': {'short': {'openIf': 'helper-1'}},
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                    },
                ],
                'batch_jobs': [
                    {
                        'id': 'portfolio-job',
                        'label': 'Portfolio Job',
                        'chart': {'symbol': 'EURUSD', 'timeframe': 'M1', 'bars': 4000},
                        'strategy': {'long': {'openIf': 'lead'}},
                        'strategies': [
                            {
                                'id': 'helper-1',
                                'label': 'Helper 1',
                                'priority': 1,
                                'enabled': True,
                                'strategy': {'short': {'openIf': 'helper-1'}},
                            },
                        ],
                        'researchPlan': {'kind': 'preset_compare'},
                    },
                ],
            },
        )

        self.assertEqual(created['request']['jobs'][0]['request']['strategies'][0]['id'], 'helper-1')
        self.assertEqual(created['request']['jobs'][0]['request']['researchPlan']['payload']['baseline']['strategies'][0]['id'], 'helper-1')
        self.assertEqual(created['request']['batch_jobs'][0]['strategies'][0]['id'], 'helper-1')

    def test_merge_indicator_payloads_preserves_alias_when_contexts_are_combined(self):
        merged = research_service._merge_indicator_payloads(
            [
                {'name': 'MarketRegime', 'params': [9, 21, 14], 'alias': ''},
                {'name': 'EMA', 'params': ['close', 21], 'alias': ''},
            ],
            [
                {'name': 'MarketRegime', 'params': [9, 21, 14], 'alias': 'mreg'},
                {'name': 'EMA', 'params': ['close', 21], 'alias': 'ema21'},
            ],
        )

        self.assertEqual(merged[0]['alias'], 'mreg')
        self.assertEqual(merged[1]['alias'], 'ema21')

    def test_update_research_campaign_preserves_unpatched_request_sections(self):
        created = workspace_store.create_workspace_research_campaign(
            'user-1',
            'default',
            label='Batch Template',
            description='Saved from Batch.',
            request={
                'jobs': [
                    {
                        'job_type': 'strategy_pipeline',
                        'request': {'id': 'job-a', 'label': 'Job A'},
                    },
                ],
                'batch_jobs': [
                    {
                        'id': 'draft-a',
                        'label': 'Draft A',
                    },
                ],
                'shared_features': [
                    {'name': 'EMA', 'params': ['close', 20], 'alias': 'ema20'},
                ],
                'options': {
                    'comparisonPreset': 'top_k',
                    'activeTemplateId': '12',
                },
            },
        )

        updated = research_service.update_research_campaign(
            'user-1',
            'default',
            created['id'],
            jobs=[
                {
                    'job_type': 'preset_compare',
                    'request': {'id': 'job-b', 'label': 'Job B'},
                },
            ],
        )

        self.assertEqual(updated['request']['jobs'][0]['request']['id'], 'job-b')
        self.assertEqual(updated['request']['batch_jobs'][0]['id'], 'draft-a')
        self.assertEqual(updated['request']['shared_features'][0]['alias'], 'ema20')
        self.assertEqual(updated['request']['options']['comparisonPreset'], 'top_k')

    def test_launch_research_campaign_rejects_draft_without_executable_jobs(self):
        created = workspace_store.create_workspace_research_campaign(
            'user-1',
            'default',
            label='Draft Only',
            description='Saved template without runnable jobs yet.',
            request={
                'jobs': [],
                'batch_jobs': [
                    {'id': 'draft-a', 'label': 'Draft A'},
                ],
                'shared_features': [
                    {'name': 'RSI', 'params': ['close', 14], 'alias': 'rsi14'},
                ],
                'options': {'activeTemplateId': '55'},
            },
        )

        with self.assertRaisesRegex(ValueError, 'no executable jobs'):
            research_service.launch_research_campaign('user-1', 'default', created['id'])

    def test_workspace_strategy_benchmark_crud(self):
        created = workspace_store.create_workspace_strategy_benchmark(
            'user-1',
            'default',
            label='Benchmark A',
            side='long',
            source='current_strategy',
            notes='Baseline benchmark',
            is_favorite=False,
            symbol='EURUSD',
            timeframe='M15',
            strategy={'long': {'openIf': 'True'}, 'short': {}, 'other': {}},
            strategies=[{'id': 'helper-1', 'label': 'Helper 1', 'strategy': {'short': {'openIf': 'False'}}}],
        )

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['label'], 'Benchmark A')
        self.assertEqual(created['side'], 'long')
        self.assertFalse(created['is_favorite'])

        benchmarks = workspace_store.list_workspace_strategy_benchmarks('user-1', 'default', limit=20)
        self.assertEqual(len(benchmarks), 1)
        self.assertEqual(benchmarks[0]['id'], created['id'])
        self.assertEqual(benchmarks[0]['notes'], 'Baseline benchmark')
        self.assertFalse(benchmarks[0]['is_favorite'])
        self.assertEqual(benchmarks[0]['strategy']['long']['openIf'], 'True')
        self.assertEqual(benchmarks[0]['strategies'][0]['id'], 'helper-1')

        updated = workspace_store.update_workspace_strategy_benchmark(
            'user-1',
            'default',
            created['id'],
            label='Benchmark B',
            side='short',
            source='manual',
            notes='Updated benchmark',
            is_favorite=True,
        )
        self.assertEqual(updated['label'], 'Benchmark B')
        self.assertEqual(updated['side'], 'short')
        self.assertEqual(updated['source'], 'manual')
        self.assertEqual(updated['notes'], 'Updated benchmark')
        self.assertTrue(updated['is_favorite'])

        deleted = workspace_store.delete_workspace_strategy_benchmark('user-1', 'default', created['id'])
        self.assertEqual(deleted['id'], created['id'])

        benchmarks_after_delete = workspace_store.list_workspace_strategy_benchmarks('user-1', 'default', limit=20)
        self.assertEqual(benchmarks_after_delete, [])

    def test_positive_history_save_resolves_external_lane_candidate_by_candidate_id(self):
        main_repo_root = Path(self.temp_dir.name) / 'main-repo'
        daytrade_repo_root = Path(self.temp_dir.name) / 'daytrade-repo'
        main_research_root = main_repo_root / 'backend' / 'python' / 'data' / 'research'
        daytrade_research_root = daytrade_repo_root / 'backend' / 'python' / 'data' / 'research'
        main_research_root.mkdir(parents=True, exist_ok=True)
        daytrade_research_root.mkdir(parents=True, exist_ok=True)

        artifact_path = (
            daytrade_research_root
            / 'user-1'
            / 'cross_asset_usdsek_m15_breakout_study'
            / 'paper1080_stage1.json'
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(
                {
                    'paper_id': 1080,
                    'candidates': [
                        {
                            'candidate_id': 's001',
                            'label': 'EMA stack depth refresh',
                            'symbol': 'USDSEK',
                            'timeframe': 'M15',
                            'indicators': [
                                {'name': 'EMA', 'params': ['close', 9], 'alias': 'ema9'},
                            ],
                            'strategy_payload': {
                                'long': {},
                                'short': {'openIf': 'close < ema9'},
                                'other': {},
                            },
                            'candidate_summary': {
                                'n_trades': 39,
                                'net_pnl': 35727.799421344724,
                                'monthly_projection': 727.9265843689491,
                            },
                        },
                    ],
                },
                ensure_ascii=True,
            ),
            encoding='utf-8',
        )

        existing = workspace_store.create_workspace_strategy_benchmark(
            'user-1',
            'default',
            label='paper1080 · EMA stack depth refresh',
            side='short',
            source='manual',
            notes='Existing item with the same human label but different payload.',
            is_favorite=False,
            symbol='USDSEK',
            timeframe='M15',
            strategy={'short': {'openIf': 'False'}, 'long': {}, 'other': {}},
            strategies=[],
        )

        with patch.object(workspace_backend, 'RESEARCH_ARTIFACTS_ROOT', main_research_root), patch.object(
            workspace_backend,
            'discover_lane_roots',
            return_value=[main_repo_root, daytrade_repo_root],
        ):
            result = workspace_backend._save_positive_history_winner_as_benchmark(
                'user-1',
                'default',
                {
                    'id': 'paper:1080:candidate:s001',
                    'sharedRegistryKey': 'paper:1080:candidate:s001',
                    'paperId': 1080,
                    'candidateId': 's001',
                    'label': 'paper1080 · EMA stack depth refresh',
                    'study': 'Paper 1080 winner group',
                    'classification': 'watch',
                    'operatorVerdict': 'Winner · 7%+ monthly',
                    'symbol': 'USDSEK',
                    'timeframe': 'M15',
                    'side': 'short',
                    'positiveCheckpoint': '~ +35727.80 net / +727.93 per month / 39 trades',
                    'checkpointContext': 'Paper 1080 row `s001`.',
                    'trades': 39,
                },
                is_favorite=False,
            )

        self.assertFalse(result['already_exists'])
        self.assertEqual(result['paper_id'], 1080)
        self.assertEqual(result['candidate_id'], 's001')
        self.assertEqual(result['benchmark']['symbol'], 'USDSEK')
        self.assertEqual(result['benchmark']['timeframe'], 'M15')
        self.assertEqual(result['benchmark']['strategy']['short']['openIf'], 'close < ema9')

        benchmarks = workspace_store.list_workspace_strategy_benchmarks('user-1', 'default', limit=20)
        self.assertEqual(len(benchmarks), 2)
        self.assertEqual(existing['id'], benchmarks[1]['id'])

    def test_positive_history_save_supports_multi_strategy_candidates_and_clear_scope(self):
        workspace_store.create_workspace_broker_profile(
            'user-1',
            'default',
            label='Forex.com',
            broker_code='forex.com',
            market_domain='forex',
            base_currency='USD',
            is_default=True,
        )
        clear_profile = workspace_store.create_workspace_broker_profile(
            'user-1',
            'default',
            label='CLEAR',
            broker_code='clear',
            market_domain='b3',
            base_currency='BRL',
            is_default=False,
        )

        main_repo_root = Path(self.temp_dir.name) / 'main-repo'
        general_repo_root = Path(self.temp_dir.name) / 'general-repo'
        main_research_root = main_repo_root / 'backend' / 'python' / 'data' / 'research'
        general_research_root = general_repo_root / 'backend' / 'python' / 'data' / 'research'
        main_research_root.mkdir(parents=True, exist_ok=True)
        general_research_root.mkdir(parents=True, exist_ok=True)

        artifact_path = (
            general_research_root
            / 'user-1'
            / 'ccm_portfolio_positive_history_study'
            / 'paper90119_stage1.json'
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(
                {
                    'paper_id': 90119,
                    'backtest_params': {
                        'broker_cost_context': {
                            'broker_profile_id': 'clear',
                            'broker_profile_label': 'CLEAR',
                            'broker_code': 'clear',
                            'broker_label': 'CLEAR',
                            'market_domain': 'b3',
                        },
                    },
                    'candidates': [
                        {
                            'candidate_id': 's003',
                            'label': 'Long corner 1.60 / 0.95',
                            'symbol': 'CCM$',
                            'timeframe': 'M15',
                            'candidate_summary': {
                                'n_trades': 61,
                                'net_pnl': 1327.8237921814011,
                                'monthly_projection': 134.6004203327652,
                            },
                            'strategy_entries': [
                                {
                                    'id': 'heavy_stack',
                                    'label': 'VWAP 0.0003 with bearish stack',
                                    'priority': 0,
                                    'enabled': True,
                                    'strategy_payload': {
                                        'long': {'openIf': 'False'},
                                        'short': {
                                            'openIf': (
                                                'tc_london_new_york_overlap[0] > 0 and ema20[0] < ema50[0] '
                                                'and ema50[0] < ema200[0] and reg_direction_score[0] < -0.15 '
                                                'and close[0] < dc20_lower[1] and roc10[0] < 0 '
                                                'and vwap_distance_ratio[0] < -0.0003'
                                            ),
                                        },
                                        'other': {'priority': 'Short'},
                                    },
                                },
                                {
                                    'id': 'fast_recycle_target_163_stop_092',
                                    'label': 'Target 1.63 with 0.92 stop',
                                    'priority': 1,
                                    'enabled': True,
                                    'strategy_payload': {
                                        'long': {'openIf': 'False'},
                                        'short': {
                                            'openIf': (
                                                'tc_london_new_york_overlap[0] > 0 and ema20[0] < ema50[0] '
                                                'and ema50[0] < ema200[0] and reg_direction_score[0] < -0.15 '
                                                'and close[0] < dc20_lower[1] and roc10[0] < 0 '
                                                'and vwap_distance_ratio[0] < -0.0003'
                                            ),
                                        },
                                        'other': {'priority': 'Short'},
                                    },
                                },
                                {
                                    'id': 'width_reexpansion_long_target_160_stop_095',
                                    'label': 'Width re-expansion long target 1.60 stop 0.95',
                                    'priority': 2,
                                    'enabled': True,
                                    'strategy_payload': {
                                        'long': {
                                            'openIf': (
                                                'ema20[0] > ema50[0] and ema50[0] > ema200[0] '
                                                'and reg_direction_score[0] > 0.20 and low[0] <= ema20[0] '
                                                'and close[0] > ema20[0] and bb_width[0] > bb_width[1]'
                                            ),
                                        },
                                        'short': {'openIf': 'False'},
                                        'other': {'priority': 'Long'},
                                    },
                                },
                            ],
                            'resolved_strategy_entries': [
                                {
                                    'strategy_id': 'heavy_stack',
                                    'strategy_label': 'VWAP 0.0003 with bearish stack',
                                    'priority': 0,
                                    'enabled': True,
                                    'resolved_strategy_params': {
                                        'open_short_condition': (
                                            'TemporalContext_london_new_york_overlap[0] > 0 and EMA_close_20[0] < EMA_close_50[0] '
                                            'and EMA_close_50[0] < EMA_close_200[0] and '
                                            'MarketRegime_55_21_14_5_3_2_20_14_10_3_hlc3_5_3_direction_score[0] < -0.15 '
                                            'and close[0] < DonchianChannels_20_lower[1] and ROC_close_10[0] < 0 '
                                            'and VWAP_hlc3_distance_ratio[0] < -0.0003'
                                        ),
                                    },
                                },
                                {
                                    'strategy_id': 'fast_recycle_target_163_stop_092',
                                    'strategy_label': 'Target 1.63 with 0.92 stop',
                                    'priority': 1,
                                    'enabled': True,
                                    'resolved_strategy_params': {
                                        'open_short_condition': (
                                            'TemporalContext_london_new_york_overlap[0] > 0 and EMA_close_20[0] < EMA_close_50[0] '
                                            'and EMA_close_50[0] < EMA_close_200[0] and '
                                            'MarketRegime_55_21_14_5_3_2_20_14_10_3_hlc3_5_3_direction_score[0] < -0.15 '
                                            'and close[0] < DonchianChannels_20_lower[1] and ROC_close_10[0] < 0 '
                                            'and VWAP_hlc3_distance_ratio[0] < -0.0003'
                                        ),
                                    },
                                },
                                {
                                    'strategy_id': 'width_reexpansion_long_target_160_stop_095',
                                    'strategy_label': 'Width re-expansion long target 1.60 stop 0.95',
                                    'priority': 2,
                                    'enabled': True,
                                    'resolved_strategy_params': {
                                        'open_long_condition': (
                                            'EMA_close_20[0] > EMA_close_50[0] and EMA_close_50[0] > EMA_close_200[0] '
                                            'and MarketRegime_55_21_14_5_3_2_20_14_10_3_hlc3_5_3_direction_score[0] > 0.20 '
                                            'and low[0] <= EMA_close_20[0] and close[0] > EMA_close_20[0] '
                                            'and BollingerBands_close_20_2_width[0] > BollingerBands_close_20_2_width[1]'
                                        ),
                                    },
                                },
                            ],
                        },
                    ],
                },
                ensure_ascii=True,
            ),
            encoding='utf-8',
        )

        with patch.object(workspace_backend, 'RESEARCH_ARTIFACTS_ROOT', main_research_root), patch.object(
            workspace_backend,
            'discover_lane_roots',
            return_value=[main_repo_root, general_repo_root],
        ):
            result = workspace_backend._save_positive_history_winner_as_benchmark(
                'user-1',
                'default',
                {
                    'id': 'paper90119-s003-general',
                    'sharedRegistryKey': 'paper:90119:candidate:s003',
                    'paperId': 90119,
                    'candidateId': 's003',
                    'label': 'Long corner 1.60 / 0.95',
                    'study': 'Paper 90119 winner group',
                    'classification': 'promoted',
                    'operatorVerdict': 'Winner · 7%+ monthly',
                    'symbol': 'CCM$',
                    'timeframe': 'M15',
                    'side': 'both',
                    'positiveCheckpoint': '~ +1327.82 net / +134.60 per month / 61 trades',
                    'checkpointContext': 'Paper 90119 row `s003`.',
                    'trades': 61,
                },
                is_favorite=False,
            )

        self.assertFalse(result['already_exists'])
        self.assertEqual(result['paper_id'], 90119)
        self.assertEqual(result['candidate_id'], 's003')
        self.assertEqual(result['benchmark']['symbol'], 'CCM$')
        self.assertEqual(result['benchmark']['broker_profile_id'], clear_profile['id'])
        self.assertEqual(result['benchmark']['broker_profile_label'], clear_profile['label'])
        self.assertEqual(len(result['benchmark']['strategies']), 3)
        first_entry_indicators = result['benchmark']['strategies'][0]['strategy']['featureManifest']['indicators']
        self.assertTrue(any(item['name'] == 'TemporalContext' for item in first_entry_indicators))
        self.assertTrue(any(item['name'] == 'EMA' and item['alias'] == 'ema20' for item in first_entry_indicators))
        self.assertTrue(any(item['name'] == 'MarketRegime' and item['alias'] == 'reg' for item in first_entry_indicators))
        self.assertTrue(any(item['name'] == 'DonchianChannels' and item['alias'] == 'dc20' for item in first_entry_indicators))
        self.assertTrue(any(item['name'] == 'ROC' and item['alias'] == 'roc10' for item in first_entry_indicators))
        self.assertTrue(any(item['name'] == 'VWAP' and item['alias'] == 'vwap' for item in first_entry_indicators))
        long_entry_indicators = result['benchmark']['strategies'][2]['strategy']['featureManifest']['indicators']
        self.assertTrue(any(item['name'] == 'BollingerBands' and item['alias'] == 'bb' for item in long_entry_indicators))

        clear_benchmarks = workspace_store.list_workspace_strategy_benchmarks(
            'user-1',
            'default',
            limit=20,
            broker_profile_id=clear_profile['id'],
        )
        self.assertEqual(len(clear_benchmarks), 1)
        self.assertEqual(clear_benchmarks[0]['label'], 'Long corner 1.60 / 0.95')
        self.assertEqual(len(clear_benchmarks[0]['strategies']), 3)

    def test_workspace_trade_reconciliation_persists_snapshots(self):
        workspace_store.upsert_workspace_live_trade(
            'user-1',
            'default',
            command_id='cmd-1',
            execution_mode='live_mt5',
            status='filled',
            sleeve_label='Deep 01',
            source_strategy_id='primary',
            symbol='EURUSD',
            timeframe='M1',
            action='open',
            side='long',
            created_at=100.0,
            filled_at=104.5,
            profit=12.0,
            commission=-0.5,
            swap=0.0,
            strategy={'long': {'openIf': 'True'}},
        )
        workspace_store.upsert_workspace_live_trade(
            'user-1',
            'default',
            command_id='cmd-2',
            execution_mode='live_mt5',
            status='rejected',
            sleeve_label='Deep 01',
            source_strategy_id='primary',
            symbol='EURUSD',
            timeframe='M1',
            action='open',
            side='short',
            created_at=110.0,
            rejected_at=111.0,
            strategy={'short': {'openIf': 'True'}},
        )

        created = workspace_store.create_workspace_trade_reconciliation(
            'user-1',
            'default',
            range_key='all',
            strategy_filter='Deep 01',
        )

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['summary']['total_commands'], 2)
        self.assertEqual(created['summary']['filled_count'], 1)
        self.assertEqual(created['summary']['rejected_count'], 1)
        self.assertAlmostEqual(created['summary']['realized_pnl'], 11.5)
        self.assertAlmostEqual(created['summary']['avg_delay_seconds'], 2.75)
        self.assertAlmostEqual(created['summary']['max_delay_seconds'], 4.5)

        snapshots = workspace_store.list_workspace_trade_reconciliations(
            'user-1',
            'default',
            range_key='all',
            strategy_filter='Deep 01',
            limit=10,
        )
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]['id'], created['id'])
        self.assertEqual(len(snapshots[0]['rows']), 2)

    def test_walkforward_train_test_pairs_builds_expected_segments(self):
        pairs = build_walkforward_train_test_pairs(
            total_bars=1000,
            train_bars=300,
            test_bars=100,
            step_bars=100,
        )

        self.assertGreaterEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['train_start_index'], 0)
        self.assertEqual(pairs[0]['train_end_index'], 300)
        self.assertEqual(pairs[0]['test_start_index'], 300)
        self.assertEqual(pairs[0]['test_end_index'], 400)
        self.assertEqual(pairs[0]['train_bars'], 300)
        self.assertEqual(pairs[0]['test_bars'], 100)

        if len(pairs) > 1:
            self.assertEqual(pairs[1]['train_start_index'], 100)
            self.assertEqual(pairs[1]['test_start_index'], 400)

    def test_walkforward_train_test_pairs_returns_empty_when_total_is_zero(self):
        pairs = build_walkforward_train_test_pairs(
            total_bars=0,
            train_bars=300,
            test_bars=100,
            step_bars=100,
        )

        self.assertEqual(pairs, [])

    def test_research_chart_context_auto_injects_market_regime_indicator(self):
        context = normalize_research_chart_context({
            'symbol': 'eurusd',
            'timeframe': 'm1',
            'bars': 2000,
            'indicators': [
                {'name': 'RSI', 'params': ['close', 14]},
            ],
        })

        self.assertEqual(context['symbol'], 'EURUSD')
        self.assertEqual(context['timeframe'], 'M1')
        self.assertEqual(context['bars'], 2000)
        self.assertTrue(any(str(item.get('name') or '') == 'RSI' for item in context['indicators']))
        self.assertTrue(any(str(item.get('name') or '') == 'MarketRegime' for item in context['indicators']))

    def test_research_chart_context_keeps_existing_market_regime_indicator(self):
        indicators = ensure_market_regime_indicator_payload([
            {'name': 'MarketRegime', 'params': [9, 21, 14, 14, 20, 2, 20, 14, 10, 3, 'hlc3', 5, 3]},
        ])

        self.assertEqual(len(indicators), 1)
        self.assertEqual(indicators[0]['name'], 'MarketRegime')


if __name__ == '__main__':
    unittest.main()
