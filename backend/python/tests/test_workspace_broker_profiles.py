import tempfile
import time
import unittest
from pathlib import Path

from backend.python.services import workspace_store


class WorkspaceBrokerProfilesStoreTest(unittest.TestCase):
    def setUp(self):
        self.original_db_path = workspace_store.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        workspace_store.DB_PATH = Path(self.temp_dir.name) / 'workspace-broker-profiles.db'
        workspace_store.ensure_workspace_store()
        self.user_id = 'broker-test-user'
        self.workspace_id = 'default'

    def tearDown(self):
        workspace_store.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def _create_live_trade(self, command_id, *, broker_profile_id=None, broker_profile_label=None, symbol='EURUSD'):
        now = time.time()
        return workspace_store.upsert_workspace_live_trade(
            self.user_id,
            self.workspace_id,
            command_id=command_id,
            status='filled',
            execution_mode='live_mt5',
            symbol=symbol,
            timeframe='M15',
            action='open',
            side='long',
            created_at=now - 5,
            filled_at=now,
            profit=12.5,
            commission=-0.7,
            swap=0.0,
            strategy={'long': {'openIf': 'True'}},
            broker_profile_id=broker_profile_id,
            broker_profile_label=broker_profile_label,
        )

    def test_first_profile_adopts_legacy_unscoped_records(self):
        workspace_store.create_workspace_strategy_benchmark(
            self.user_id,
            self.workspace_id,
            label='Legacy benchmark',
            side='long',
            source='manual',
            notes='',
            is_favorite=False,
            symbol='EURUSD',
            timeframe='M15',
            strategy={'long': {'openIf': 'True'}},
            strategies=[],
        )
        workspace_store.create_workspace_saved_portfolio(
            self.user_id,
            self.workspace_id,
            label='Legacy portfolio',
            source='manual',
            notes='',
            is_favorite=False,
            portfolio={'pipelines': []},
            capital_model={},
        )
        self._create_live_trade('cmd-legacy')
        workspace_store.create_workspace_trade_reconciliation(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id='',
            broker_profile_label='',
            limit=50,
        )

        profile = workspace_store.create_workspace_broker_profile(
            self.user_id,
            self.workspace_id,
            label='FOREX.com Live',
            broker_code='forex.com',
            market_domain='forex',
            base_currency='USD',
            is_default=True,
        )

        benchmarks = workspace_store.list_workspace_strategy_benchmarks(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=profile['id'],
        )
        portfolios = workspace_store.list_workspace_saved_portfolios(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=profile['id'],
        )
        live_payload = workspace_store.list_workspace_live_trades(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=profile['id'],
            limit=20,
        )
        reconciliations = workspace_store.list_workspace_trade_reconciliations(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=profile['id'],
            limit=20,
        )

        self.assertTrue(profile['is_default'])
        self.assertEqual(len(benchmarks), 1)
        self.assertEqual(benchmarks[0]['broker_profile_id'], profile['id'])
        self.assertEqual(benchmarks[0]['broker_profile_label'], 'FOREX.com Live')
        self.assertEqual(len(portfolios), 1)
        self.assertEqual(portfolios[0]['broker_profile_id'], profile['id'])
        self.assertEqual(len(live_payload['trades']), 1)
        self.assertEqual(live_payload['trades'][0]['broker_profile_id'], profile['id'])
        self.assertEqual(len(reconciliations), 1)
        self.assertEqual(reconciliations[0]['broker_profile_id'], profile['id'])

    def test_updating_profile_label_cascades_to_referenced_rows(self):
        profile = workspace_store.create_workspace_broker_profile(
            self.user_id,
            self.workspace_id,
            label='Clear Alpha',
            broker_code='clear',
            market_domain='brazil',
            base_currency='BRL',
            is_default=True,
        )
        workspace_store.create_workspace_strategy_benchmark(
            self.user_id,
            self.workspace_id,
            label='B3 benchmark',
            side='long',
            source='manual',
            notes='',
            is_favorite=False,
            symbol='WIN$',
            timeframe='M15',
            strategy={'long': {'openIf': 'True'}},
            strategies=[],
            broker_profile_id=profile['id'],
        )
        workspace_store.create_workspace_saved_portfolio(
            self.user_id,
            self.workspace_id,
            label='B3 portfolio',
            source='manual',
            notes='',
            is_favorite=False,
            portfolio={'pipelines': []},
            capital_model={},
            broker_profile_id=profile['id'],
        )
        self._create_live_trade(
            'cmd-clear',
            broker_profile_id=profile['id'],
            broker_profile_label=profile['label'],
            symbol='WDO$',
        )
        workspace_store.create_workspace_trade_reconciliation(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=profile['id'],
            broker_profile_label=profile['label'],
            limit=50,
        )

        updated = workspace_store.update_workspace_broker_profile(
            self.user_id,
            self.workspace_id,
            profile['id'],
            label='Clear Brasil',
        )

        benchmarks = workspace_store.list_workspace_strategy_benchmarks(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=profile['id'],
        )
        portfolios = workspace_store.list_workspace_saved_portfolios(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=profile['id'],
        )
        live_payload = workspace_store.list_workspace_live_trades(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=profile['id'],
            limit=20,
        )
        reconciliations = workspace_store.list_workspace_trade_reconciliations(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=profile['id'],
            limit=20,
        )

        self.assertEqual(updated['label'], 'Clear Brasil')
        self.assertEqual(benchmarks[0]['broker_profile_label'], 'Clear Brasil')
        self.assertEqual(portfolios[0]['broker_profile_label'], 'Clear Brasil')
        self.assertEqual(live_payload['trades'][0]['broker_profile_label'], 'Clear Brasil')
        self.assertEqual(reconciliations[0]['broker_profile_label'], 'Clear Brasil')

    def test_filters_keep_broker_contexts_separate(self):
        forex_profile = workspace_store.create_workspace_broker_profile(
            self.user_id,
            self.workspace_id,
            label='FOREX.com',
            broker_code='forex.com',
            market_domain='forex',
            base_currency='USD',
            is_default=True,
        )
        clear_profile = workspace_store.create_workspace_broker_profile(
            self.user_id,
            self.workspace_id,
            label='Clear',
            broker_code='clear',
            market_domain='brazil',
            base_currency='BRL',
            is_default=False,
        )

        workspace_store.create_workspace_strategy_benchmark(
            self.user_id,
            self.workspace_id,
            label='FX benchmark',
            side='long',
            source='manual',
            notes='',
            is_favorite=False,
            symbol='EURUSD',
            timeframe='M15',
            strategy={'long': {'openIf': 'True'}},
            strategies=[],
            broker_profile_id=forex_profile['id'],
        )
        workspace_store.create_workspace_strategy_benchmark(
            self.user_id,
            self.workspace_id,
            label='B3 benchmark',
            side='long',
            source='manual',
            notes='',
            is_favorite=False,
            symbol='WIN$',
            timeframe='M15',
            strategy={'long': {'openIf': 'True'}},
            strategies=[],
            broker_profile_id=clear_profile['id'],
        )
        workspace_store.create_workspace_saved_portfolio(
            self.user_id,
            self.workspace_id,
            label='FX portfolio',
            source='manual',
            notes='',
            is_favorite=False,
            portfolio={'pipelines': []},
            capital_model={},
            broker_profile_id=forex_profile['id'],
        )
        workspace_store.create_workspace_saved_portfolio(
            self.user_id,
            self.workspace_id,
            label='B3 portfolio',
            source='manual',
            notes='',
            is_favorite=False,
            portfolio={'pipelines': []},
            capital_model={},
            broker_profile_id=clear_profile['id'],
        )
        self._create_live_trade(
            'cmd-forex',
            broker_profile_id=forex_profile['id'],
            broker_profile_label=forex_profile['label'],
            symbol='EURUSD',
        )
        self._create_live_trade(
            'cmd-clear',
            broker_profile_id=clear_profile['id'],
            broker_profile_label=clear_profile['label'],
            symbol='WIN$',
        )
        workspace_store.create_workspace_trade_reconciliation(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=forex_profile['id'],
            broker_profile_label=forex_profile['label'],
            limit=50,
        )
        workspace_store.create_workspace_trade_reconciliation(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=clear_profile['id'],
            broker_profile_label=clear_profile['label'],
            limit=50,
        )

        forex_benchmarks = workspace_store.list_workspace_strategy_benchmarks(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=forex_profile['id'],
        )
        clear_benchmarks = workspace_store.list_workspace_strategy_benchmarks(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=clear_profile['id'],
        )
        forex_portfolios = workspace_store.list_workspace_saved_portfolios(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=forex_profile['id'],
        )
        clear_portfolios = workspace_store.list_workspace_saved_portfolios(
            self.user_id,
            self.workspace_id,
            limit=20,
            broker_profile_id=clear_profile['id'],
        )
        forex_live = workspace_store.list_workspace_live_trades(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=forex_profile['id'],
            limit=20,
        )
        clear_live = workspace_store.list_workspace_live_trades(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=clear_profile['id'],
            limit=20,
        )
        forex_reconciliations = workspace_store.list_workspace_trade_reconciliations(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=forex_profile['id'],
            limit=20,
        )
        clear_reconciliations = workspace_store.list_workspace_trade_reconciliations(
            self.user_id,
            self.workspace_id,
            range_key='all',
            broker_profile_id=clear_profile['id'],
            limit=20,
        )

        self.assertEqual([entry['label'] for entry in forex_benchmarks], ['FX benchmark'])
        self.assertEqual([entry['label'] for entry in clear_benchmarks], ['B3 benchmark'])
        self.assertEqual([entry['label'] for entry in forex_portfolios], ['FX portfolio'])
        self.assertEqual([entry['label'] for entry in clear_portfolios], ['B3 portfolio'])
        self.assertEqual([entry['command_id'] for entry in forex_live['trades']], ['cmd-forex'])
        self.assertEqual([entry['command_id'] for entry in clear_live['trades']], ['cmd-clear'])
        self.assertEqual(len(forex_reconciliations), 1)
        self.assertEqual(forex_reconciliations[0]['broker_profile_id'], forex_profile['id'])
        self.assertEqual(len(clear_reconciliations), 1)
        self.assertEqual(clear_reconciliations[0]['broker_profile_id'], clear_profile['id'])

    def test_workspace_state_repairs_benchmark_derived_strategy_pipe_order(self):
        benchmark = workspace_store.create_workspace_strategy_benchmark(
            self.user_id,
            self.workspace_id,
            label='Long corner 1.60 / 0.95',
            side='both',
            source='codex-research-winner',
            notes='',
            is_favorite=True,
            symbol='CCM$',
            timeframe='M15',
            strategy={
                'long': {'openIf': 'long_primary'},
                'short': {'openIf': 'False'},
                'other': {'priority': 'Long'},
            },
            strategies=[
                {
                    'label': 'VWAP 0.0003 with bearish stack',
                    'enabled': True,
                    'strategy': {
                        'long': {'openIf': 'False'},
                        'short': {'openIf': 'short_vwap'},
                        'other': {'priority': 'Short'},
                    },
                },
                {
                    'label': 'Target 1.63 with 0.92 stop',
                    'enabled': True,
                    'strategy': {
                        'long': {'openIf': 'False'},
                        'short': {'openIf': 'short_target'},
                        'other': {'priority': 'Short'},
                    },
                },
            ],
        )

        malformed_entries = [
            {
                'id': 'entry-0',
                'label': 'Long corner 1.60 / 0.95',
                'sourceBenchmarkId': str(benchmark['id']),
                'sourceBenchmarkLabel': 'Long corner 1.60 / 0.95',
                'sourceBenchmarkEntryLabel': 'Target 1.63 with 0.92 stop',
                'symbol': 'CCM$',
                'timeframe': 'M15',
                'enabled': True,
                'strategy': {
                    'long': {'openIf': 'False'},
                    'short': {'openIf': 'short_target'},
                    'other': {'priority': 'Short'},
                },
            },
            {
                'id': 'entry-1',
                'label': 'Strategy 1 · Long/Short',
                'sourceBenchmarkId': str(benchmark['id']),
                'sourceBenchmarkLabel': 'Long corner 1.60 / 0.95',
                'sourceBenchmarkEntryLabel': 'Strategy 1 · Long/Short',
                'symbol': 'CCM$',
                'timeframe': 'M15',
                'enabled': True,
                'strategy': {
                    'long': {'openIf': 'long_primary'},
                    'short': {'openIf': 'False'},
                    'other': {'priority': 'Long'},
                },
            },
            {
                'id': 'entry-2',
                'label': 'VWAP 0.0003 with bearish stack',
                'sourceBenchmarkId': str(benchmark['id']),
                'sourceBenchmarkLabel': 'Long corner 1.60 / 0.95',
                'sourceBenchmarkEntryLabel': 'VWAP 0.0003 with bearish stack',
                'symbol': 'CCM$',
                'timeframe': 'M15',
                'enabled': True,
                'strategy': {
                    'long': {'openIf': 'False'},
                    'short': {'openIf': 'short_vwap'},
                    'other': {'priority': 'Short'},
                },
            },
        ]

        saved = workspace_store.save_workspace_state(
            self.user_id,
            self.workspace_id,
            {
                'strategy': malformed_entries[1]['strategy'],
                'backtestStrategySet': malformed_entries,
                'backtestRunResponse': {
                    'status': 'ok',
                    'request': {
                        'strategy': malformed_entries[1]['strategy'],
                        'strategies': malformed_entries,
                    },
                },
            },
        )

        repaired_entries = saved['state']['backtestStrategySet']
        self.assertEqual(
            [entry['label'] for entry in repaired_entries],
            [
                'Long corner 1.60 / 0.95',
                'VWAP 0.0003 with bearish stack',
                'Target 1.63 with 0.92 stop',
            ],
        )
        self.assertEqual(repaired_entries[0]['strategy']['long']['openIf'], 'long_primary')
        self.assertEqual(repaired_entries[1]['strategy']['short']['openIf'], 'short_vwap')
        self.assertEqual(repaired_entries[2]['strategy']['short']['openIf'], 'short_target')
        self.assertEqual(
            [entry['label'] for entry in saved['state']['backtestRunResponse']['request']['strategies']],
            [
                'Long corner 1.60 / 0.95',
                'VWAP 0.0003 with bearish stack',
                'Target 1.63 with 0.92 stop',
            ],
        )

        loaded = workspace_store.load_workspace_state(self.user_id, self.workspace_id)
        self.assertEqual(
            [entry['label'] for entry in loaded['state']['backtestStrategySet']],
            [
                'Long corner 1.60 / 0.95',
                'VWAP 0.0003 with bearish stack',
                'Target 1.63 with 0.92 stop',
            ],
        )

    def test_delete_profile_rejects_when_referenced(self):
        profile = workspace_store.create_workspace_broker_profile(
            self.user_id,
            self.workspace_id,
            label='OANDA',
            broker_code='oanda',
            market_domain='forex',
            base_currency='USD',
            is_default=True,
        )
        workspace_store.create_workspace_strategy_benchmark(
            self.user_id,
            self.workspace_id,
            label='Referenced benchmark',
            side='long',
            source='manual',
            notes='',
            is_favorite=False,
            symbol='GBPUSD',
            timeframe='M15',
            strategy={'long': {'openIf': 'True'}},
            strategies=[],
            broker_profile_id=profile['id'],
        )

        with self.assertRaisesRegex(ValueError, 'still referenced'):
            workspace_store.delete_workspace_broker_profile(
                self.user_id,
                self.workspace_id,
                profile['id'],
            )


if __name__ == '__main__':
    unittest.main()
