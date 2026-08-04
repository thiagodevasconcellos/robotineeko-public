import unittest

import pandas as pd

from backend.python.app_state import state
from backend.python.lib.symbol import Symbol
from backend.python.runtime.chart_runtime import build_chart_runtime_payload
from backend.python.services.engine_view_service import (
    build_neural_market_view,
    build_results_view,
    build_strategy_feature_view,
)
from backend.python.strategy_backend import build_runtime_payload


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('TEST', 'M1', len(candles), candles=candles)


class RuntimeConsistencyTest(unittest.TestCase):
    def setUp(self):
        state.market_data.cache_by_key.clear()
        state.market_data.request_order.clear()
        state.market_data.requests_by_id.clear()
        state.market_data.revision = 0
        state.market.revision = 0
        state.market.tick_revision = 0
        state.market.candle_revision = 0
        state.market.last_event = None
        state.market.last_update_at = None

        state.chart.request = {
            'symbol': 'TEST',
            'timeframe': 'M1',
            'bars': 3,
            'indicators': [],
        }
        state.chart.snapshot_signature = None
        state.chart.snapshot_symbol = None
        state.chart.snapshot_applied_indicators = []
        state.chart.snapshot_available_column_details = []
        state.chart.snapshot_built_at = None

        state.strategy.request = None
        state.strategy.strategy = None
        state.strategy.backtester = None
        state.strategy.results = None
        state.strategy.stats = None
        state.strategy.available_columns = []
        state.strategy.available_column_details = []
        state.strategy.required_features = []
        state.strategy.required_feature_details = []
        state.strategy.strategy_view_meta = None
        state.strategy.last_applied_at = None
        state.strategy.last_results_generated_at = None
        state.strategy.performance = None

        state.neural.active_jobs.clear()
        state.neural.recent_events = []
        state.neural.last_run_at = None
        state.neural.last_error = None

    def test_strategy_view_meta_carries_chart_snapshot_context(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100, 'foo': 1.0},
            {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110, 'foo': 2.0},
            {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120, 'foo': 3.0},
        ])
        signature = {
            'market_context_revision': 17,
            'market_revision': 23,
            'market_context_key': 'TEST|M1|3',
            'refresh_mode': 'partial',
        }

        view = build_strategy_feature_view(
            chart_request={'symbol': 'TEST', 'timeframe': 'M1', 'bars': 3},
            snapshot_symbol=symbol,
            snapshot_signature=signature,
        )

        self.assertEqual(view['meta']['snapshot_market_context_revision'], 17)
        self.assertEqual(view['meta']['snapshot_market_revision'], 23)
        self.assertEqual(view['meta']['snapshot_cache_key'], 'TEST|M1|3')
        self.assertEqual(view['meta']['snapshot_refresh_mode'], 'partial')

    def test_strategy_runtime_payload_exposes_consumed_view_meta(self):
        state.strategy.strategy_view_meta = {
            'snapshot_market_context_revision': 12,
            'snapshot_market_revision': 15,
            'snapshot_cache_key': 'EURUSD|M1|1000',
            'snapshot_refresh_mode': 'full',
        }
        state.strategy.last_applied_at = 100.0
        state.strategy.last_results_generated_at = 105.0

        payload = build_runtime_payload()

        self.assertEqual(payload['strategy_view_meta']['snapshot_market_context_revision'], 12)
        self.assertEqual(payload['strategy_view_meta']['snapshot_cache_key'], 'EURUSD|M1|1000')
        self.assertEqual(payload['last_results_generated_at'], 105.0)

    def test_neural_market_view_uses_market_cache_revision_and_key(self):
        cache_key = 'EURUSD|M1|3'
        state.market_data.cache_by_key[cache_key] = {
            'revision': 44,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 3,
                'bars_loaded': 3,
                'candles': [
                    {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100},
                    {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110},
                    {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120},
                ],
            },
        }

        view = build_neural_market_view({
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 3,
        }, include_candles=False)

        self.assertTrue(view['ready'])
        self.assertEqual(view['cache_key'], cache_key)
        self.assertEqual(view['revision'], 44)

    def test_results_view_carries_strategy_snapshot_context(self):
        view = build_results_view(
            request={
                'backtest': {
                    'historyScopeMode': 'loaded_chart',
                    'historyScopeBars': 1000,
                    'executionMode': 'next_bar_open',
                },
            },
            stats={
                'n_trades': 7,
                'net_pnl': 12.5,
                'win_rate': 0.57,
            },
            results=[{'time': 1}, {'time': 2}],
            trade_markers=[{'time': 2, 'type': 'buy'}],
            strategy_view_meta={
                'snapshot_market_context_revision': 44,
                'snapshot_market_revision': 51,
                'snapshot_cache_key': 'EURUSD|M1|1000',
                'snapshot_refresh_mode': 'partial',
            },
        )

        self.assertEqual(view['meta']['row_count'], 2)
        self.assertEqual(view['meta']['trade_marker_count'], 1)
        self.assertEqual(view['meta']['snapshot_market_context_revision'], 44)
        self.assertEqual(view['meta']['snapshot_cache_key'], 'EURUSD|M1|1000')
        self.assertEqual(view['meta']['execution_mode'], 'next_bar_open')

    def test_strategy_runtime_payload_exposes_results_view_meta(self):
        state.strategy.request = {
            'backtest': {
                'historyScopeMode': 'loaded_chart',
                'historyScopeBars': 1000,
                'executionMode': 'next_bar_open',
            },
        }
        state.strategy.strategy_view_meta = {
            'snapshot_market_context_revision': 12,
            'snapshot_market_revision': 15,
            'snapshot_cache_key': 'EURUSD|M1|1000',
            'snapshot_refresh_mode': 'full',
        }
        state.strategy.results = [{'time': 1}, {'time': 2}, {'time': 3}]
        state.strategy.trade_markers = [{'time': 2, 'type': 'buy'}]
        state.strategy.stats = {'n_trades': 3, 'net_pnl': 7.5}

        payload = build_runtime_payload()

        self.assertEqual(payload['results_view']['row_count'], 3)
        self.assertEqual(payload['results_view']['trade_marker_count'], 1)
        self.assertEqual(payload['results_view']['snapshot_market_context_revision'], 12)

    def test_chart_strategy_and_neural_can_align_on_same_market_context(self):
        cache_key = 'EURUSD|M1|3'
        signature = {
            'market_context_revision': 44,
            'market_revision': 51,
            'market_context_key': cache_key,
            'refresh_mode': 'full',
        }
        symbol = make_symbol([
            {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100, 'foo': 1.0},
            {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110, 'foo': 2.0},
            {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120, 'foo': 3.0},
        ])

        state.chart.snapshot_signature = signature
        state.chart.snapshot_symbol = symbol
        state.chart.snapshot_built_at = 200.0
        state.market.revision = 51
        state.market.last_event = 'candle_update'
        state.market.last_update_at = 190.0
        state.market_data.cache_by_key[cache_key] = {
            'revision': 44,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 3,
                'bars_loaded': 3,
                'candles': [
                    {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100},
                    {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110},
                    {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120},
                ],
            },
        }

        strategy_view = build_strategy_feature_view(
            chart_request={'symbol': 'TEST', 'timeframe': 'M1', 'bars': 3},
            snapshot_symbol=symbol,
            snapshot_signature=signature,
        )
        neural_view = build_neural_market_view({
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 3,
        }, include_candles=False)
        chart_payload = build_chart_runtime_payload()

        self.assertEqual(chart_payload['snapshot_signature']['market_context_key'], cache_key)
        self.assertEqual(strategy_view['meta']['snapshot_cache_key'], cache_key)
        self.assertEqual(neural_view['cache_key'], cache_key)
        self.assertEqual(chart_payload['snapshot_signature']['market_context_revision'], neural_view['revision'])


if __name__ == '__main__':
    unittest.main()
