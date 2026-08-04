import unittest

import pandas as pd

from backend.python.app_state import state
from backend.python.lib.symbol import Symbol
from backend.python.services.engine_view_service import (
    build_neural_market_view,
    build_strategy_feature_view,
)


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('TEST', 'M1', len(candles), candles=candles)


class EngineViewsTest(unittest.TestCase):
    def setUp(self):
        state.market_data.cache_by_key.clear()
        state.market_data.request_order.clear()
        state.market_data.requests_by_id.clear()
        state.market_data.revision = 0

    def test_strategy_feature_view_uses_loaded_chart_scope_by_default(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100, 'foo': 1.0},
            {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110, 'foo': 2.0},
            {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120, 'foo': 3.0},
        ])

        view = build_strategy_feature_view(
            chart_request={'symbol': 'TEST', 'timeframe': 'M1', 'bars': 3},
            snapshot_symbol=symbol,
            applied_indicators=[{'name': 'FakeFeature', 'params': [], 'columns': ['foo']}],
            available_column_details=[
                {'column_name': 'time', 'normalized_column_name': 'time'},
                {'column_name': 'foo', 'normalized_column_name': 'foo'},
            ],
        )

        self.assertEqual(view['meta']['history_scope_mode'], 'loaded_chart')
        self.assertEqual(view['meta']['row_count'], 3)
        self.assertIn('foo', view['available_columns'])
        self.assertEqual(view['history_scope_info']['history_scope_bars'], 3)
        self.assertEqual(len(view['available_column_details']), 2)
        self.assertIs(view['symbol'], symbol)

    def test_strategy_feature_view_supports_custom_tail_scope(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100, 'foo': 1.0},
            {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110, 'foo': 2.0},
            {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120, 'foo': 3.0},
            {'time': 4, 'open': 13, 'high': 14, 'low': 12, 'close': 13.5, 'volume': 130, 'foo': 4.0},
        ])

        view = build_strategy_feature_view(
            chart_request={'symbol': 'TEST', 'timeframe': 'M1', 'bars': 4},
            snapshot_symbol=symbol,
            backtest_params={'history_scope_mode': 'custom', 'history_scope_bars': 2},
        )

        self.assertEqual(view['meta']['history_scope_mode'], 'custom')
        self.assertEqual(view['meta']['row_count'], 2)
        self.assertEqual(view['symbol'].candles['time'].tolist(), [3, 4])

    def test_neural_market_view_reads_ready_cache_context(self):
        cache_key = 'EURUSD|M1|3'
        state.market_data.cache_by_key[cache_key] = {
            'revision': 7,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 3,
                'bars_loaded': 3,
                'first_time': 1,
                'last_time': 3,
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
        })

        self.assertTrue(view['ready'])
        self.assertEqual(view['cache_key'], cache_key)
        self.assertEqual(view['bars_available'], 3)
        self.assertEqual(len(view['candles']), 3)

    def test_neural_market_view_reports_not_ready_state(self):
        view = build_neural_market_view({
            'symbol': 'GBPUSD',
            'timeframe': 'M5',
            'bars': 10,
        }, include_candles=False)

        self.assertFalse(view['ready'])
        self.assertIn('not ready', str(view['error']).lower())
        self.assertEqual(view['bars_available'], 0)

    def test_symbol_can_reuse_dataframe_without_copy(self):
        candles = pd.DataFrame([
            {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100},
        ])

        symbol = Symbol('TEST', 'M1', len(candles), candles=candles, copy_candles=False)

        self.assertIs(symbol.candles, candles)


if __name__ == '__main__':
    unittest.main()
