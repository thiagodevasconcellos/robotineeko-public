import unittest

from backend.python.app_state import state
from backend.python.strategy_backend import build_isolated_backtest_response


class BacktestIsolatedContextTest(unittest.TestCase):
    def setUp(self):
        state.strategy.request = {
            'backtest': {
                'historyScopeMode': 'loaded_chart',
                'historyScopeBars': 1000,
            },
        }
        state.strategy.strategy_view_meta = {
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'history_scope_mode': 'loaded_chart',
            'history_scope_bars': 1000,
        }
        state.strategy.stats = {
            'net_pnl': 12.0,
        }
        state.strategy.trade_markers = [{'time': 2}]

    def test_isolated_backtest_response_keeps_evaluation_meta_separate_from_runtime(self):
        evaluation = {
            'serialized_results': [{'time': 1}, {'time': 2}, {'time': 3}],
            'stats': {'net_pnl': 34.5},
            'trade_markers': [{'time': 3}],
            'strategy_view_meta': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'history_scope_mode': 'custom',
                'history_scope_bars': 1000000,
                'history_scope_requested_bars': 1000000,
                'history_scope_available_bars': 1000000,
            },
            'applied_indicators': [{'name': 'RSI'}],
            'available_columns': ['close'],
            'available_column_details': [{'column_name': 'close'}],
        }

        response = build_isolated_backtest_response(
            request_payload={
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars': 1000000,
            },
            evaluation=evaluation,
        )

        self.assertEqual(response['rows'], 3)
        self.assertEqual(response['stats']['net_pnl'], 34.5)
        self.assertEqual(response['strategy_view_meta']['history_scope_mode'], 'custom')
        self.assertEqual(response['strategy_view_meta']['history_scope_bars'], 1000000)
        self.assertEqual(response['runtime']['strategy_view_meta']['history_scope_mode'], 'loaded_chart')


if __name__ == '__main__':
    unittest.main()
