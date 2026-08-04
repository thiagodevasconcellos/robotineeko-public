import ast
import re
from dataclasses import dataclass

import pandas as pd

try:
    from ...indicator_registry import get_indicator_class, split_indicator_feature_name
except ImportError:
    from indicator_registry import get_indicator_class, split_indicator_feature_name

from .models import MultiStrategyEngineResult, PortfolioTradeEvent, StrategyPortfolioState
from .multi_strategy import StrategyOpenCandidate, resolve_open_conflicts


@dataclass
class _StrategyRuntimeState:
    strategy_id: str
    strategy_label: str
    priority: int
    strategy: object
    enabled: bool
    position: int = 0
    pending_action: dict | None = None
    history: dict | None = None


class MultiStrategyExecutionEngine:
    def __init__(self, strategy_entries, symbol, allow_hedge=False):
        self.strategy_entries = list(strategy_entries or [])
        self.symbol = symbol
        self.allow_hedge = bool(allow_hedge)
        self.events = []
        self.strategy_states = []
        self.history = {}

    def run(self):
        self._ensure_required_indicators()
        self._start_runtime()

        for t in range(len(self.symbol.candles)):
            line = self.symbol.candles.iloc[t]

            self._new_portfolio_history_line()
            self._update_portfolio_history_from_candle(line)

            for state in self.strategy_states:
                self._new_strategy_history_line(state)
                self._update_strategy_history_from_candle(state, line)

            self._execute_pending_close_actions(t, line)
            self._execute_pending_open_actions(t, line)
            self._update_dynamic_stop_levels(t)
            self._process_bar_signals(t, line)
            self._calc_order_life(t)
            self._calc_pnl(line)
            self._record_portfolio_snapshot()

        return MultiStrategyEngineResult(
            history=pd.DataFrame(self.history),
            events=list(self.events),
            strategy_states=[
                StrategyPortfolioState(
                    strategy_id=state.strategy_id,
                    strategy_label=state.strategy_label,
                    priority=state.priority,
                    enabled=state.enabled,
                    position=state.position,
                    pending_action=dict(state.pending_action) if state.pending_action else None,
                    metadata={
                        'history': pd.DataFrame(state.history or {}),
                    },
                )
                for state in self.strategy_states
            ],
            final_portfolio_position=self._current_portfolio_direction(),
            metadata={
                'allow_hedge': self.allow_hedge,
            },
        )

    def _ensure_required_indicators(self):
        required_names = set()

        for entry in self.strategy_entries:
            strategy = entry['strategy']
            expressions = []
            expressions.extend(strategy.trade_conditions.values())
            for section in strategy.trade_prices.values():
                expressions.extend(section.values())
            for expr in expressions:
                if isinstance(expr, str):
                    required_names.update(re.findall(r'([A-Za-z_]\w*)\[\d+\]', expr))

        missing_names = [
            name
            for name in required_names
            if name not in self.symbol.features
        ]

        for feature_name in missing_names:
            indicator_name, raw_params = split_indicator_feature_name(feature_name)
            if indicator_name is None:
                continue
            indicator_class = get_indicator_class(indicator_name)
            if indicator_class is None:
                continue
            parsed_params = [self._parse_indicator_param(param) for param in raw_params]
            indicator_class(self.symbol, *parsed_params)

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

    def _start_runtime(self):
        self.events = []
        self.history = {
            'time': [],
            'open': [],
            'high': [],
            'low': [],
            'close': [],
            'portfolio_position': [],
            'active_strategy_ids': [],
            'accepted_open_ids': [],
            'skipped_open_ids': [],
        }
        self.strategy_states = []

        for entry in sorted(self.strategy_entries, key=lambda item: (int(item['priority']), str(item['strategy_id']))):
            strategy = entry['strategy']
            if getattr(strategy, 'execution_mode', 'next_bar_open') != 'next_bar_open':
                raise NotImplementedError('MultiStrategyExecutionEngine currently supports next_bar_open only.')
            state = _StrategyRuntimeState(
                strategy_id=entry['strategy_id'],
                strategy_label=entry['strategy_label'],
                priority=int(entry['priority']),
                strategy=strategy,
                enabled=bool(entry.get('enabled', True)),
                history=self._build_strategy_history(strategy),
            )
            self.strategy_states.append(state)

    def _build_strategy_history(self, strategy):
        columns = []
        for col in self.symbol.features:
            if col not in columns:
                columns.append(col)
        for col in strategy.trade_columns:
            if col not in columns:
                columns.append(col)
        return {col: [] for col in columns}

    def _new_portfolio_history_line(self):
        for col in self.history:
            self.history[col].append(None)

    def _update_portfolio_history_from_candle(self, line):
        for key in ('time', 'open', 'high', 'low', 'close'):
            self.history[key][-1] = line[key]

    def _new_strategy_history_line(self, state):
        for col in state.history:
            state.history[col].append(None)

        current_length = len(next(iter(state.history.values())))
        if current_length <= 1:
            return

        repeat_cols = [
            'long_open_timestamp',
            'long_open_price',
            'long_open_idx',
            'long_trailing_stop_price',
            'last_long_close_timestamp',
            'short_open_timestamp',
            'short_open_price',
            'short_open_idx',
            'short_trailing_stop_price',
            'last_short_close_timestamp',
            'last_trade_close_timestamp',
            'position',
        ]
        for repeat_col in repeat_cols:
            state.history[repeat_col][-1] = state.history[repeat_col][-2]

        if state.history['position'][-1] != 1:
            state.history['long_open_timestamp'][-1] = None
            state.history['long_open_price'][-1] = None
            state.history['long_open_idx'][-1] = None
            state.history['long_close_timestamp'][-1] = None
            state.history['long_close_price'][-1] = None
            state.history['long_trailing_stop_price'][-1] = None

        if state.history['position'][-1] != -1:
            state.history['short_open_timestamp'][-1] = None
            state.history['short_open_price'][-1] = None
            state.history['short_open_idx'][-1] = None
            state.history['short_close_timestamp'][-1] = None
            state.history['short_close_price'][-1] = None
            state.history['short_trailing_stop_price'][-1] = None

        if 1 not in state.history['position'][-2:]:
            state.history['long_open_idx'][-1] = None

        if -1 not in state.history['position'][-2:]:
            state.history['short_open_idx'][-1] = None

    def _update_strategy_history_from_candle(self, state, line):
        for col in self.symbol.features:
            state.history[col][-1] = line[col]

    def _parse_optional_price(self, state, t, price):
        if price is None:
            return None
        if isinstance(price, str) and not price.strip():
            return None
        return self._parse_price(state, t, price)

    def _execute_pending_close_actions(self, t, line):
        for state in self.strategy_states:
            action = state.pending_action
            if not action:
                continue
            if action['kind'] == 'close_long':
                state.pending_action = None
                self._close_long(state, t, line)
            elif action['kind'] == 'close_short':
                state.pending_action = None
                self._close_short(state, t, line)
            elif action['kind'] == 'invert_to_short' and state.position == 1:
                state.pending_action = {'kind': 'open_short'}
                self._close_long(state, t, line)
            elif action['kind'] == 'invert_to_long' and state.position == -1:
                state.pending_action = {'kind': 'open_long'}
                self._close_short(state, t, line)

    def _execute_pending_open_actions(self, t, line):
        candidates = []
        candidate_map = {}

        for state in self.strategy_states:
            action = state.pending_action
            if not action:
                continue
            if action['kind'] not in {'open_long', 'open_short'}:
                continue
            side = 'long' if action['kind'] == 'open_long' else 'short'
            candidate = StrategyOpenCandidate(
                strategy_id=state.strategy_id,
                strategy_label=state.strategy_label,
                priority=state.priority,
                side=side,
                metadata={'source': 'pending_action'},
            )
            candidates.append(candidate)
            candidate_map[state.strategy_id] = state

        resolution = resolve_open_conflicts(
            candidates,
            current_portfolio_position=self._current_portfolio_direction(),
            allow_hedge=self.allow_hedge,
        )
        self._apply_open_resolution(resolution, t, line, candidate_map)

    def _previous_bar_range_pips(self, state):
        pip_size = float(state.strategy.execution_slippage.get('pip_size') or 0.0)
        if pip_size <= 0 or len(state.history.get('high', [])) < 2 or len(state.history.get('low', [])) < 2:
            return 0.0

        previous_high = state.history['high'][-2]
        previous_low = state.history['low'][-2]

        if previous_high is None or previous_low is None or pd.isna(previous_high) or pd.isna(previous_low):
            return 0.0

        return max((float(previous_high) - float(previous_low)) / pip_size, 0.0)

    def _effective_slippage_pips(self, state, base_pips):
        base_value = max(float(base_pips or 0.0), 0.0)
        volatility_multiplier = max(float(state.strategy.execution_slippage.get('volatility_multiplier') or 0.0), 0.0)
        return base_value + (self._previous_bar_range_pips(state) * volatility_multiplier)

    def _minimum_stop_distance_price(self, state):
        pip_size = float(state.strategy.execution_slippage.get('pip_size') or 0.0)
        minimum_stop_distance_in_pips = max(float(state.strategy.execution_slippage.get('minimum_stop_distance_in_pips') or 0.0), 0.0)
        if pip_size <= 0 or minimum_stop_distance_in_pips <= 0:
            return 0.0
        return minimum_stop_distance_in_pips * pip_size

    def _enforce_minimum_stop_distance(self, state, side, stop_kind, candidate_price):
        if candidate_price is None:
            return None

        minimum_distance_price = self._minimum_stop_distance_price(state)
        if minimum_distance_price <= 0:
            return float(candidate_price)

        open_price_key = 'long_open_price' if side == 'long' else 'short_open_price'
        open_price = state.history.get(open_price_key, [None])[-1]
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

    def _apply_execution_slippage(self, state, price, side, event_kind):
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

        pip_size = float(state.strategy.execution_slippage.get('pip_size') or 0.0)
        if pip_size <= 0:
            return float(price)

        slippage_pips = self._effective_slippage_pips(state, state.strategy.execution_slippage.get(config_key, 0.0))
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

    def _update_dynamic_stop_levels(self, t):
        for state in self.strategy_states:
            if state.position == 1:
                stop_loss_candidate = self._parse_optional_price(state, t, state.strategy.trade_prices['stop_long']['loss'])
                take_profit_candidate = self._parse_optional_price(state, t, state.strategy.trade_prices['stop_long']['gain'])
                state.history['long_stop_loss_price'][-1] = self._enforce_minimum_stop_distance(state, 'long', 'loss', stop_loss_candidate)
                state.history['long_take_profit_price'][-1] = self._enforce_minimum_stop_distance(state, 'long', 'gain', take_profit_candidate)
                candidate = self._parse_optional_price(state, t, state.strategy.trade_prices['stop_long']['trail'])
                candidate = self._enforce_minimum_stop_distance(state, 'long', 'trail', candidate)
                previous = state.history['long_trailing_stop_price'][-1]
                if candidate is None:
                    continue
                if previous is None or pd.isna(previous):
                    state.history['long_trailing_stop_price'][-1] = candidate
                else:
                    state.history['long_trailing_stop_price'][-1] = max(float(previous), float(candidate))
                continue

            if state.position == -1:
                stop_loss_candidate = self._parse_optional_price(state, t, state.strategy.trade_prices['stop_short']['loss'])
                take_profit_candidate = self._parse_optional_price(state, t, state.strategy.trade_prices['stop_short']['gain'])
                state.history['short_stop_loss_price'][-1] = self._enforce_minimum_stop_distance(state, 'short', 'loss', stop_loss_candidate)
                state.history['short_take_profit_price'][-1] = self._enforce_minimum_stop_distance(state, 'short', 'gain', take_profit_candidate)
                candidate = self._parse_optional_price(state, t, state.strategy.trade_prices['stop_short']['trail'])
                candidate = self._enforce_minimum_stop_distance(state, 'short', 'trail', candidate)
                previous = state.history['short_trailing_stop_price'][-1]
                if candidate is None:
                    continue
                if previous is None or pd.isna(previous):
                    state.history['short_trailing_stop_price'][-1] = candidate
                else:
                    state.history['short_trailing_stop_price'][-1] = min(float(previous), float(candidate))

    def _resolve_long_stop(self, state, t, line):
        stop_loss_price = state.history['long_stop_loss_price'][-1]
        trailing_price = state.history['long_trailing_stop_price'][-1]
        take_profit_price = state.history['long_take_profit_price'][-1]

        if stop_loss_price is not None and line['low'] <= stop_loss_price:
            return 'loss', float(line['low'])

        is_entry_bar = state.history['long_open_idx'][-1] == t
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

    def _resolve_short_stop(self, state, t, line):
        stop_loss_price = state.history['short_stop_loss_price'][-1]
        trailing_price = state.history['short_trailing_stop_price'][-1]
        take_profit_price = state.history['short_take_profit_price'][-1]

        if stop_loss_price is not None and line['high'] >= stop_loss_price:
            return 'loss', float(line['high'])

        is_entry_bar = state.history['short_open_idx'][-1] == t
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

    def _process_bar_signals(self, t, line):
        new_open_candidates = []
        candidate_map = {}

        for state in self.strategy_states:
            if not state.enabled:
                continue

            trigger_long = self._evaluate_condition(state, t, state.strategy.trade_conditions['open_long'])
            trigger_short = self._evaluate_condition(state, t, state.strategy.trade_conditions['open_short'])

            if state.position == 1:
                stop_hit = self._resolve_long_stop(state, t, line)
                if stop_hit is not None:
                    stop_type, stop_price = stop_hit
                    self._stop_long(state, t, line, stop_type, executed_price=stop_price)
                elif self._evaluate_condition(state, t, state.strategy.trade_conditions['close_long']):
                    state.pending_action = {'kind': 'close_long'}
                elif trigger_short and state.strategy.allow_invertion:
                    state.pending_action = {'kind': 'invert_to_short'}
                continue

            if state.position == -1:
                stop_hit = self._resolve_short_stop(state, t, line)
                if stop_hit is not None:
                    stop_type, stop_price = stop_hit
                    self._stop_short(state, t, line, stop_type, executed_price=stop_price)
                elif self._evaluate_condition(state, t, state.strategy.trade_conditions['close_short']):
                    state.pending_action = {'kind': 'close_short'}
                elif trigger_long and state.strategy.allow_invertion:
                    state.pending_action = {'kind': 'invert_to_long'}
                continue

            if trigger_long and trigger_short:
                chosen_side = 'long' if state.strategy.prioritize == 'long' else 'short'
                candidate = StrategyOpenCandidate(
                    strategy_id=state.strategy_id,
                    strategy_label=state.strategy_label,
                    priority=state.priority,
                    side=chosen_side,
                    metadata={'source': 'bar_signal', 'conflicting_signals': True},
                )
                new_open_candidates.append(candidate)
                candidate_map[state.strategy_id] = state
                continue

            if trigger_long:
                candidate = StrategyOpenCandidate(
                    strategy_id=state.strategy_id,
                    strategy_label=state.strategy_label,
                    priority=state.priority,
                    side='long',
                    metadata={'source': 'bar_signal'},
                )
                new_open_candidates.append(candidate)
                candidate_map[state.strategy_id] = state
            elif trigger_short:
                candidate = StrategyOpenCandidate(
                    strategy_id=state.strategy_id,
                    strategy_label=state.strategy_label,
                    priority=state.priority,
                    side='short',
                    metadata={'source': 'bar_signal'},
                )
                new_open_candidates.append(candidate)
                candidate_map[state.strategy_id] = state

        resolution = resolve_open_conflicts(
            new_open_candidates,
            current_portfolio_position=self._current_portfolio_direction(),
            allow_hedge=self.allow_hedge,
        )

        for entry in resolution['accepted']:
            state = candidate_map[entry.strategy_id]
            state.pending_action = {'kind': f'open_{entry.side}'}

        self.history['accepted_open_ids'][-1] = [entry.strategy_id for entry in resolution['accepted']]
        self.history['skipped_open_ids'][-1] = [entry['candidate'].strategy_id for entry in resolution['skipped']]

        for skipped in resolution['skipped']:
            candidate = skipped['candidate']
            self.events.append(PortfolioTradeEvent(
                kind='skip_open',
                strategy_id=candidate.strategy_id,
                strategy_label=candidate.strategy_label,
                side=candidate.side,
                time=int(line['time']) if line['time'] is not None else None,
                bar_index=t,
                price=None,
                portfolio_position_after=self._current_portfolio_direction(),
                strategy_position_after=candidate_map[candidate.strategy_id].position,
                metadata={'reason': skipped['reason']},
            ))

    def _apply_open_resolution(self, resolution, t, line, candidate_map):
        self.history['accepted_open_ids'][-1] = [entry.strategy_id for entry in resolution['accepted']]
        self.history['skipped_open_ids'][-1] = [entry['candidate'].strategy_id for entry in resolution['skipped']]

        for accepted in resolution['accepted']:
            state = candidate_map[accepted.strategy_id]
            state.pending_action = None
            if accepted.side == 'long':
                self._open_long(state, t, line)
            else:
                self._open_short(state, t, line)

        for skipped in resolution['skipped']:
            candidate = skipped['candidate']
            state = candidate_map[candidate.strategy_id]
            state.pending_action = None
            self.events.append(PortfolioTradeEvent(
                kind='skip_open',
                strategy_id=candidate.strategy_id,
                strategy_label=candidate.strategy_label,
                side=candidate.side,
                time=int(line['time']) if line['time'] is not None else None,
                bar_index=t,
                price=None,
                portfolio_position_after=self._current_portfolio_direction(),
                strategy_position_after=state.position,
                metadata={'reason': skipped['reason']},
            ))

    def _record_portfolio_snapshot(self):
        self.history['portfolio_position'][-1] = self._current_portfolio_direction()
        self.history['active_strategy_ids'][-1] = [
            state.strategy_id for state in self.strategy_states if state.position != 0
        ]

    def _current_portfolio_direction(self):
        has_long = any(state.position == 1 for state in self.strategy_states)
        has_short = any(state.position == -1 for state in self.strategy_states)
        if has_long and not has_short:
            return 1
        if has_short and not has_long:
            return -1
        return 0

    def _evaluate_condition(self, state, t, condition):
        if not condition:
            return False
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

        return bool(eval(parsed_condition, {'__builtins__': {}}, {key: value for key, value in state.history.items()}))

    def _parse_price(self, state, t, price):
        if isinstance(price, (int, float)):
            return float(price)
        parsed_price = self._translate_expression_to_python(t, price)
        if not self._is_safe_expression(parsed_price):
            raise ValueError(f'Unsafe price expression: {parsed_price}')
        return float(eval(parsed_price, {'__builtins__': {}}, {key: value for key, value in state.history.items()}))

    def _translate_expression_to_python(self, t, expr):
        pattern = r'([A-Za-z_]\w*)\[(\d+)\]'

        def replace(match):
            name = match.group(1)
            offset = int(match.group(2))
            index = t - offset
            if index < 0:
                raise ValueError(f'Negative history access is not allowed: {name}[{offset}] at t={t}')
            return f'{name}[{index}]'

        return re.sub(pattern, replace, expr)

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

    def _open_long(self, state, t, line):
        state.position = 1
        base_price = self._parse_price(state, t, state.strategy.trade_prices['open_trade']['long'])
        price = self._apply_execution_slippage(state, base_price, 'long', 'entry')
        state.history['long_open_timestamp'][-1] = line['time']
        state.history['long_open_price'][-1] = price
        state.history['long_trailing_stop_price'][-1] = None
        state.history['position'][-1] = 1
        state.history['long_open_idx'][-1] = t
        state.history['order_type'][-1] = 'open_long'
        self.events.append(PortfolioTradeEvent(
            kind='open',
            strategy_id=state.strategy_id,
            strategy_label=state.strategy_label,
            side='long',
            time=int(line['time']) if line['time'] is not None else None,
            bar_index=t,
            price=price,
            portfolio_position_after=self._current_portfolio_direction(),
            strategy_position_after=1,
            metadata={'base_price': float(base_price)},
        ))

    def _open_short(self, state, t, line):
        state.position = -1
        base_price = self._parse_price(state, t, state.strategy.trade_prices['open_trade']['short'])
        price = self._apply_execution_slippage(state, base_price, 'short', 'entry')
        state.history['short_open_timestamp'][-1] = line['time']
        state.history['short_open_price'][-1] = price
        state.history['short_trailing_stop_price'][-1] = None
        state.history['position'][-1] = -1
        state.history['short_open_idx'][-1] = t
        state.history['order_type'][-1] = 'open_short'
        self.events.append(PortfolioTradeEvent(
            kind='open',
            strategy_id=state.strategy_id,
            strategy_label=state.strategy_label,
            side='short',
            time=int(line['time']) if line['time'] is not None else None,
            bar_index=t,
            price=price,
            portfolio_position_after=self._current_portfolio_direction(),
            strategy_position_after=-1,
            metadata={'base_price': float(base_price)},
        ))

    def _close_long(self, state, t, line):
        state.position = 0
        base_price = self._parse_price(state, t, state.strategy.trade_prices['close_trade']['long'])
        price = self._apply_execution_slippage(state, base_price, 'long', 'close')
        state.history['long_close_timestamp'][-1] = line['time']
        state.history['last_long_close_timestamp'][-1] = line['time']
        state.history['last_trade_close_timestamp'][-1] = line['time']
        state.history['long_close_price'][-1] = price
        state.history['long_trailing_stop_price'][-1] = None
        state.history['position'][-1] = 0
        state.history['order_type'][-1] = 'close_long'
        self.events.append(PortfolioTradeEvent(
            kind='close',
            strategy_id=state.strategy_id,
            strategy_label=state.strategy_label,
            side='long',
            time=int(line['time']) if line['time'] is not None else None,
            bar_index=t,
            price=price,
            portfolio_position_after=self._current_portfolio_direction(),
            strategy_position_after=0,
            metadata={'base_price': float(base_price)},
        ))

    def _close_short(self, state, t, line):
        state.position = 0
        base_price = self._parse_price(state, t, state.strategy.trade_prices['close_trade']['short'])
        price = self._apply_execution_slippage(state, base_price, 'short', 'close')
        state.history['short_close_timestamp'][-1] = line['time']
        state.history['last_short_close_timestamp'][-1] = line['time']
        state.history['last_trade_close_timestamp'][-1] = line['time']
        state.history['short_close_price'][-1] = price
        state.history['short_trailing_stop_price'][-1] = None
        state.history['position'][-1] = 0
        state.history['order_type'][-1] = 'close_short'
        self.events.append(PortfolioTradeEvent(
            kind='close',
            strategy_id=state.strategy_id,
            strategy_label=state.strategy_label,
            side='short',
            time=int(line['time']) if line['time'] is not None else None,
            bar_index=t,
            price=price,
            portfolio_position_after=self._current_portfolio_direction(),
            strategy_position_after=0,
            metadata={'base_price': float(base_price)},
        ))

    def _stop_long(self, state, t, line, stop_type, executed_price=None):
        state.position = 0
        base_price = float(executed_price) if executed_price is not None else self._parse_price(
            state,
            t,
            state.strategy.trade_prices['stop_long'][stop_type],
        )
        event_kind = 'take_profit' if stop_type == 'gain' else 'trailing_stop' if stop_type == 'trail' else 'stop_loss'
        price = self._apply_execution_slippage(state, base_price, 'long', event_kind)
        state.history['long_close_timestamp'][-1] = line['time']
        state.history['last_long_close_timestamp'][-1] = line['time']
        state.history['last_trade_close_timestamp'][-1] = line['time']
        state.history['long_close_price'][-1] = price
        state.history['position'][-1] = 0
        state.history['long_trailing_stop_price'][-1] = None
        state.history['order_type'][-1] = f'stop_long_{stop_type}'
        self.events.append(PortfolioTradeEvent(
            kind='stop',
            strategy_id=state.strategy_id,
            strategy_label=state.strategy_label,
            side='long',
            time=int(line['time']) if line['time'] is not None else None,
            bar_index=t,
            price=price,
            portfolio_position_after=self._current_portfolio_direction(),
            strategy_position_after=0,
            metadata={'stop_type': stop_type, 'base_price': float(base_price)},
        ))

    def _stop_short(self, state, t, line, stop_type, executed_price=None):
        state.position = 0
        base_price = float(executed_price) if executed_price is not None else self._parse_price(
            state,
            t,
            state.strategy.trade_prices['stop_short'][stop_type],
        )
        event_kind = 'take_profit' if stop_type == 'gain' else 'trailing_stop' if stop_type == 'trail' else 'stop_loss'
        price = self._apply_execution_slippage(state, base_price, 'short', event_kind)
        state.history['short_close_timestamp'][-1] = line['time']
        state.history['last_short_close_timestamp'][-1] = line['time']
        state.history['last_trade_close_timestamp'][-1] = line['time']
        state.history['short_close_price'][-1] = price
        state.history['position'][-1] = 0
        state.history['short_trailing_stop_price'][-1] = None
        state.history['order_type'][-1] = f'stop_short_{stop_type}'
        self.events.append(PortfolioTradeEvent(
            kind='stop',
            strategy_id=state.strategy_id,
            strategy_label=state.strategy_label,
            side='short',
            time=int(line['time']) if line['time'] is not None else None,
            bar_index=t,
            price=price,
            portfolio_position_after=self._current_portfolio_direction(),
            strategy_position_after=0,
            metadata={'stop_type': stop_type, 'base_price': float(base_price)},
        ))

    def _calc_order_life(self, t):
        for state in self.strategy_states:
            state.history['opened_order_life'][-1] = None

            if state.history['long_open_idx'][-1] is not None:
                state.history['long_order_life'][-1] = t - state.history['long_open_idx'][-1]
                if state.history['long_close_price'][-1] is None:
                    state.history['opened_order_life'][-1] = state.history['long_order_life'][-1]
            else:
                state.history['long_order_life'][-1] = None

            if state.history['short_open_idx'][-1] is not None:
                state.history['short_order_life'][-1] = t - state.history['short_open_idx'][-1]
                if state.history['short_close_price'][-1] is None:
                    state.history['opened_order_life'][-1] = state.history['short_order_life'][-1]
            else:
                state.history['short_order_life'][-1] = None

    def _calc_pnl(self, line):
        for state in self.strategy_states:
            state.history['unrealized_pnl'][-1] = None
            state.history['realized_pnl'][-1] = None

            if state.history['position'][-1] == 1:
                open_price = state.history['long_open_price'][-1]
                if open_price is not None:
                    state.history['unrealized_pnl'][-1] = line['close'] - open_price
                continue

            if state.history['position'][-1] == -1:
                open_price = state.history['short_open_price'][-1]
                if open_price is not None:
                    state.history['unrealized_pnl'][-1] = open_price - line['close']
                continue

            if (
                state.history['long_close_price'][-1] is not None
                and state.history['long_open_price'][-1] is not None
            ):
                state.history['realized_pnl'][-1] = (
                    state.history['long_close_price'][-1] - state.history['long_open_price'][-1]
                )
                continue

            if (
                state.history['short_close_price'][-1] is not None
                and state.history['short_open_price'][-1] is not None
            ):
                state.history['realized_pnl'][-1] = (
                    state.history['short_open_price'][-1] - state.history['short_close_price'][-1]
                )
