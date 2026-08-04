import unittest
from unittest.mock import patch

from backend.python.app_state import state
from backend.python.strategy_backend import build_contextual_strategy_view


class StrategyBacktestHistoryFallbackTest(unittest.TestCase):
    def setUp(self):
        state.research.feature_view_cache.clear()
        state.research.feature_view_cache_order.clear()
        state.research.feature_view_cache_stats.update({
            'hits': 0,
            'misses': 0,
            'stores': 0,
            'evictions': 0,
            'last_event': None,
            'last_key': None,
            'last_at': None,
        })

    def test_contextual_strategy_view_uses_truncated_market_fallback_meta(self):
        truncated_context = {
            'ready': True,
            'truncated': True,
            'notice': 'Requested 100,000 candles but only 3 are currently available. Using the maximum available history instead.',
            'cache_key': 'CCM$|M15|1000',
            'revision': 21,
            'bars_requested': 3,
            'requested_bars_original': 100000,
            'candles': [
                {'time': 1, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100},
                {'time': 2, 'open': 11, 'high': 12, 'low': 10, 'close': 11.5, 'volume': 110},
                {'time': 3, 'open': 12, 'high': 13, 'low': 11, 'close': 12.5, 'volume': 120},
            ],
        }

        with patch('backend.python.strategy_backend.wait_for_market_data', return_value=truncated_context) as wait_mock:
            view = build_contextual_strategy_view(
                symbol_name='CCM$',
                timeframe='M15',
                bars=100000,
                indicators_payload=[],
                backtest_params={'history_scope_mode': 'loaded_chart'},
            )

        self.assertEqual(wait_mock.call_args.kwargs['allow_truncated_fallback'], True)
        self.assertEqual(view['meta']['requested_market_bars'], 100000)
        self.assertEqual(view['meta']['available_market_bars'], 3)
        self.assertTrue(view['meta']['market_context_truncated'])
        self.assertEqual(view['meta']['market_context_truncated_from_bars'], 100000)
        self.assertEqual(view['meta']['market_context_truncated_to_bars'], 3)
        self.assertEqual(view['meta']['bars'], 3)
        self.assertEqual(view['meta']['row_count'], 3)
        self.assertEqual(view['meta']['history_scope_available_bars'], 3)
        self.assertEqual(view['symbol'].candles['time'].tolist(), [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
