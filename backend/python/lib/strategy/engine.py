import ast
import re
import pandas as pd

try:
    from ...indicator_registry import get_indicator_class, split_indicator_feature_name
    from .expression_identifiers import build_expression_safe_identifier
except ImportError:
    from indicator_registry import get_indicator_class, split_indicator_feature_name
    from expression_identifiers import build_expression_safe_identifier
from .models import StrategyEngineResult, TradeEvent


class StrategyExecutionEngine:
    def __init__(self, strategy, symbol):
        self.strategy = strategy
        self.symbol = symbol
        self.position = 0
        self.history = {}
        self.events = []
        self.pending_action = None

    def run(self, start_index=0, previous_execution=None):
        self._ensure_required_indicators()
        self._start_history(start_index=start_index, previous_execution=previous_execution)

        for t in range(start_index, len(self.symbol.candles)):
            line = self.symbol.candles.iloc[t]

            self._new_history_line(t)
            self._update_history_from_candle(line)

            if self.strategy.execution_mode == 'next_bar_open':
                self._execute_pending_action(t, line)

            self._update_dynamic_stop_levels(t)

            trigger_long, trigger_short = self._evaluate_open_triggers(t)
            self._process_position_state(
                t,
                line,
                trigger_long=trigger_long,
                trigger_short=trigger_short,
            )

            self._calc_order_life(t)
            self._calc_pnl(line)

        return StrategyEngineResult(
            history=pd.DataFrame(self.history),
            events=list(self.events),
            final_position=self.position,
            pending_action=dict(self.pending_action) if self.pending_action else None,
        )

    def _evaluate_open_triggers(self, t):
        trigger_long = False
        trigger_short = False

        if self.strategy.trade_conditions['open_long']:
            trigger_long = self._parse_condition(t, self.strategy.trade_conditions['open_long'])

        if self.strategy.trade_conditions['open_short']:
            trigger_short = self._parse_condition(t, self.strategy.trade_conditions['open_short'])

        return trigger_long, trigger_short

    def _process_position_state(self, t, line, *, trigger_long, trigger_short):
        if self.position == 0:
            self._process_flat_position(t, line, trigger_long=trigger_long, trigger_short=trigger_short)
            return

        if self.position == 1:
            self._process_long_position(t, line, trigger_short=trigger_short)
            return

        if self.position == -1:
            self._process_short_position(t, line, trigger_long=trigger_long)

    def _process_flat_position(self, t, line, *, trigger_long, trigger_short):
        if trigger_long and trigger_short:
            self._process_conflicting_open_signals(t, line)
            return

        if trigger_long:
            self._open_long_or_queue(t, line)
            return

        if trigger_short:
            self._open_short_or_queue(t, line)

    def _process_conflicting_open_signals(self, t, line):
        if self.strategy.prioritize == 'long':
            self._open_long_or_queue(t, line)
            return
        self._open_short_or_queue(t, line)

    def _process_long_position(self, t, line, *, trigger_short):
        stop_hit = self._resolve_long_stop(t, line)
        if stop_hit is not None:
            stop_type, stop_price = stop_hit
            self._stop_long(t, line, stop_type, executed_price=stop_price)
            return

        close_long_triggered = self._evaluate_close_trigger(t, 'long')
        if close_long_triggered:
            self._close_long_or_queue(t, line)
            return

        if trigger_short and self.strategy.allow_invertion:
            self._invert_long_to_short(t, line)

    def _process_short_position(self, t, line, *, trigger_long):
        stop_hit = self._resolve_short_stop(t, line)
        if stop_hit is not None:
            stop_type, stop_price = stop_hit
            self._stop_short(t, line, stop_type, executed_price=stop_price)
            return

        close_short_triggered = self._evaluate_close_trigger(t, 'short')
        if close_short_triggered:
            self._close_short_or_queue(t, line)
            return

        if trigger_long and self.strategy.allow_invertion:
            self._invert_short_to_long(t, line)

    def _evaluate_close_trigger(self, t, side):
        condition_key = 'close_long' if side == 'long' else 'close_short'
        if not self.strategy.trade_conditions[condition_key]:
            return False
        return self._parse_condition(t, self.strategy.trade_conditions[condition_key])

    def _invert_long_to_short(self, t, line):
        if self.strategy.execution_mode == 'next_bar_open':
            self._queue_action('invert_to_short')
            return
        if self.position == 1:
            self._close_long(t, line)
        if self.position == 0:
            self._open_short(t, line, is_invertion=True)

    def _invert_short_to_long(self, t, line):
        if self.strategy.execution_mode == 'next_bar_open':
            self._queue_action('invert_to_long')
            return
        if self.position == -1:
            self._close_short(t, line)
        if self.position == 0:
            self._open_long(t, line, is_invertion=True)

    def _ensure_required_indicators(self):
        expressions = []

        expressions.extend(self.strategy.trade_conditions.values())

        for section in self.strategy.trade_prices.values():
            expressions.extend(section.values())

        required_names = set()

        for expr in expressions:
            if isinstance(expr, str):
                required_names.update(self._extract_feature_names(expr))

        internal_history_names = set(self.strategy.trade_columns)
        missing_names = [
            name
            for name in required_names
            if name not in self.symbol.features and name not in internal_history_names
        ]

        for feature_name in missing_names:
            self._instantiate_indicator_for_feature(feature_name)

    def _extract_feature_names(self, expr):
        if not isinstance(expr, str):
            return set()

        matches = re.findall(r'([A-Za-z_]\w*)\[\d+\]', expr)
        return set(matches)

    def _instantiate_indicator_for_feature(self, feature_name):
        if feature_name in self.symbol.features:
            return

        indicator_name, params = self._split_indicator_feature_name(feature_name)
        if indicator_name is None:
            return

        indicator_class = get_indicator_class(indicator_name)
        if indicator_class is None:
            return

        indicator_class(self.symbol, *params)

    def _split_indicator_feature_name(self, feature_name):
        indicator_name, raw_params = split_indicator_feature_name(feature_name)

        if indicator_name is not None:
            parsed_params = [self._parse_indicator_param(param) for param in raw_params]
            return indicator_name, parsed_params

        return None, None

    def _parse_indicator_param(self, value):
        if not isinstance(value, str):
            return value

        lower = value.lower()

        if lower == 'true':
            return True
        if lower == 'false':
            return False
        if lower == 'none':
            return None

        try:
            return ast.literal_eval(value)
        except Exception:
            return value

    def _build_history_columns(self):
        columns = []

        for col in self.symbol.features:
            if col not in columns:
                columns.append(col)

        for col in self.strategy.trade_columns:
            if col not in columns:
                columns.append(col)

        return columns

    def _start_history(self, start_index=0, previous_execution=None):
        history_columns = self._build_history_columns()
        self.history = {col: [] for col in history_columns}
        self.events = []
        self.position = 0
        self.pending_action = None

        if previous_execution is None or start_index <= 0:
            return

        previous_history = previous_execution.history
        if isinstance(previous_history, pd.DataFrame):
            previous_history_df = previous_history.copy()
        else:
            previous_history_df = pd.DataFrame(previous_history)

        prefix_length = min(start_index, len(previous_history_df))

        for col in history_columns:
            if col in previous_history_df.columns:
                self.history[col] = previous_history_df[col].iloc[:prefix_length].tolist()
            else:
                self.history[col] = [None] * prefix_length

        self.events = [
            event
            for event in getattr(previous_execution, 'events', [])
            if event.bar_index < start_index
        ]

        if prefix_length > 0 and 'position' in self.history:
            last_position = self.history['position'][-1]
            self.position = int(last_position) if last_position is not None and pd.notna(last_position) else 0
            pending_kind = self.history.get('pending_action_kind', [None])[-1]
            pending_stop_type = self.history.get('pending_action_stop_type', [None])[-1]
            if pending_kind is not None and pd.notna(pending_kind):
                self.pending_action = {
                    'kind': pending_kind,
                    'stop_type': pending_stop_type if pending_stop_type is not None and pd.notna(pending_stop_type) else None,
                }

    def _queue_action(self, action_kind, stop_type=None):
        self.pending_action = {
            'kind': action_kind,
            'stop_type': stop_type,
        }
        if 'pending_action_kind' in self.history and self.history['pending_action_kind']:
            self.history['pending_action_kind'][-1] = action_kind
        if 'pending_action_stop_type' in self.history and self.history['pending_action_stop_type']:
            self.history['pending_action_stop_type'][-1] = stop_type

    def _execute_pending_action(self, t, line):
        if not self.pending_action:
            return

        action = self.pending_action
        self.pending_action = None
        kind = action['kind']

        if kind == 'open_long':
            self._open_long(t, line)
        elif kind == 'open_short':
            self._open_short(t, line)
        elif kind == 'close_long':
            self._close_long(t, line)
        elif kind == 'close_short':
            self._close_short(t, line)
        elif kind == 'invert_to_long':
            if self.position == -1:
                self._close_short(t, line)
            if self.position == 0:
                self._open_long(t, line, is_invertion=True)
        elif kind == 'invert_to_short':
            if self.position == 1:
                self._close_long(t, line)
            if self.position == 0:
                self._open_short(t, line, is_invertion=True)

    def _open_long_or_queue(self, t, line):
        if self.strategy.execution_mode == 'next_bar_open':
            self._queue_action('open_long')
            return
        self._open_long(t, line)

    def _open_short_or_queue(self, t, line):
        if self.strategy.execution_mode == 'next_bar_open':
            self._queue_action('open_short')
            return
        self._open_short(t, line)

    def _close_long_or_queue(self, t, line):
        if self.strategy.execution_mode == 'next_bar_open':
            self._queue_action('close_long')
            return
        self._close_long(t, line)

    def _close_short_or_queue(self, t, line):
        if self.strategy.execution_mode == 'next_bar_open':
            self._queue_action('close_short')
            return
        self._close_short(t, line)

    def _new_history_line(self, t):
        for col in self.history:
            self.history[col].append(None)

        current_length = len(next(iter(self.history.values())))

        if current_length <= 1:
            return

        repeat_cols = [
            'long_open_timestamp',
            'long_open_price',
            'long_open_idx',
            'long_take_profit_price',
            'long_stop_loss_price',
            'long_trailing_stop_price',
            'last_long_close_timestamp',
            'short_open_timestamp',
            'short_open_price',
            'short_open_idx',
            'short_take_profit_price',
            'short_stop_loss_price',
            'short_trailing_stop_price',
            'last_short_close_timestamp',
            'last_trade_close_timestamp',
            'position',
        ]

        for repeat_col in repeat_cols:
            self.history[repeat_col][-1] = self.history[repeat_col][-2]

        for position_value, side in ((1, 'long'), (-1, 'short')):
            if self.history['position'][-1] != position_value:
                self.history[f'{side}_open_timestamp'][-1] = None
                self.history[f'{side}_open_price'][-1] = None
                self.history[f'{side}_open_idx'][-1] = None
                self.history[f'{side}_close_timestamp'][-1] = None
                self.history[f'{side}_close_price'][-1] = None
                self.history[f'{side}_take_profit_price'][-1] = None
                self.history[f'{side}_stop_loss_price'][-1] = None
                self.history[f'{side}_trailing_stop_price'][-1] = None

        if 1 not in self.history['position'][-2:]:
            self.history['long_open_idx'][-1] = None

        if -1 not in self.history['position'][-2:]:
            self.history['short_open_idx'][-1] = None

        self._calc_order_life(t)

    def _update_history_from_candle(self, line):
        for col in self.symbol.features:
            self.history[col][-1] = line[col]

    def _parse_optional_price(self, t, price):
        if price is None:
            return None

        if isinstance(price, str) and not price.strip():
            return None

        return self._parse_price(t, price)

    def _update_dynamic_stop_levels(self, t):
        if self.position == 1:
            stop_loss_candidate = self._parse_optional_price(
                t,
                self.strategy.trade_prices['stop_long']['loss'],
            )
            take_profit_candidate = self._parse_optional_price(
                t,
                self.strategy.trade_prices['stop_long']['gain'],
            )
            self.history['long_stop_loss_price'][-1] = self._enforce_minimum_stop_distance('long', 'loss', stop_loss_candidate)
            self.history['long_take_profit_price'][-1] = self._enforce_minimum_stop_distance('long', 'gain', take_profit_candidate)
            candidate = self._parse_optional_price(
                t,
                self.strategy.trade_prices['stop_long']['trail'],
            )
            candidate = self._enforce_minimum_stop_distance('long', 'trail', candidate)
            previous = self.history['long_trailing_stop_price'][-1]
            if candidate is None:
                return
            if previous is None or pd.isna(previous):
                self.history['long_trailing_stop_price'][-1] = candidate
            else:
                self.history['long_trailing_stop_price'][-1] = max(float(previous), float(candidate))
            return

        if self.position == -1:
            stop_loss_candidate = self._parse_optional_price(
                t,
                self.strategy.trade_prices['stop_short']['loss'],
            )
            take_profit_candidate = self._parse_optional_price(
                t,
                self.strategy.trade_prices['stop_short']['gain'],
            )
            self.history['short_stop_loss_price'][-1] = self._enforce_minimum_stop_distance('short', 'loss', stop_loss_candidate)
            self.history['short_take_profit_price'][-1] = self._enforce_minimum_stop_distance('short', 'gain', take_profit_candidate)
            candidate = self._parse_optional_price(
                t,
                self.strategy.trade_prices['stop_short']['trail'],
            )
            candidate = self._enforce_minimum_stop_distance('short', 'trail', candidate)
            previous = self.history['short_trailing_stop_price'][-1]
            if candidate is None:
                return
            if previous is None or pd.isna(previous):
                self.history['short_trailing_stop_price'][-1] = candidate
            else:
                self.history['short_trailing_stop_price'][-1] = min(float(previous), float(candidate))
            return

        self.history['long_take_profit_price'][-1] = None
        self.history['long_stop_loss_price'][-1] = None
        self.history['long_trailing_stop_price'][-1] = None
        self.history['short_take_profit_price'][-1] = None
        self.history['short_stop_loss_price'][-1] = None
        self.history['short_trailing_stop_price'][-1] = None

    def _previous_bar_range_pips(self):
        pip_size = float(self.strategy.execution_slippage.get('pip_size') or 0.0)
        if pip_size <= 0 or len(self.history.get('high', [])) < 2 or len(self.history.get('low', [])) < 2:
            return 0.0

        previous_high = self.history['high'][-2]
        previous_low = self.history['low'][-2]

        if previous_high is None or previous_low is None or pd.isna(previous_high) or pd.isna(previous_low):
            return 0.0

        return max((float(previous_high) - float(previous_low)) / pip_size, 0.0)

    def _effective_slippage_pips(self, base_pips):
        base_value = max(float(base_pips or 0.0), 0.0)
        volatility_multiplier = max(float(self.strategy.execution_slippage.get('volatility_multiplier') or 0.0), 0.0)
        return base_value + (self._previous_bar_range_pips() * volatility_multiplier)

    def _minimum_stop_distance_price(self):
        pip_size = float(self.strategy.execution_slippage.get('pip_size') or 0.0)
        minimum_stop_distance_in_pips = max(float(self.strategy.execution_slippage.get('minimum_stop_distance_in_pips') or 0.0), 0.0)
        if pip_size <= 0 or minimum_stop_distance_in_pips <= 0:
            return 0.0
        return minimum_stop_distance_in_pips * pip_size

    def _enforce_minimum_stop_distance(self, side, stop_kind, candidate_price):
        if candidate_price is None:
            return None

        minimum_distance_price = self._minimum_stop_distance_price()
        if minimum_distance_price <= 0:
            return float(candidate_price)

        open_price_key = 'long_open_price' if side == 'long' else 'short_open_price'
        open_price = self.history.get(open_price_key, [None])[-1]
        if open_price is None or pd.isna(open_price):
            return float(candidate_price)

        open_price = float(open_price)
        candidate_price = float(candidate_price)

        if side == 'long':
            if stop_kind == 'loss':
                return min(candidate_price, open_price - minimum_distance_price)
            return max(candidate_price, open_price + minimum_distance_price)

        if stop_kind == 'loss':
            return max(candidate_price, open_price + minimum_distance_price)
        return min(candidate_price, open_price - minimum_distance_price)

    def _apply_execution_slippage(self, price, side, event_kind):
        if price is None:
            return None

        slippage_key_map = {
            'entry': 'entry_in_pips',
            'close': 'close_in_pips',
            'take_profit': 'take_profit_in_pips',
            'stop_loss': 'stop_loss_in_pips',
            'trailing_stop': 'trailing_stop_in_pips',
        }
        config_key = slippage_key_map.get(event_kind)
        if config_key is None:
            return float(price)

        pip_size = float(self.strategy.execution_slippage.get('pip_size') or 0.0)
        if pip_size <= 0:
            return float(price)

        slippage_pips = self._effective_slippage_pips(self.strategy.execution_slippage.get(config_key, 0.0))
        slippage_price = slippage_pips * pip_size
        execution_price = float(price)

        if side == 'long':
            if event_kind == 'entry':
                return execution_price + slippage_price
            return execution_price - slippage_price

        if side == 'short':
            if event_kind == 'entry':
                return execution_price - slippage_price
            return execution_price + slippage_price

        return execution_price

    def _resolve_long_stop(self, t, line):
        stop_loss_price = self.history['long_stop_loss_price'][-1]
        trailing_price = self.history['long_trailing_stop_price'][-1]
        take_profit_price = self.history['long_take_profit_price'][-1]

        if stop_loss_price is not None and line['low'] <= stop_loss_price:
            return 'loss', float(line['low'])

        is_entry_bar = self.history['long_open_idx'][-1] == t

        if (
            trailing_price is not None
            and pd.notna(trailing_price)
            and not is_entry_bar
            and line['low'] <= float(trailing_price)
        ):
            return 'trail', float(line['low'])

        if take_profit_price is not None and line['high'] >= take_profit_price:
            return 'gain', float(take_profit_price)

        return None

    def _resolve_short_stop(self, t, line):
        stop_loss_price = self.history['short_stop_loss_price'][-1]
        trailing_price = self.history['short_trailing_stop_price'][-1]
        take_profit_price = self.history['short_take_profit_price'][-1]

        if stop_loss_price is not None and line['high'] >= stop_loss_price:
            return 'loss', float(line['high'])

        is_entry_bar = self.history['short_open_idx'][-1] == t

        if (
            trailing_price is not None
            and pd.notna(trailing_price)
            and not is_entry_bar
            and line['high'] >= float(trailing_price)
        ):
            return 'trail', float(line['high'])

        if take_profit_price is not None and line['low'] <= take_profit_price:
            return 'gain', float(take_profit_price)

        return None

    def _open_long(self, t, line, is_invertion=False):
        self.position = 1
        base_price = self._parse_price(
            t,
            self.strategy.trade_prices['open_trade']['long'],
        )
        price = self._apply_execution_slippage(base_price, 'long', 'entry')
        self.history['long_open_timestamp'][-1] = line['time']
        self.history['long_open_price'][-1] = price
        self.history['long_trailing_stop_price'][-1] = None
        self.history['position'][-1] = 1
        self.history['long_open_idx'][-1] = t
        self.history['order_type'][-1] = 'invert_to_long' if is_invertion else 'open_long'
        self._record_event(
            kind='open',
            side='long',
            bar_index=t,
            time_value=line['time'],
            price=price,
            metadata={'is_invertion': is_invertion, 'base_price': float(base_price)},
        )

    def _close_long(self, t, line):
        self.position = 0
        base_price = self._parse_price(
            t,
            self.strategy.trade_prices['close_trade']['long'],
        )
        price = self._apply_execution_slippage(base_price, 'long', 'close')
        self.history['long_close_timestamp'][-1] = line['time']
        self.history['last_long_close_timestamp'][-1] = line['time']
        self.history['last_trade_close_timestamp'][-1] = line['time']
        self.history['long_close_price'][-1] = price
        self.history['long_trailing_stop_price'][-1] = None
        self.history['position'][-1] = 0
        self.history['order_type'][-1] = 'close_long'
        self._record_event(
            kind='close',
            side='long',
            bar_index=t,
            time_value=line['time'],
            price=price,
            metadata={'base_price': float(base_price)},
        )

    def _stop_long(self, t, line, stop_type, executed_price=None):
        self.position = 0
        base_price = float(executed_price) if executed_price is not None else self._parse_price(
            t,
            self.strategy.trade_prices['stop_long'][stop_type],
        )
        event_kind = 'take_profit' if stop_type == 'gain' else 'trailing_stop' if stop_type == 'trail' else 'stop_loss'
        price = self._apply_execution_slippage(base_price, 'long', event_kind)
        self.history['long_close_timestamp'][-1] = line['time']
        self.history['last_long_close_timestamp'][-1] = line['time']
        self.history['last_trade_close_timestamp'][-1] = line['time']
        self.history['long_close_price'][-1] = price
        self.history['position'][-1] = 0
        self.history['long_trailing_stop_price'][-1] = None
        self.history['order_type'][-1] = f'stop_long_{stop_type}'
        self._record_event(
            kind='stop',
            side='long',
            bar_index=t,
            time_value=line['time'],
            price=price,
            metadata={'stop_type': stop_type, 'base_price': float(base_price)},
        )

    def _open_short(self, t, line, is_invertion=False):
        self.position = -1
        base_price = self._parse_price(
            t,
            self.strategy.trade_prices['open_trade']['short'],
        )
        price = self._apply_execution_slippage(base_price, 'short', 'entry')
        self.history['short_open_timestamp'][-1] = line['time']
        self.history['short_open_price'][-1] = price
        self.history['short_trailing_stop_price'][-1] = None
        self.history['position'][-1] = -1
        self.history['short_open_idx'][-1] = t
        self.history['order_type'][-1] = 'invert_to_short' if is_invertion else 'open_short'
        self._record_event(
            kind='open',
            side='short',
            bar_index=t,
            time_value=line['time'],
            price=price,
            metadata={'is_invertion': is_invertion, 'base_price': float(base_price)},
        )

    def _close_short(self, t, line):
        self.position = 0
        base_price = self._parse_price(
            t,
            self.strategy.trade_prices['close_trade']['short'],
        )
        price = self._apply_execution_slippage(base_price, 'short', 'close')
        self.history['short_close_timestamp'][-1] = line['time']
        self.history['last_short_close_timestamp'][-1] = line['time']
        self.history['last_trade_close_timestamp'][-1] = line['time']
        self.history['short_close_price'][-1] = price
        self.history['short_trailing_stop_price'][-1] = None
        self.history['position'][-1] = 0
        self.history['order_type'][-1] = 'close_short'
        self._record_event(
            kind='close',
            side='short',
            bar_index=t,
            time_value=line['time'],
            price=price,
            metadata={'base_price': float(base_price)},
        )

    def _stop_short(self, t, line, stop_type, executed_price=None):
        self.position = 0
        base_price = float(executed_price) if executed_price is not None else self._parse_price(
            t,
            self.strategy.trade_prices['stop_short'][stop_type],
        )
        event_kind = 'take_profit' if stop_type == 'gain' else 'trailing_stop' if stop_type == 'trail' else 'stop_loss'
        price = self._apply_execution_slippage(base_price, 'short', event_kind)
        self.history['short_close_timestamp'][-1] = line['time']
        self.history['last_short_close_timestamp'][-1] = line['time']
        self.history['last_trade_close_timestamp'][-1] = line['time']
        self.history['short_close_price'][-1] = price
        self.history['position'][-1] = 0
        self.history['short_trailing_stop_price'][-1] = None
        self.history['order_type'][-1] = f'stop_short_{stop_type}'
        self._record_event(
            kind='stop',
            side='short',
            bar_index=t,
            time_value=line['time'],
            price=price,
            metadata={'stop_type': stop_type, 'base_price': float(base_price)},
        )

    def _record_event(self, kind, side, bar_index, time_value, price, metadata=None):
        self.events.append(
            TradeEvent(
                kind=kind,
                side=side,
                time=int(time_value) if time_value is not None else None,
                bar_index=bar_index,
                price=float(price) if price is not None else None,
                position_after=self.position,
                metadata=metadata or {},
            )
        )

    def _calc_order_life(self, t):
        self.history['opened_order_life'][-1] = None

        if self.history['long_open_idx'][-1] is not None:
            self.history['long_order_life'][-1] = t - self.history['long_open_idx'][-1]
            if self.history['long_close_price'][-1] is None:
                self.history['opened_order_life'][-1] = self.history['long_order_life'][-1]
        else:
            self.history['long_order_life'][-1] = None

        if self.history['short_open_idx'][-1] is not None:
            self.history['short_order_life'][-1] = t - self.history['short_open_idx'][-1]
            if self.history['short_close_price'][-1] is None:
                self.history['opened_order_life'][-1] = self.history['short_order_life'][-1]
        else:
            self.history['short_order_life'][-1] = None

    def _calc_pnl(self, line):
        self.history['unrealized_pnl'][-1] = None
        self.history['realized_pnl'][-1] = None

        if self.history['position'][-1] == 1:
            open_price = self.history['long_open_price'][-1]
            if open_price is not None:
                self.history['unrealized_pnl'][-1] = line['close'] - open_price
            return

        if self.history['position'][-1] == -1:
            open_price = self.history['short_open_price'][-1]
            if open_price is not None:
                self.history['unrealized_pnl'][-1] = open_price - line['close']
            return

        if (
            self.history['long_close_price'][-1] is not None
            and self.history['long_open_price'][-1] is not None
        ):
            self.history['realized_pnl'][-1] = (
                self.history['long_close_price'][-1] - self.history['long_open_price'][-1]
            )
            return

        if (
            self.history['short_close_price'][-1] is not None
            and self.history['short_open_price'][-1] is not None
        ):
            self.history['realized_pnl'][-1] = (
                self.history['short_open_price'][-1] - self.history['short_close_price'][-1]
            )

    def _parse_condition(self, t, condition):
        if not isinstance(condition, str):
            raise TypeError('Condition must be a string.')

        try:
            parsed_condition = self._translate_expression_to_python(t, condition)
        except ValueError as exc:
            if 'Negative history access is not allowed' in str(exc):
                return False
            raise

        if not self._is_safe_expression(parsed_condition):
            raise ValueError(f'Unsafe expression: {parsed_condition}')

        return bool(self._safe_eval(parsed_condition))

    def _parse_price(self, t, price):
        if isinstance(price, (int, float)):
            return float(price)

        if not isinstance(price, str):
            raise TypeError('Price must be a string, int, or float.')

        parsed_price = self._translate_expression_to_python(t, price)

        if not self._is_safe_expression(parsed_price):
            raise ValueError(f'Unsafe price expression: {parsed_price}')

        return float(self._safe_eval(parsed_price))

    def _translate_expression_to_python(self, t, expr):
        pattern = r'([A-Za-z_]\w*)\[(\d+)\]'

        def replace(match):
            name = match.group(1)
            offset = int(match.group(2))
            index = t - offset

            if index < 0:
                raise ValueError(
                    f'Negative history access is not allowed: {name}[{offset}] at t={t}'
                )

            return f'{name}[{index}]'

        return re.sub(pattern, replace, expr)

    def _build_eval_context(self):
        context = {key: value for key, value in self.history.items()}

        for key, value in self.history.items():
            safe_identifier = build_expression_safe_identifier(key)
            if not safe_identifier:
                continue
            existing = context.get(safe_identifier)
            if existing is not None and safe_identifier != key and existing is not value:
                raise ValueError(
                    f'Expression identifier collision for {key} -> {safe_identifier}'
                )
            context[safe_identifier] = value

        return context

    def _safe_eval(self, expr):
        context = self._build_eval_context()
        return eval(expr, {'__builtins__': {}}, context)

    def _is_safe_expression(self, expr):
        allowed_nodes = (
            ast.Expression,
            ast.BoolOp,
            ast.And,
            ast.Or,
            ast.Not,
            ast.UnaryOp,
            ast.USub,
            ast.UAdd,
            ast.BinOp,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Mod,
            ast.Pow,
            ast.Compare,
            ast.Eq,
            ast.NotEq,
            ast.Gt,
            ast.GtE,
            ast.Lt,
            ast.LtE,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.Subscript,
            ast.List,
            ast.Tuple,
        )

        try:
            tree = ast.parse(expr, mode='eval')
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                return False

        return True
