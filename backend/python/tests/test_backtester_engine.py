import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

from backend.python.lib.strategy import Backtester, Strategy
from backend.python.lib.strategy.expression_identifiers import build_expression_safe_identifier
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    candles = pd.DataFrame(rows)
    return Symbol('TEST', 'M1', len(candles), candles=candles)


class BacktesterExecutionSemanticsTest(unittest.TestCase):
    def build_strategy(self, **overrides):
        strategy = Strategy()
        params = {
            'open_long_condition': 'False',
            'close_long_condition': 'False',
            'open_short_condition': 'False',
            'close_short_condition': 'False',
            'open_trade_price_long': 'open[0]',
            'open_trade_price_short': 'open[0]',
            'close_trade_price_long': 'close[0]',
            'close_trade_price_short': 'close[0]',
            'stop_gain_long_price': '',
            'stop_loss_long_price': '',
            'stop_gain_short_price': '',
            'stop_loss_short_price': '',
            'trailing_stop_long_price': '',
            'trailing_stop_short_price': '',
            'allow_invertion': False,
            'prioritize': 'short',
            'execution_mode': 'next_bar_open',
        }
        params.update(overrides)
        strategy.set_params(**params)
        return strategy

    def build_backtester(self, symbol, strategy, **overrides):
        backtester = Backtester(symbol, strategy)
        params = {
            'initial_balance': 10000.0,
            'asset_type': 'forex',
            'initial_volume': 1.0,
            'pip_size': 1.0,
            'pip_value_per_lot': 1.0,
            'cost_profile': 'oanda',
            'spread_in_pips': 0.0,
            'entry_slippage_in_pips': 0.0,
            'close_slippage_in_pips': 0.0,
            'take_profit_slippage_in_pips': 0.0,
            'stop_loss_slippage_in_pips': 0.0,
            'trailing_stop_slippage_in_pips': 0.0,
            'volatility_slippage_multiplier': 0.0,
            'execution_mode': 'next_bar_open',
        }
        params.update(overrides)
        backtester.set_params(**params)
        return backtester

    def test_next_bar_open_enters_on_following_candle_open(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            open_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(symbol, strategy)

        results = backtester.run()

        self.assertTrue(pd.isna(results.loc[0, 'long_open_price']))
        self.assertEqual(results.loc[1, 'long_open_price'], 20.0)
        self.assertEqual(backtester.execution.final_position, 1)

    def test_intrabar_conflict_prefers_loss_over_gain(self):
        symbol = make_symbol([
            {'time': 1, 'open': 5.0, 'high': 6.0, 'low': 4.0, 'close': 5.5, 'volume': 1},
            {'time': 2, 'open': 10.0, 'high': 12.0, 'low': 8.0, 'close': 11.0, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            stop_gain_long_price='long_open_price[0] + 1',
            stop_loss_long_price='long_open_price[0] - 1',
        )
        backtester = self.build_backtester(symbol, strategy)

        results = backtester.run()

        self.assertEqual(results.loc[1, 'order_type'], 'stop_long_loss')
        self.assertEqual(results.loc[1, 'long_open_price'], 10.0)
        self.assertEqual(results.loc[1, 'long_close_price'], 8.0)

    def test_trailing_stop_is_blocked_on_entry_candle(self):
        symbol = make_symbol([
            {'time': 1, 'open': 5.0, 'high': 6.0, 'low': 4.0, 'close': 5.5, 'volume': 1},
            {'time': 2, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 3, 'open': 11.0, 'high': 11.2, 'low': 10.4, 'close': 10.6, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            trailing_stop_long_price='long_open_price[0] + 0.5',
        )
        backtester = self.build_backtester(symbol, strategy)

        results = backtester.run()

        self.assertTrue(pd.isna(results.loc[1, 'long_close_price']))
        self.assertEqual(results.loc[2, 'order_type'], 'stop_long_trail')
        self.assertEqual(results.loc[2, 'long_close_price'], 10.4)

    def test_event_slippage_and_volatility_multiplier_change_execution_prices(self):
        symbol = make_symbol([
            {'time': 1, 'open': 100.0, 'high': 104.0, 'low': 100.0, 'close': 103.0, 'volume': 1},
            {'time': 2, 'open': 200.0, 'high': 201.0, 'low': 198.0, 'close': 199.0, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            stop_loss_long_price='long_open_price[0] - 1',
        )
        backtester = self.build_backtester(
            symbol,
            strategy,
            entry_slippage_in_pips=0.2,
            stop_loss_slippage_in_pips=0.5,
            volatility_slippage_multiplier=0.1,
        )

        results = backtester.run()

        self.assertAlmostEqual(results.loc[1, 'long_open_price'], 200.6, places=6)
        self.assertEqual(results.loc[1, 'order_type'], 'stop_long_loss')
        self.assertAlmostEqual(results.loc[1, 'long_close_price'], 197.1, places=6)

    def test_backtester_accepts_safe_identifiers_for_decimal_param_columns(self):
        decimal_column = 'ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_bull_breakout_flag'
        safe_identifier = build_expression_safe_identifier(decimal_column)
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1, decimal_column: 1.0},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1, decimal_column: 0.0},
        ])
        strategy = self.build_strategy(
            open_long_condition=f'{safe_identifier}[0] > 0',
            open_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(symbol, strategy)

        results = backtester.run()

        self.assertEqual(results.loc[1, 'long_open_price'], 20.0)
        self.assertEqual(backtester.execution.final_position, 1)

    def test_last_trade_close_timestamp_persists_for_cooldown_gates(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 11.0, 'high': 12.0, 'low': 10.0, 'close': 11.5, 'volume': 1},
            {'time': 3, 'open': 12.0, 'high': 13.0, 'low': 11.0, 'close': 12.5, 'volume': 1},
            {'time': 4, 'open': 13.0, 'high': 14.0, 'low': 12.0, 'close': 13.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition=(
                '((time[0] == 1) or (time[0] >= 3)) and '
                '((last_trade_close_timestamp[0] == None) or ((time[0] - last_trade_close_timestamp[0]) >= 2))'
            ),
            close_long_condition='time[0] == 2',
            open_trade_price_long='open[0]',
            close_trade_price_long='open[0]',
            execution_mode='same_bar',
        )
        backtester = self.build_backtester(symbol, strategy, execution_mode='same_bar')

        results = backtester.run()

        self.assertEqual(results.loc[0, 'order_type'], 'open_long')
        self.assertEqual(results.loc[1, 'order_type'], 'close_long')
        self.assertEqual(results.loc[1, 'last_trade_close_timestamp'], 2)
        self.assertEqual(results.loc[2, 'last_trade_close_timestamp'], 2)
        self.assertEqual(results.loc[3, 'last_trade_close_timestamp'], 2)
        self.assertTrue(pd.isna(results.loc[2, 'order_type']))
        self.assertEqual(results.loc[3, 'order_type'], 'open_long')

    def test_partial_rerun_matches_full_run(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.8, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.4, 'volume': 1},
            {'time': 3, 'open': 21.0, 'high': 22.0, 'low': 20.5, 'close': 20.0, 'volume': 1},
            {'time': 4, 'open': 19.0, 'high': 19.5, 'low': 18.0, 'close': 18.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            close_long_condition='time[0] == 3',
            open_trade_price_long='open[0]',
            close_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(symbol, strategy)

        full_results = backtester.run().copy(deep=True)
        full_execution = backtester.execution
        full_stats = dict(backtester.stats)

        rerun_results = backtester.run_from(start_index=1, previous_execution=full_execution)

        assert_frame_equal(full_results, rerun_results, check_dtype=False, check_like=False)
        self.assertEqual(full_stats['n_trades'], backtester.stats['n_trades'])
        self.assertEqual(full_stats['net_pnl'], backtester.stats['net_pnl'])

    def test_cost_profile_is_accepted_and_exposed_in_execution_policy(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.8, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.4, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            close_long_condition='time[0] == 2',
            open_trade_price_long='open[0]',
            close_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(symbol, strategy, cost_profile='forex')

        backtester.run()

        self.assertEqual(backtester.cost_profile, 'forex')
        self.assertEqual(backtester.stats['execution_policy']['cost_profile'], 'forex')

    def test_engine_events_match_history_and_trade_markers_for_manual_close(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.8, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.4, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.4, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            close_long_condition='time[0] == 2',
            open_trade_price_long='open[0]',
            close_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(symbol, strategy)

        results = backtester.run()
        events = backtester.execution.events
        markers = backtester.trade_markers

        self.assertEqual([event.kind for event in events], ['open', 'close'])
        self.assertEqual([event.side for event in events], ['long', 'long'])
        self.assertEqual([event.time for event in events], [2, 3])
        self.assertEqual(results.loc[1, 'order_type'], 'open_long')
        self.assertEqual(results.loc[2, 'order_type'], 'close_long')

        self.assertEqual(len(markers), 2)
        self.assertEqual(markers[0]['time'], 2)
        self.assertEqual(markers[1]['time'], 3)
        self.assertIn('Long open', markers[0]['text'])
        self.assertIn('Long close normal', markers[1]['text'])

    def test_stop_event_matches_history_and_trade_markers(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 18.0, 'close': 19.0, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1',
            open_trade_price_long='open[0]',
            stop_loss_long_price='long_open_price[0] - 1',
        )
        backtester = self.build_backtester(symbol, strategy)

        results = backtester.run()
        events = backtester.execution.events
        markers = backtester.trade_markers

        self.assertEqual([event.kind for event in events], ['open', 'stop'])
        self.assertEqual(events[1].metadata['stop_type'], 'loss')
        self.assertEqual(results.loc[1, 'order_type'], 'stop_long_loss')
        self.assertEqual(results.loc[1, 'long_open_price'], 20.0)
        self.assertEqual(results.loc[1, 'long_close_price'], 18.0)

        self.assertEqual(len(markers), 2)
        self.assertIn('Long open', markers[0]['text'])
        self.assertIn('Long stop loss', markers[1]['text'])
        self.assertEqual(markers[0]['time'], 2)
        self.assertEqual(markers[1]['time'], 2)

    def test_backtester_stops_accounting_when_balance_hits_zero(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 20.0, 'low': 0.0, 'close': 1.0, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 30.5, 'low': 29.5, 'close': 30.2, 'volume': 1},
            {'time': 4, 'open': 40.0, 'high': 40.0, 'low': 0.0, 'close': 1.0, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1 or time[0] == 3',
            stop_loss_long_price='0.0',
        )
        backtester = self.build_backtester(
            symbol,
            strategy,
            initial_balance=100.0,
            pip_size=1.0,
            pip_value_per_lot=10.0,
        )

        results = backtester.run()

        self.assertTrue(backtester.stats['bankrupt'])
        self.assertEqual(backtester.stats['bankruptcy_index'], 1)
        self.assertEqual(backtester.stats['final_balance'], 0.0)
        self.assertEqual(backtester.stats['n_trades'], 1)
        self.assertEqual(results.loc[1, 'trade_net_pnl'], -100.0)
        self.assertEqual(results.loc[2, 'account_balance'], 0.0)
        self.assertEqual(results.loc[3, 'account_balance'], 0.0)
        self.assertTrue(all(marker['time'] <= 2 for marker in backtester.trade_markers))

    def test_clear_b3_equity_daytrade_cost_and_tax_breakdown_is_reported(self):
        symbol = make_symbol([
            {'time': 1717601400, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1},
            {'time': 1717601460, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1},
            {'time': 1717601520, 'open': 12.0, 'high': 12.5, 'low': 11.5, 'close': 12.2, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1717601400',
            close_long_condition='time[0] == 1717601460',
            open_trade_price_long='open[0]',
            close_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(
            symbol,
            strategy,
            asset_type='b3_equity',
            initial_volume=10.0,
            cost_profile='clear_b3',
            spread_in_pips=0.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
        )
        backtester.set_params(
            **{
                'initial_balance': 10000.0,
                'asset_type': 'b3_equity',
                'initial_volume': 10.0,
                'pip_size': 1.0,
                'pip_value_per_lot': 1.0,
                'cost_profile': 'clear_b3',
                'spread_in_pips': 0.0,
                'entry_slippage_in_pips': 0.0,
                'close_slippage_in_pips': 0.0,
                'broker_cost_context': {
                    'broker_code': 'clear',
                    'market_domain': 'b3',
                },
            }
        )

        results = backtester.run()
        cost_breakdown = results.loc[2, 'trade_cost_breakdown']

        self.assertEqual(len(cost_breakdown), 3)
        self.assertAlmostEqual(results.loc[2, 'trade_gross_pnl'], 20.0, places=6)
        self.assertAlmostEqual(results.loc[2, 'trade_cost'], 4.04048, places=6)
        self.assertAlmostEqual(results.loc[2, 'trade_net_pnl'], 15.95952, places=6)
        self.assertAlmostEqual(backtester.stats['total_operational_cost'], 0.0506, places=6)
        self.assertAlmostEqual(backtester.stats['total_estimated_tax'], 3.98988, places=6)
        self.assertEqual(backtester.stats['execution_policy']['cost_profile'], 'clear_b3')
        self.assertEqual(backtester.stats['execution_policy']['asset_type'], 'b3_equity')
        self.assertEqual(backtester.stats['cost_breakdown_totals'][0]['id'], 'b3_equity_entry_fee')
        self.assertEqual(backtester.stats['cost_breakdown_totals'][1]['id'], 'b3_equity_exit_fee')
        self.assertEqual(backtester.stats['cost_breakdown_totals'][2]['id'], 'b3_equity_estimated_income_tax')
        self.assertEqual(backtester.stats['operational_cost_breakdown_totals'][0]['id'], 'b3_equity_entry_fee')
        self.assertEqual(backtester.stats['operational_cost_breakdown_totals'][1]['id'], 'b3_equity_exit_fee')
        self.assertEqual(backtester.stats['estimated_tax_breakdown_totals'][0]['id'], 'b3_equity_estimated_income_tax')
        self.assertTrue(backtester.stats['execution_policy']['taxes_modeled'])
        self.assertTrue(backtester.stats['execution_policy']['taxes_estimated'])

    def test_clear_b3_equity_common_trade_uses_common_tax_rate(self):
        symbol = make_symbol([
            {'time': 1717601400, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1},
            {'time': 1717601460, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1},
            {'time': 1717687800, 'open': 11.0, 'high': 11.5, 'low': 10.5, 'close': 11.2, 'volume': 1},
            {'time': 1717687860, 'open': 12.0, 'high': 12.5, 'low': 11.5, 'close': 12.2, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long_condition='time[0] == 1717601400',
            close_long_condition='time[0] == 1717687800',
            open_trade_price_long='open[0]',
            close_trade_price_long='open[0]',
        )
        backtester = self.build_backtester(
            symbol,
            strategy,
            asset_type='b3_equity',
            initial_volume=10.0,
            cost_profile='clear_b3',
            spread_in_pips=0.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
        )
        backtester.set_params(
            **{
                'initial_balance': 10000.0,
                'asset_type': 'b3_equity',
                'initial_volume': 10.0,
                'pip_size': 1.0,
                'pip_value_per_lot': 1.0,
                'cost_profile': 'clear_b3',
                'spread_in_pips': 0.0,
                'entry_slippage_in_pips': 0.0,
                'close_slippage_in_pips': 0.0,
                'broker_cost_context': {
                    'broker_code': 'clear',
                    'market_domain': 'b3',
                },
            }
        )

        results = backtester.run()
        cost_breakdown = results.loc[3, 'trade_cost_breakdown']
        estimated_tax = next(item for item in cost_breakdown if item['id'] == 'b3_equity_estimated_income_tax')

        self.assertAlmostEqual(results.loc[3, 'trade_gross_pnl'], 20.0, places=6)
        self.assertAlmostEqual(results.loc[3, 'trade_cost'], 3.0561, places=6)
        self.assertAlmostEqual(results.loc[3, 'trade_net_pnl'], 16.9439, places=6)
        self.assertAlmostEqual(float(estimated_tax['rate']), 0.15, places=9)
        self.assertFalse(bool(estimated_tax['day_trade']))


if __name__ == '__main__':
    unittest.main()
