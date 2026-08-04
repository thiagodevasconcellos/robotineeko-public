from .engine import StrategyExecutionEngine


import re


class Strategy:
    def __init__(self):
        self.trade_conditions = {
            'open_long': False,
            'close_long': False,
            'open_short': False,
            'close_short': False,
        }

        self.trade_prices = {
            'open_trade': {
                'long': 'close[0]',
                'short': 'close[0]',
            },
            'close_trade': {
                'long': 'close[0]',
                'short': 'close[0]',
            },
            'stop_long': {
                'gain': 'high[0]',
                'loss': 'low[0]',
                'trail': '',
            },
            'stop_short': {
                'gain': 'low[0]',
                'loss': 'high[0]',
                'trail': '',
            },
        }

        self.trade_columns = [
            'long_open_timestamp',
            'long_open_price',
            'long_close_timestamp',
            'last_long_close_timestamp',
            'long_close_price',
            'long_open_idx',
            'long_order_life',
            'short_open_timestamp',
            'short_open_price',
            'short_close_timestamp',
            'last_short_close_timestamp',
            'short_close_price',
            'short_open_idx',
            'short_order_life',
            'last_trade_close_timestamp',
            'opened_order_life',
            'position',
            'unrealized_pnl',
            'realized_pnl',
            'order_type',
            'long_take_profit_price',
            'long_stop_loss_price',
            'long_trailing_stop_price',
            'short_take_profit_price',
            'short_stop_loss_price',
            'short_trailing_stop_price',
            'pending_action_kind',
            'pending_action_stop_type',
        ]

        self.position = 0
        self.symbol = None
        self.history = {}
        self.last_execution = None

        self.allow_invertion = False
        self.prioritize = 'short'
        self.execution_mode = 'next_bar_open'
        self.execution_slippage = {
            'pip_size': 0.0001,
            'entry_in_pips': 0.0,
            'close_in_pips': 0.0,
            'take_profit_in_pips': 0.0,
            'stop_loss_in_pips': 0.0,
            'trailing_stop_in_pips': 0.0,
            'volatility_multiplier': 0.0,
        }

    def set_params(
        self,
        open_long_condition=False,
        close_long_condition=False,
        open_short_condition=False,
        close_short_condition=False,
        open_trade_price_long='close[0]',
        open_trade_price_short='close[0]',
        close_trade_price_long='close[0]',
        close_trade_price_short='close[0]',
        stop_gain_long_price='high[0]',
        stop_loss_long_price='low[0]',
        stop_gain_short_price='low[0]',
        stop_loss_short_price='high[0]',
        trailing_stop_long_price='',
        trailing_stop_short_price='',
        allow_invertion=False,
        prioritize='short',
        execution_mode='next_bar_open',
    ):
        self.trade_conditions['open_long'] = open_long_condition
        self.trade_conditions['close_long'] = close_long_condition
        self.trade_conditions['open_short'] = open_short_condition
        self.trade_conditions['close_short'] = close_short_condition

        self.trade_prices['open_trade']['long'] = open_trade_price_long
        self.trade_prices['open_trade']['short'] = open_trade_price_short
        self.trade_prices['close_trade']['long'] = close_trade_price_long
        self.trade_prices['close_trade']['short'] = close_trade_price_short
        self.trade_prices['stop_long']['gain'] = stop_gain_long_price
        self.trade_prices['stop_long']['loss'] = stop_loss_long_price
        self.trade_prices['stop_long']['trail'] = trailing_stop_long_price
        self.trade_prices['stop_short']['gain'] = stop_gain_short_price
        self.trade_prices['stop_short']['loss'] = stop_loss_short_price
        self.trade_prices['stop_short']['trail'] = trailing_stop_short_price

        self.allow_invertion = allow_invertion
        self.prioritize = prioritize if prioritize in ['long', 'short'] else 'short'
        self.execution_mode = (
            execution_mode
            if execution_mode in ['same_bar', 'next_bar_open']
            else 'next_bar_open'
        )

    def set_execution_slippage(
        self,
        pip_size=0.0001,
        entry_in_pips=0.0,
        close_in_pips=0.0,
        take_profit_in_pips=0.0,
        stop_loss_in_pips=0.0,
        trailing_stop_in_pips=0.0,
        minimum_stop_distance_in_pips=0.0,
        volatility_multiplier=0.0,
    ):
        self.execution_slippage = {
            'pip_size': float(pip_size),
            'entry_in_pips': max(float(entry_in_pips), 0.0),
            'close_in_pips': max(float(close_in_pips), 0.0),
            'take_profit_in_pips': max(float(take_profit_in_pips), 0.0),
            'stop_loss_in_pips': max(float(stop_loss_in_pips), 0.0),
            'trailing_stop_in_pips': max(float(trailing_stop_in_pips), 0.0),
            'minimum_stop_distance_in_pips': max(float(minimum_stop_distance_in_pips), 0.0),
            'volatility_multiplier': max(float(volatility_multiplier), 0.0),
        }

    def execute(self, symbol, start_index=0, previous_execution=None):
        engine = StrategyExecutionEngine(self, symbol)
        self.symbol = symbol
        self.position = 0
        self.last_execution = engine.run(
            start_index=start_index,
            previous_execution=previous_execution,
        )
        self.history = self.last_execution.history
        self.position = self.last_execution.final_position
        return self.last_execution

    def run(self, symbol):
        execution = self.execute(symbol)
        return self.history

    def get_expression_strings(self):
        expressions = []

        expressions.extend(self.trade_conditions.values())

        for section in self.trade_prices.values():
            expressions.extend(section.values())

        return [expr for expr in expressions if isinstance(expr, str)]

    def get_required_feature_names(self):
        required_names = set()

        for expr in self.get_expression_strings():
            matches = re.findall(r'([A-Za-z_]\w*)\[\d+\]', expr)
            required_names.update(matches)

        return sorted(required_names)
