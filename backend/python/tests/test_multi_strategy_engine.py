import unittest

import pandas as pd

from backend.python.lib.strategy import MultiStrategyBacktester, MultiStrategyExecutionEngine, Strategy
from backend.python.lib.symbol import Symbol


def make_symbol(rows):
    return Symbol('TEST', 'M1', len(rows), candles=rows)


class MultiStrategyExecutionEngineTest(unittest.TestCase):
    def build_strategy(
        self,
        *,
        open_long='False',
        close_long='False',
        open_short='False',
        close_short='False',
        stop_gain_long='999999.0',
        stop_loss_long='-999999.0',
        stop_gain_short='-999999.0',
        stop_loss_short='999999.0',
        trailing_stop_long='',
        trailing_stop_short='',
        priority='short',
        allow_inversion=False,
    ):
        strategy = Strategy()
        strategy.set_params(
            open_long_condition=open_long,
            close_long_condition=close_long,
            open_short_condition=open_short,
            close_short_condition=close_short,
            open_trade_price_long='open[0]',
            open_trade_price_short='open[0]',
            close_trade_price_long='open[0]',
            close_trade_price_short='open[0]',
            stop_gain_long_price=stop_gain_long,
            stop_loss_long_price=stop_loss_long,
            stop_gain_short_price=stop_gain_short,
            stop_loss_short_price=stop_loss_short,
            trailing_stop_long_price=trailing_stop_long,
            trailing_stop_short_price=trailing_stop_short,
            allow_invertion=allow_inversion,
            prioritize=priority,
            execution_mode='next_bar_open',
        )
        return strategy

    def build_entry(self, strategy_id, label, priority, strategy, enabled=True):
        return {
            'strategy_id': strategy_id,
            'strategy_label': label,
            'priority': priority,
            'enabled': enabled,
            'strategy': strategy,
        }

    def test_priority_blocks_opposite_direction_without_hedge(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
        ])
        fast_short = self.build_strategy(open_short='time[0] == 1')
        slow_long = self.build_strategy(open_long='time[0] == 1')

        engine = MultiStrategyExecutionEngine([
            self.build_entry('short-fast', 'Short Fast', 1, fast_short),
            self.build_entry('long-slow', 'Long Slow', 2, slow_long),
        ], symbol)

        result = engine.run()

        open_events = [event for event in result.events if event.kind == 'open']
        skip_events = [event for event in result.events if event.kind == 'skip_open']

        self.assertEqual(len(open_events), 1)
        self.assertEqual(open_events[0].strategy_id, 'short-fast')
        self.assertEqual(open_events[0].side, 'short')
        self.assertEqual(len(skip_events), 1)
        self.assertEqual(skip_events[0].strategy_id, 'long-slow')

    def test_parallel_sleeves_mode_allows_opposite_directions_to_open_together(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
        ])
        fast_short = self.build_strategy(open_short='time[0] == 1')
        slow_long = self.build_strategy(open_long='time[0] == 1')

        backtester = MultiStrategyBacktester(symbol, [
            self.build_entry('short-fast', 'Short Fast', 1, fast_short),
            self.build_entry('long-slow', 'Long Slow', 2, slow_long),
        ], portfolio_mode='parallel_sleeves')
        backtester.set_params(
            initial_balance=10000.0,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
            cost_profile='oanda',
            spread_in_pips=0.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            portfolio_mode='parallel_sleeves',
        )

        backtester.run()

        open_events = [event for event in backtester.execution.events if event.kind == 'open']
        skip_events = [event for event in backtester.execution.events if event.kind == 'skip_open']

        self.assertEqual(len(open_events), 2)
        self.assertEqual(len(skip_events), 0)
        self.assertEqual(backtester.stats['execution_policy']['portfolio_mode'], 'parallel_sleeves')

    def test_same_direction_strategies_can_open_together_and_close_independently(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
            {'time': 4, 'open': 40.0, 'high': 41.0, 'low': 39.0, 'close': 40.5, 'volume': 1},
        ])
        first = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 2',
        )
        second = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 3',
        )

        engine = MultiStrategyExecutionEngine([
            self.build_entry('first', 'First', 1, first),
            self.build_entry('second', 'Second', 2, second),
        ], symbol)

        result = engine.run()

        first_state = next(state for state in result.strategy_states if state.strategy_id == 'first')
        second_state = next(state for state in result.strategy_states if state.strategy_id == 'second')
        close_events = [event for event in result.events if event.kind == 'close']

        self.assertEqual(len([event for event in result.events if event.kind == 'open']), 2)
        self.assertEqual(len(close_events), 2)
        self.assertEqual(first_state.position, 0)
        self.assertEqual(second_state.position, 0)

        first_history = first_state.metadata['history']
        second_history = second_state.metadata['history']
        self.assertEqual(first_history.loc[2, 'order_type'], 'close_long')
        self.assertTrue(pd.isna(second_history.loc[2, 'long_close_price']))
        self.assertEqual(second_history.loc[3, 'order_type'], 'close_long')

    def test_multi_strategy_backtester_builds_portfolio_stats_and_breakdown(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
            {'time': 4, 'open': 40.0, 'high': 41.0, 'low': 39.0, 'close': 40.5, 'volume': 1},
        ])
        first = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 2',
        )
        second = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 3',
        )

        backtester = MultiStrategyBacktester(symbol, [
            self.build_entry('first', 'First', 1, first),
            self.build_entry('second', 'Second', 2, second),
        ])
        backtester.set_params(
            initial_balance=10000.0,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
            spread_in_pips=0.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
        )

        results = backtester.run()

        self.assertEqual(backtester.stats['n_trades'], 2)
        self.assertEqual(len(backtester.stats['portfolio_strategy_stats']), 2)
        self.assertEqual(backtester.stats['net_pnl'], 30.0)
        self.assertEqual(backtester.stats['final_balance'], 10030.0)
        self.assertIn('portfolio_analytics', backtester.stats)
        self.assertEqual(backtester.stats['portfolio_analytics']['max_concurrent_strategies'], 2)
        self.assertEqual(len(backtester.stats['portfolio_analytics']['pairwise']), 1)
        self.assertEqual(results.loc[2, 'trade_net_pnl'], 10.0)
        self.assertEqual(results.loc[3, 'trade_net_pnl'], 20.0)
        self.assertGreaterEqual(len(backtester.trade_markers), 4)

    def test_multi_strategy_backtester_accepts_cost_profile(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        first = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 2',
        )
        second = self.build_strategy(
            open_short='time[0] == 1',
            close_short='time[0] == 2',
        )

        backtester = MultiStrategyBacktester(symbol, [
            self.build_entry('first', 'First', 1, first),
            self.build_entry('second', 'Second', 2, second),
        ], portfolio_mode='parallel_sleeves')
        backtester.set_params(
            initial_balance=10000.0,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
            cost_profile='forex',
            spread_in_pips=0.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            portfolio_mode='parallel_sleeves',
        )

        backtester.run()

        self.assertEqual(backtester.cost_profile, 'forex')
        self.assertEqual(backtester.stats['execution_policy']['cost_profile'], 'forex')

    def test_explicit_portfolio_fixed_volume_uses_requested_lot_when_margin_allows(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 2',
        )
        backtester = MultiStrategyBacktester(symbol, [{
            'strategy_id': 'fixed-explicit',
            'strategy_label': 'Fixed Explicit',
            'priority': 1,
            'enabled': True,
            'symbol': 'TEST',
            'timeframe': 'M1',
            'portfolio_id': 'portfolio-1',
            'portfolio_label': 'Portfolio 1',
            'pipeline_id': 'pipe-1',
            'pipeline_label': 'Pipe 1',
            'volume_mode': 'fixed_volume',
            'fixed_volume': 0.5,
            'strategy': strategy,
        }])
        backtester.set_params(
            initial_balance=25_000.0,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
            spread_in_pips=0.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            capital_model={
                'marginModel': 'forex_notional',
                'accountLeverage': 50,
                'contractSizePerLot': 100_000,
                'minLot': 0.01,
                'lotStep': 0.01,
            },
        )

        results = backtester.run()

        self.assertTrue(backtester.capital_replay_enabled)
        self.assertEqual(len(backtester.replayed_trades), 1)
        self.assertAlmostEqual(backtester.replayed_trades[0]['executed_volume'], 0.5)
        self.assertAlmostEqual(backtester.replayed_trades[0]['required_margin'], 20_000.0)
        self.assertAlmostEqual(results.loc[2, 'trade_net_pnl'], 5.0)

    def test_explicit_portfolio_max_affordable_uses_available_margin(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 11.0, 'low': 9.0, 'close': 10.5, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 21.0, 'low': 19.0, 'close': 20.5, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 31.0, 'low': 29.0, 'close': 30.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long='time[0] == 1',
            close_long='time[0] == 2',
        )
        backtester = MultiStrategyBacktester(symbol, [{
            'strategy_id': 'max-explicit',
            'strategy_label': 'Max Explicit',
            'priority': 1,
            'enabled': True,
            'symbol': 'TEST',
            'timeframe': 'M1',
            'portfolio_id': 'portfolio-1',
            'portfolio_label': 'Portfolio 1',
            'pipeline_id': 'pipe-1',
            'pipeline_label': 'Pipe 1',
            'volume_mode': 'max_affordable',
            'strategy': strategy,
        }])
        backtester.set_params(
            initial_balance=10_000.0,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=1.0,
            pip_value_per_lot=1.0,
            spread_in_pips=0.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            capital_model={
                'marginModel': 'forex_notional',
                'accountLeverage': 50,
                'contractSizePerLot': 100_000,
                'minLot': 0.01,
                'lotStep': 0.01,
            },
        )

        results = backtester.run()

        self.assertTrue(backtester.capital_replay_enabled)
        self.assertEqual(len(backtester.replayed_trades), 1)
        self.assertAlmostEqual(backtester.replayed_trades[0]['executed_volume'], 0.25)
        self.assertAlmostEqual(backtester.replayed_trades[0]['required_margin'], 10_000.0)
        self.assertEqual(backtester.stats['portfolio_event_counts']['skip_open_sizing'], 0)
        self.assertAlmostEqual(results.loc[2, 'trade_net_pnl'], 2.5)

    def test_long_stop_loss_generates_stop_event_and_realized_pnl(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2, 'volume': 1},
            {'time': 2, 'open': 10.0, 'high': 10.1, 'low': 8.0, 'close': 8.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long='time[0] == 1',
            stop_loss_long='9.0',
            stop_gain_long='20.0',
        )

        engine = MultiStrategyExecutionEngine([
            self.build_entry('stopper', 'Stopper', 1, strategy),
        ], symbol)
        result = engine.run()

        stop_events = [event for event in result.events if event.kind == 'stop']
        self.assertEqual(len(stop_events), 1)
        self.assertEqual(stop_events[0].metadata['stop_type'], 'loss')
        self.assertEqual(stop_events[0].price, 8.0)

        state = result.strategy_states[0]
        history = state.metadata['history']
        self.assertEqual(history.loc[1, 'order_type'], 'stop_long_loss')
        self.assertEqual(history.loc[1, 'realized_pnl'], -2.0)

    def test_trailing_stop_is_blocked_on_entry_bar_and_hits_on_later_bar(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 12.0, 'low': 9.0, 'close': 11.0, 'volume': 1},
            {'time': 2, 'open': 11.0, 'high': 13.0, 'low': 11.0, 'close': 12.0, 'volume': 1},
            {'time': 3, 'open': 12.0, 'high': 12.5, 'low': 11.0, 'close': 11.5, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long='time[0] == 1',
            stop_gain_long='20.0',
            stop_loss_long='0.0',
            trailing_stop_long='close[0] - 0.5',
        )

        engine = MultiStrategyExecutionEngine([
            self.build_entry('trail', 'Trail', 1, strategy),
        ], symbol)
        result = engine.run()

        stop_events = [event for event in result.events if event.kind == 'stop']
        self.assertEqual(len(stop_events), 1)
        self.assertEqual(stop_events[0].metadata['stop_type'], 'trail')
        self.assertEqual(stop_events[0].bar_index, 2)

        state = result.strategy_states[0]
        history = state.metadata['history']
        self.assertIsNone(history.loc[0, 'order_type'])
        self.assertEqual(history.loc[1, 'long_trailing_stop_price'], 11.5)
        self.assertEqual(history.loc[2, 'order_type'], 'stop_long_trail')

    def test_inversion_closes_then_reopens_on_same_execution_bar(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 10.2, 'low': 9.8, 'close': 10.0, 'volume': 1},
            {'time': 2, 'open': 11.0, 'high': 11.2, 'low': 10.8, 'close': 11.0, 'volume': 1},
            {'time': 3, 'open': 12.0, 'high': 12.2, 'low': 11.8, 'close': 12.0, 'volume': 1},
        ])
        strategy = self.build_strategy(
            open_long='time[0] == 1',
            open_short='time[0] == 2',
            allow_inversion=True,
        )

        engine = MultiStrategyExecutionEngine([
            self.build_entry('flip', 'Flip', 1, strategy),
        ], symbol)
        result = engine.run()

        state = result.strategy_states[0]
        history = state.metadata['history']
        self.assertEqual(history.loc[1, 'order_type'], 'open_long')
        self.assertEqual(history.loc[2, 'order_type'], 'open_short')
        self.assertEqual(state.position, -1)
        self.assertIsNone(state.pending_action)

        close_events = [event for event in result.events if event.kind == 'close']
        open_events = [event for event in result.events if event.kind == 'open']
        self.assertEqual(close_events[-1].bar_index, 2)
        self.assertEqual(open_events[-1].bar_index, 2)
        self.assertEqual(close_events[-1].side, 'long')
        self.assertEqual(open_events[-1].side, 'short')

    def test_multi_strategy_backtester_stops_accounting_when_balance_hits_zero(self):
        symbol = make_symbol([
            {'time': 1, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.0, 'volume': 1},
            {'time': 2, 'open': 20.0, 'high': 20.0, 'low': 0.0, 'close': 1.0, 'volume': 1},
            {'time': 3, 'open': 30.0, 'high': 30.5, 'low': 29.5, 'close': 30.0, 'volume': 1},
            {'time': 4, 'open': 40.0, 'high': 40.0, 'low': 0.0, 'close': 1.0, 'volume': 1},
        ])
        bankrupting = self.build_strategy(
            open_long='time[0] == 1',
            stop_loss_long='0.0',
        )
        later_trade = self.build_strategy(
            open_long='time[0] == 2',
            close_long='time[0] == 3',
        )

        backtester = MultiStrategyBacktester(symbol, [
            self.build_entry('bankrupting', 'Bankrupting', 1, bankrupting),
            self.build_entry('later', 'Later', 2, later_trade),
        ])
        backtester.set_params(
            initial_balance=100.0,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=1.0,
            pip_value_per_lot=10.0,
            spread_in_pips=0.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
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


if __name__ == '__main__':
    unittest.main()
