import unittest
from unittest.mock import patch

from backend.python.lib.symbol import Symbol
from backend.python.strategy_backend import ApplyStrategyRequest, evaluate_strategy_request_in_context


def make_symbol(name, timeframe, rows):
    return Symbol(name, timeframe, len(rows), candles=rows)


class MultiMarketBacktestIntegrationTest(unittest.TestCase):
    def build_view(self, symbol):
        return {
            'symbol': symbol,
            'available_columns': list(symbol.candles.columns),
            'available_column_details': [],
            'applied_indicators': [],
            'history_scope_info': {
                'history_scope_mode': 'loaded_chart',
                'history_scope_bars': len(symbol.candles),
                'history_scope_available_bars': len(symbol.candles),
            },
            'meta': {
                'symbol': symbol.name,
                'timeframe': symbol.timeframe,
                'bars': len(symbol.candles),
            },
        }

    @patch('backend.python.strategy_backend.build_contextual_strategy_view')
    def test_multi_market_stack_runs_across_different_symbols(self, build_contextual_strategy_view_mock):
        eurusd = make_symbol('EURUSD', 'M1', [
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        gbpusd = make_symbol('GBPUSD', 'M5', [
            {'time': 10, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.5, 'volume': 1},
            {'time': 20, 'open': 110.0, 'high': 111.0, 'low': 109.0, 'close': 110.5, 'volume': 1},
            {'time': 30, 'open': 120.0, 'high': 121.0, 'low': 119.0, 'close': 120.5, 'volume': 1},
        ])
        view_map = {
            ('EURUSD', 'M1'): self.build_view(eurusd),
            ('GBPUSD', 'M5'): self.build_view(gbpusd),
        }
        build_contextual_strategy_view_mock.side_effect = lambda **kwargs: view_map[(kwargs['symbol_name'], kwargs['timeframe'])]

        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'strategies': [
                {
                    'id': 'eur-long',
                    'label': 'EUR long',
                    'symbol': 'EURUSD',
                    'timeframe': 'M1',
                    'priority': 0,
                    'strategy': {
                        'long': {
                            'openIf': 'time[0] == 1',
                            'closeIf': 'time[0] == 2',
                            'openPrice': 'open[0]',
                            'closePrice': 'open[0]',
                        },
                    },
                },
                {
                    'id': 'gbp-long',
                    'label': 'GBP long',
                    'symbol': 'GBPUSD',
                    'timeframe': 'M5',
                    'priority': 1,
                    'strategy': {
                        'long': {
                            'openIf': 'time[0] == 10',
                            'closeIf': 'time[0] == 20',
                            'openPrice': 'open[0]',
                            'closePrice': 'open[0]',
                        },
                    },
                },
            ],
            'backtest': {
                'portfolioMode': 'parallel_sleeves',
                'spreadInPips': 0.0,
                'entrySlippageInPips': 0.0,
                'closeSlippageInPips': 0.0,
            },
        })

        evaluation = evaluate_strategy_request_in_context(
            payload=payload,
            symbol_name='EURUSD',
            timeframe='M1',
            bars=3,
            indicators_payload=[],
        )

        self.assertEqual(evaluation['status'], 'ok')
        self.assertEqual(evaluation['stats']['strategy_count'], 2)
        self.assertEqual(evaluation['stats']['execution_policy']['portfolio_structure'], 'multi_market_stack')
        self.assertEqual(len(evaluation['stats']['portfolio_market_groups']), 2)
        self.assertEqual(evaluation['stats']['n_trades'], 2)
        self.assertEqual(evaluation['strategy_view_meta']['market_group_count'], 2)
        self.assertTrue(any(item['symbol'] == 'EURUSD' and item['timeframe'] == 'M1' for item in evaluation['stats']['portfolio_strategy_stats']))
        self.assertTrue(any(item['symbol'] == 'GBPUSD' and item['timeframe'] == 'M5' for item in evaluation['stats']['portfolio_strategy_stats']))
        self.assertTrue(any('[EURUSD M1]' in str(marker.get('text') or '') for marker in evaluation['trade_markers']))
        self.assertTrue(any('[GBPUSD M5]' in str(marker.get('text') or '') for marker in evaluation['trade_markers']))

    @patch.dict('backend.python.strategy_backend.FEATURE_FLAGS', {'backtest_portfolios_v2': True}, clear=False)
    @patch('backend.python.strategy_backend.build_contextual_strategy_view')
    def test_multi_market_stack_replays_shared_capital_for_explicit_portfolios(self, build_contextual_strategy_view_mock):
        eurusd = make_symbol('EURUSD', 'M1', [
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        gbpusd = make_symbol('GBPUSD', 'M5', [
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        view_map = {
            ('EURUSD', 'M1'): self.build_view(eurusd),
            ('GBPUSD', 'M5'): self.build_view(gbpusd),
        }
        build_contextual_strategy_view_mock.side_effect = lambda **kwargs: view_map[(kwargs['symbol_name'], kwargs['timeframe'])]

        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'portfolioStructureVersion': 2,
            'capitalModel': {
                'initialBalance': 10_000,
                'marginModel': 'forex_notional',
                'accountLeverage': 50,
                'contractSizePerLot': 100_000,
                'minLot': 0.01,
                'lotStep': 0.01,
            },
            'portfolios': [
                {
                    'id': 'p1',
                    'label': 'Portfolio 1',
                    'pipelines': [
                        {
                            'id': 'pipe1',
                            'label': 'Pipe 1',
                            'strategyEntries': [
                                {
                                    'id': 'eur-long',
                                    'label': 'EUR long',
                                    'symbol': 'EURUSD',
                                    'timeframe': 'M1',
                                    'volumeMode': 'max_affordable',
                                    'strategy': {
                                        'long': {
                                            'openIf': 'time[0] == 1',
                                            'closeIf': 'time[0] == 2',
                                            'openPrice': 'open[0]',
                                            'closePrice': 'open[0]',
                                        },
                                    },
                                },
                                {
                                    'id': 'gbp-long',
                                    'label': 'GBP long',
                                    'symbol': 'GBPUSD',
                                    'timeframe': 'M5',
                                    'volumeMode': 'max_affordable',
                                    'strategy': {
                                        'long': {
                                            'openIf': 'time[0] == 1',
                                            'closeIf': 'time[0] == 2',
                                            'openPrice': 'open[0]',
                                            'closePrice': 'open[0]',
                                        },
                                    },
                                },
                            ],
                        },
                    ],
                },
            ],
            'backtest': {
                'portfolioMode': 'parallel_sleeves',
                'spreadInPips': 0.0,
                'entrySlippageInPips': 0.0,
                'closeSlippageInPips': 0.0,
                'pipSize': 1.0,
                'pipValuePerLot': 1.0,
            },
        })

        evaluation = evaluate_strategy_request_in_context(
            payload=payload,
            symbol_name='EURUSD',
            timeframe='M1',
            bars=3,
            indicators_payload=[],
        )

        self.assertEqual(evaluation['status'], 'ok')
        self.assertEqual(evaluation['stats']['n_trades'], 1)
        self.assertEqual(evaluation['stats']['portfolio_event_counts']['skip_open_sizing'], 1)
        self.assertEqual(len(evaluation['stats']['ledger']), 1)
        self.assertEqual(evaluation['stats']['ledger'][0]['strategy_id'], 'eur-long')
        self.assertAlmostEqual(evaluation['stats']['ledger'][0]['executed_volume'], 0.25)
