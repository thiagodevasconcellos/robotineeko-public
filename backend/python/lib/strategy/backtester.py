import pandas as pd

from .backtest_cost_profiles import (
    build_backtest_cost_policy,
    merge_cost_breakdown_items,
    normalize_backtest_cost_profile,
    partition_cost_breakdown_items,
    resolve_backtest_asset_type,
    resolve_broker_cost_context,
    resolve_effective_backtest_cost_profile,
    sum_cost_breakdown_amount,
)
from .capital_model import build_capital_policy, normalize_capital_model, resolve_trade_volume
from .multi_engine import MultiStrategyExecutionEngine
from .performance import BacktestPerformanceAnalyzer


def _scope_text(value, fallback=''):
    text = str(value or '').strip()
    return text or fallback


def _build_scope_metadata(entry: dict | None):
    safe_entry = dict(entry or {})
    strategy_id = _scope_text(safe_entry.get('strategy_id'))
    strategy_label = _scope_text(safe_entry.get('strategy_label'), strategy_id or 'Strategy')
    portfolio_id = _scope_text(safe_entry.get('portfolio_id'), 'legacy-default')
    portfolio_label = _scope_text(safe_entry.get('portfolio_label'), 'Legacy default portfolio')
    pipeline_id = _scope_text(safe_entry.get('pipeline_id'), 'legacy-pipeline')
    pipeline_label = _scope_text(safe_entry.get('pipeline_label'), 'Legacy pipeline')
    return {
        'strategy_id': strategy_id,
        'strategy_label': strategy_label,
        'portfolio_id': portfolio_id,
        'portfolio_label': portfolio_label,
        'pipeline_id': pipeline_id,
        'pipeline_label': pipeline_label,
        'priority': int(safe_entry.get('priority') or 0),
        'symbol': _scope_text(safe_entry.get('symbol')).upper(),
        'timeframe': _scope_text(safe_entry.get('timeframe')).upper(),
        'volume_mode': _scope_text(safe_entry.get('volume_mode') or safe_entry.get('volumeMode'), 'fixed_volume'),
        'fixed_volume': safe_entry.get('fixed_volume', safe_entry.get('fixedVolume')),
        'base_volume': safe_entry.get('base_volume', safe_entry.get('baseVolume')),
        'max_volume_cap': safe_entry.get('max_volume_cap', safe_entry.get('maxVolumeCap')),
        'reference_capital': safe_entry.get('reference_capital', safe_entry.get('referenceCapital')),
    }


def _new_scope_bucket(seed: dict):
    return {
        **seed,
        'trades': 0,
        'net_pnl': 0.0,
        'gross_pnl': 0.0,
        'cost': 0.0,
        'wins': 0,
        'losses': 0,
        '_strategy_ids': set(),
    }


def _accumulate_scope_bucket(bucket: dict, item: dict):
    bucket['trades'] += int(item.get('trades') or 0)
    bucket['net_pnl'] += float(item.get('net_pnl') or 0.0)
    bucket['gross_pnl'] += float(item.get('gross_pnl') or 0.0)
    bucket['cost'] += float(item.get('cost') or 0.0)
    bucket['wins'] += int(item.get('wins') or 0)
    bucket['losses'] += int(item.get('losses') or 0)
    strategy_id = _scope_text(item.get('strategy_id'))
    if strategy_id:
        bucket['_strategy_ids'].add(strategy_id)
    return bucket


def _finalize_scope_bucket(bucket: dict):
    safe_bucket = dict(bucket or {})
    strategy_ids = sorted(safe_bucket.pop('_strategy_ids', set()))
    safe_bucket['strategy_count'] = len(strategy_ids)
    safe_bucket['strategy_ids'] = strategy_ids
    safe_bucket['win_rate'] = (
        float(safe_bucket.get('wins') or 0) / float(safe_bucket.get('trades') or 0)
        if float(safe_bucket.get('trades') or 0) > 0
        else 0.0
    )
    return safe_bucket


def build_scope_tree_from_strategy_stats(strategy_stats: list[dict] | None):
    portfolio_map = {}

    for item in list(strategy_stats or []):
        scope = _build_scope_metadata(item)
        portfolio = portfolio_map.setdefault(scope['portfolio_id'], {
            'id': scope['portfolio_id'],
            'label': scope['portfolio_label'],
            'pipelines': {},
        })
        pipeline = portfolio['pipelines'].setdefault(scope['pipeline_id'], {
            'id': scope['pipeline_id'],
            'label': scope['pipeline_label'],
            'sleeves': [],
        })
        pipeline['sleeves'].append({
            'id': scope['strategy_id'],
            'label': scope['strategy_label'],
            'symbol': scope['symbol'],
            'timeframe': scope['timeframe'],
            'volume_mode': scope['volume_mode'],
        })

    portfolios = []
    for portfolio_id in sorted(portfolio_map):
        portfolio = portfolio_map[portfolio_id]
        pipelines = []
        for pipeline_id in sorted(portfolio['pipelines']):
            pipeline = portfolio['pipelines'][pipeline_id]
            pipelines.append({
                'id': pipeline['id'],
                'label': pipeline['label'],
                'sleeves': sorted(
                    pipeline['sleeves'],
                    key=lambda item: (str(item.get('symbol') or ''), str(item.get('timeframe') or ''), str(item.get('id') or '')),
                ),
            })
        portfolios.append({
            'id': portfolio['id'],
            'label': portfolio['label'],
            'pipelines': pipelines,
        })

    return {
        'portfolios': portfolios,
    }


def build_scope_rollups_from_strategy_stats(strategy_stats: list[dict] | None):
    safe_stats = [dict(item or {}) for item in list(strategy_stats or [])]
    total_bucket = _new_scope_bucket({
        'id': 'total',
        'label': 'Total',
    })
    portfolio_buckets = {}
    pipeline_buckets = {}

    for item in safe_stats:
        scope = _build_scope_metadata(item)
        _accumulate_scope_bucket(total_bucket, item)

        portfolio_bucket = portfolio_buckets.setdefault(
            scope['portfolio_id'],
            _new_scope_bucket({
                'id': scope['portfolio_id'],
                'label': scope['portfolio_label'],
                'portfolio_id': scope['portfolio_id'],
                'portfolio_label': scope['portfolio_label'],
            }),
        )
        _accumulate_scope_bucket(portfolio_bucket, item)

        pipeline_key = f"{scope['portfolio_id']}::{scope['pipeline_id']}"
        pipeline_bucket = pipeline_buckets.setdefault(
            pipeline_key,
            _new_scope_bucket({
                'id': scope['pipeline_id'],
                'label': scope['pipeline_label'],
                'portfolio_id': scope['portfolio_id'],
                'portfolio_label': scope['portfolio_label'],
                'pipeline_id': scope['pipeline_id'],
                'pipeline_label': scope['pipeline_label'],
            }),
        )
        _accumulate_scope_bucket(pipeline_bucket, item)

    sleeves = [
        {
            **dict(item),
            **_build_scope_metadata(item),
        }
        for item in safe_stats
    ]
    sleeves.sort(key=lambda item: (str(item.get('portfolio_id') or ''), str(item.get('pipeline_id') or ''), str(item.get('strategy_id') or '')))

    return {
        'total': _finalize_scope_bucket(total_bucket),
        'portfolios': [
            _finalize_scope_bucket(portfolio_buckets[key])
            for key in sorted(portfolio_buckets)
        ],
        'pipelines': [
            _finalize_scope_bucket(pipeline_buckets[key])
            for key in sorted(pipeline_buckets)
        ],
        'sleeves': sleeves,
    }


class Backtester():
    def __init__(self, symbol, strategy):
        self.symbol = symbol
        self.strategy = strategy
        self.trade_markers = []
        self.stats = {}
        self.results = None
        self.execution = None
        self.portfolio_mode = 'shared_pipe'
        self.raw_capital_model = None
        self.normalized_capital_model = None
        self.set_params()

    def set_params(
            self,
            initial_balance=10000,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=0.0001,
            pip_value_per_lot=10.0,
            cost_profile='oanda',
            spread_in_pips=1.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            take_profit_slippage_in_pips=0.0,
            stop_loss_slippage_in_pips=0.0,
            trailing_stop_slippage_in_pips=0.0,
            minimum_stop_distance_in_pips=0.0,
            volatility_slippage_multiplier=0.0,
            execution_mode='next_bar_open',
            portfolio_mode='shared_pipe',
            history_scope_mode='loaded_chart',
            history_scope_bars=None,
            broker_cost_context=None,
            capital_model=None,
        ):
        self.initial_balance = initial_balance
        self.current_balance = self.initial_balance

        self.broker_cost_context = resolve_broker_cost_context(broker_cost_context)
        self.requested_cost_profile = normalize_backtest_cost_profile(cost_profile)
        self.asset_type = resolve_backtest_asset_type(
            asset_type,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
            cost_profile=self.requested_cost_profile,
        )
        self.initial_volume = initial_volume
        self.volume = self.initial_volume

        self.pip_size = pip_size
        self.pip_value_per_lot = pip_value_per_lot
        self.cost_profile = resolve_effective_backtest_cost_profile(
            self.requested_cost_profile,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
        )
        self.spread_in_pips = spread_in_pips
        self.entry_slippage_in_pips = entry_slippage_in_pips
        self.close_slippage_in_pips = close_slippage_in_pips
        self.take_profit_slippage_in_pips = take_profit_slippage_in_pips
        self.stop_loss_slippage_in_pips = stop_loss_slippage_in_pips
        self.trailing_stop_slippage_in_pips = trailing_stop_slippage_in_pips
        self.minimum_stop_distance_in_pips = minimum_stop_distance_in_pips
        self.volatility_slippage_multiplier = volatility_slippage_multiplier
        self.execution_mode = execution_mode
        normalized_portfolio_mode = str(portfolio_mode or self.portfolio_mode or 'shared_pipe').strip().lower() or 'shared_pipe'
        if normalized_portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
            normalized_portfolio_mode = 'shared_pipe'
        self.portfolio_mode = normalized_portfolio_mode
        self.history_scope_mode = history_scope_mode
        self.history_scope_bars = history_scope_bars
        self.raw_capital_model = dict(capital_model or {}) if isinstance(capital_model, dict) else None
        self.normalized_capital_model = normalize_capital_model(
            self.raw_capital_model,
            asset_type=self.asset_type,
            symbol=getattr(self.symbol, 'name', ''),
            initial_balance=self.initial_balance,
        )

    def _build_performance_analyzer(self, results=None):
        source_results = self.results if results is None else results

        analyzer = BacktestPerformanceAnalyzer(
            source_results if source_results is not None else [],
            initial_balance=self.initial_balance,
            asset_type=self.asset_type,
            initial_volume=self.initial_volume,
            pip_size=self.pip_size,
            pip_value_per_lot=self.pip_value_per_lot,
            spread_in_pips=self.spread_in_pips,
            cost_profile=self.requested_cost_profile,
            broker_cost_context=self.broker_cost_context,
        )
        analyzer.execution_policy = {
            **build_backtest_cost_policy({
                'assetType': self.asset_type,
                'initialVolume': self.initial_volume,
                'pipValuePerLot': self.pip_value_per_lot,
                'costProfile': self.requested_cost_profile,
                'spreadInPips': self.spread_in_pips,
                'entrySlippageInPips': self.entry_slippage_in_pips,
                'closeSlippageInPips': self.close_slippage_in_pips,
                'takeProfitSlippageInPips': self.take_profit_slippage_in_pips,
                'stopLossSlippageInPips': self.stop_loss_slippage_in_pips,
                'trailingStopSlippageInPips': self.trailing_stop_slippage_in_pips,
                'minimumStopDistanceInPips': self.minimum_stop_distance_in_pips,
                'volatilitySlippageMultiplier': self.volatility_slippage_multiplier,
            }, broker_profile=self.broker_cost_context),
            'execution_mode': self.execution_mode,
            'portfolio_mode': self.portfolio_mode,
            'take_profit_fill': 'target_price',
            'stop_loss_fill': 'bar_extreme',
            'trailing_stop_fill': 'bar_extreme',
            'trailing_entry_policy': 'blocked_on_entry_candle',
            'same_bar_gain_exit': True,
            'same_bar_loss_exit': True,
            'same_bar_trailing_exit': False,
            'intrabar_conflict_policy': 'pessimistic_loss_first',
            'spread_in_pips': self.spread_in_pips,
            'entry_slippage_in_pips': self.entry_slippage_in_pips,
            'close_slippage_in_pips': self.close_slippage_in_pips,
            'take_profit_slippage_in_pips': self.take_profit_slippage_in_pips,
            'stop_loss_slippage_in_pips': self.stop_loss_slippage_in_pips,
            'trailing_stop_slippage_in_pips': self.trailing_stop_slippage_in_pips,
            'minimum_stop_distance_in_pips': self.minimum_stop_distance_in_pips,
            'volatility_slippage_multiplier': self.volatility_slippage_multiplier,
            'volatility_slippage_reference': 'previous_bar_range',
            'history_scope_mode': self.history_scope_mode,
            'history_scope_bars': self.history_scope_bars,
            **build_capital_policy(self.normalized_capital_model),
        }
        return analyzer

    def pip_value(self, volume=None):
        analyzer = self._build_performance_analyzer()
        return analyzer.pip_value(volume)

    def cost(self, volume=None):
        analyzer = self._build_performance_analyzer()
        return analyzer.cost(volume)

    def gross_result(self, order_side, open_price, close_price, volume=None):
        analyzer = self._build_performance_analyzer()
        return analyzer.gross_result(order_side, open_price, close_price, volume)

    def net_result(self, order_side, open_price, close_price, volume=None):
        return self.gross_result(order_side, open_price, close_price, volume) - self.cost(volume)

    def run(self):
        self.strategy.set_execution_slippage(
            pip_size=self.pip_size,
            entry_in_pips=self.entry_slippage_in_pips,
            close_in_pips=self.close_slippage_in_pips,
            take_profit_in_pips=self.take_profit_slippage_in_pips,
            stop_loss_in_pips=self.stop_loss_slippage_in_pips,
            trailing_stop_in_pips=self.trailing_stop_slippage_in_pips,
            minimum_stop_distance_in_pips=self.minimum_stop_distance_in_pips,
            volatility_multiplier=self.volatility_slippage_multiplier,
        )
        self.execution = self.strategy.execute(self.symbol)
        analyzer = self._build_performance_analyzer(self.execution.history)

        self.results = analyzer.run()
        self.trade_markers = analyzer.trade_markers
        self.stats = analyzer.stats
        return self.results

    def run_from(self, start_index=0, previous_execution=None):
        previous_results = self.results
        self.strategy.set_execution_slippage(
            pip_size=self.pip_size,
            entry_in_pips=self.entry_slippage_in_pips,
            close_in_pips=self.close_slippage_in_pips,
            take_profit_in_pips=self.take_profit_slippage_in_pips,
            stop_loss_in_pips=self.stop_loss_slippage_in_pips,
            trailing_stop_in_pips=self.trailing_stop_slippage_in_pips,
            minimum_stop_distance_in_pips=self.minimum_stop_distance_in_pips,
            volatility_multiplier=self.volatility_slippage_multiplier,
        )
        self.execution = self.strategy.execute(
            self.symbol,
            start_index=start_index,
            previous_execution=previous_execution,
        )
        analyzer = self._build_performance_analyzer(self.execution.history)

        self.results = analyzer.run_from(
            start_index=start_index,
            previous_results=previous_results,
        )
        self.trade_markers = analyzer.trade_markers
        self.stats = analyzer.stats
        return self.results


class MultiStrategyBacktester():
    def __init__(self, symbol, strategy_entries, allow_hedge=False, portfolio_mode='shared_pipe'):
        self.symbol = symbol
        self.strategy_entries = list(strategy_entries or [])
        normalized_portfolio_mode = str(portfolio_mode or 'shared_pipe').strip().lower() or 'shared_pipe'
        if normalized_portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
            normalized_portfolio_mode = 'shared_pipe'
        self.portfolio_mode = normalized_portfolio_mode
        self.allow_hedge = bool(allow_hedge) or self.portfolio_mode == 'parallel_sleeves'
        self.trade_markers = []
        self.stats = {}
        self.results = None
        self.execution = None
        self.ledger = []
        self.opportunity_tape = []
        self.replayed_trades = []
        self.sizing_skips = []
        self.scope_tree = {'portfolios': []}
        self.rollups = {'total': {}, 'portfolios': [], 'pipelines': [], 'sleeves': []}
        self.raw_capital_model = None
        self.normalized_capital_model = None
        self.capital_replay_enabled = False
        self.set_params()

    def set_params(
            self,
            initial_balance=10000,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=0.0001,
            pip_value_per_lot=10.0,
            cost_profile='oanda',
            spread_in_pips=1.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            take_profit_slippage_in_pips=0.0,
            stop_loss_slippage_in_pips=0.0,
            trailing_stop_slippage_in_pips=0.0,
            minimum_stop_distance_in_pips=0.0,
            volatility_slippage_multiplier=0.0,
            execution_mode='next_bar_open',
            portfolio_mode='shared_pipe',
            history_scope_mode='loaded_chart',
            history_scope_bars=None,
            broker_cost_context=None,
            capital_model=None,
        ):
        self.initial_balance = initial_balance
        self.broker_cost_context = resolve_broker_cost_context(broker_cost_context)
        self.requested_cost_profile = normalize_backtest_cost_profile(cost_profile)
        self.asset_type = resolve_backtest_asset_type(
            asset_type,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
            cost_profile=self.requested_cost_profile,
        )
        self.initial_volume = initial_volume
        self.pip_size = pip_size
        self.pip_value_per_lot = pip_value_per_lot
        self.cost_profile = resolve_effective_backtest_cost_profile(
            self.requested_cost_profile,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
        )
        self.spread_in_pips = spread_in_pips
        self.entry_slippage_in_pips = entry_slippage_in_pips
        self.close_slippage_in_pips = close_slippage_in_pips
        self.take_profit_slippage_in_pips = take_profit_slippage_in_pips
        self.stop_loss_slippage_in_pips = stop_loss_slippage_in_pips
        self.trailing_stop_slippage_in_pips = trailing_stop_slippage_in_pips
        self.minimum_stop_distance_in_pips = minimum_stop_distance_in_pips
        self.volatility_slippage_multiplier = volatility_slippage_multiplier
        self.execution_mode = execution_mode
        normalized_portfolio_mode = str(portfolio_mode or self.portfolio_mode or 'shared_pipe').strip().lower() or 'shared_pipe'
        if normalized_portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
            normalized_portfolio_mode = 'shared_pipe'
        self.portfolio_mode = normalized_portfolio_mode
        self.allow_hedge = self.portfolio_mode == 'parallel_sleeves'
        self.history_scope_mode = history_scope_mode
        self.history_scope_bars = history_scope_bars
        self.raw_capital_model = dict(capital_model or {}) if isinstance(capital_model, dict) else None
        self.normalized_capital_model = normalize_capital_model(
            self.raw_capital_model,
            asset_type=self.asset_type,
            symbol=getattr(self.symbol, 'name', ''),
            initial_balance=self.initial_balance,
        )
        self.capital_replay_enabled = self._uses_capital_replay()

    def _build_analyzer(self):
        analyzer = BacktestPerformanceAnalyzer(
            [],
            initial_balance=self.initial_balance,
            asset_type=self.asset_type,
            initial_volume=self.initial_volume,
            pip_size=self.pip_size,
            pip_value_per_lot=self.pip_value_per_lot,
            spread_in_pips=self.spread_in_pips,
            cost_profile=self.requested_cost_profile,
            broker_cost_context=self.broker_cost_context,
        )
        analyzer.execution_policy = {
            **build_backtest_cost_policy({
                'assetType': self.asset_type,
                'initialVolume': self.initial_volume,
                'pipValuePerLot': self.pip_value_per_lot,
                'costProfile': self.requested_cost_profile,
                'spreadInPips': self.spread_in_pips,
                'entrySlippageInPips': self.entry_slippage_in_pips,
                'closeSlippageInPips': self.close_slippage_in_pips,
                'takeProfitSlippageInPips': self.take_profit_slippage_in_pips,
                'stopLossSlippageInPips': self.stop_loss_slippage_in_pips,
                'trailingStopSlippageInPips': self.trailing_stop_slippage_in_pips,
                'minimumStopDistanceInPips': self.minimum_stop_distance_in_pips,
                'volatilitySlippageMultiplier': self.volatility_slippage_multiplier,
            }, broker_profile=self.broker_cost_context),
            'execution_mode': self.execution_mode,
            'portfolio_mode': self.portfolio_mode,
            'allow_hedge': self.allow_hedge,
            'spread_in_pips': self.spread_in_pips,
            'entry_slippage_in_pips': self.entry_slippage_in_pips,
            'close_slippage_in_pips': self.close_slippage_in_pips,
            'take_profit_slippage_in_pips': self.take_profit_slippage_in_pips,
            'stop_loss_slippage_in_pips': self.stop_loss_slippage_in_pips,
            'trailing_stop_slippage_in_pips': self.trailing_stop_slippage_in_pips,
            'minimum_stop_distance_in_pips': self.minimum_stop_distance_in_pips,
            'volatility_slippage_multiplier': self.volatility_slippage_multiplier,
            'volatility_slippage_reference': 'previous_bar_range',
            'history_scope_mode': self.history_scope_mode,
            'history_scope_bars': self.history_scope_bars,
            **build_capital_policy(self.normalized_capital_model),
        }
        return analyzer

    def _prepare_strategy_entries(self):
        prepared = []
        for entry in self.strategy_entries:
            strategy = entry['strategy']
            strategy.set_execution_slippage(
                pip_size=self.pip_size,
                entry_in_pips=self.entry_slippage_in_pips,
                close_in_pips=self.close_slippage_in_pips,
                take_profit_in_pips=self.take_profit_slippage_in_pips,
                stop_loss_in_pips=self.stop_loss_slippage_in_pips,
                trailing_stop_in_pips=self.trailing_stop_slippage_in_pips,
                minimum_stop_distance_in_pips=self.minimum_stop_distance_in_pips,
                volatility_multiplier=self.volatility_slippage_multiplier,
            )
            prepared.append({
                **entry,
                'strategy': strategy,
            })
        return prepared

    def _build_strategy_scope_map(self):
        return {
            str(entry.get('strategy_id') or '').strip(): _build_scope_metadata(entry)
            for entry in list(self.strategy_entries or [])
            if str(entry.get('strategy_id') or '').strip()
        }

    def _uses_capital_replay(self):
        for entry in list(self.strategy_entries or []):
            portfolio_id = str(entry.get('portfolio_id') or '').strip()
            pipeline_id = str(entry.get('pipeline_id') or '').strip()
            volume_mode = str(entry.get('volume_mode') or 'fixed_volume').strip().lower() or 'fixed_volume'
            if portfolio_id and portfolio_id != 'legacy-default':
                return True
            if pipeline_id and pipeline_id != 'legacy-pipeline':
                return True
            if volume_mode != 'fixed_volume':
                return True
        return False

    def _build_trade_opportunity_tape(self):
        strategy_scope_map = self._build_strategy_scope_map()
        open_positions = {}
        opportunities = []

        for sequence_index, event in enumerate(list(self.execution.events or [])):
            if event.kind == 'open':
                open_positions[event.strategy_id] = (event, sequence_index)
                continue
            if event.kind not in {'close', 'stop'}:
                continue
            open_payload = open_positions.pop(event.strategy_id, None)
            if open_payload is None:
                continue
            open_event, open_sequence = open_payload
            scope_meta = dict(strategy_scope_map.get(event.strategy_id) or {})
            opportunities.append({
                'id': f'{event.strategy_id}:{open_event.bar_index}:{event.bar_index}:{len(opportunities) + 1}',
                'strategy_id': event.strategy_id,
                'strategy_label': event.strategy_label,
                'priority': int(scope_meta.get('priority') or 0),
                'portfolio_id': scope_meta.get('portfolio_id'),
                'portfolio_label': scope_meta.get('portfolio_label'),
                'pipeline_id': scope_meta.get('pipeline_id'),
                'pipeline_label': scope_meta.get('pipeline_label'),
                'symbol': scope_meta.get('symbol') or str(getattr(self.symbol, 'name', '') or '').strip().upper(),
                'timeframe': scope_meta.get('timeframe') or str(getattr(self.symbol, 'timeframe', '') or '').strip().upper(),
                'volume_mode': scope_meta.get('volume_mode'),
                'fixed_volume': scope_meta.get('fixed_volume'),
                'base_volume': scope_meta.get('base_volume'),
                'max_volume_cap': scope_meta.get('max_volume_cap'),
                'reference_capital': scope_meta.get('reference_capital'),
                'side': event.side,
                'open_time': open_event.time,
                'open_bar_index': int(open_event.bar_index),
                'open_price': float(open_event.price) if open_event.price is not None else None,
                'close_time': event.time,
                'close_bar_index': int(event.bar_index),
                'close_price': float(event.price) if event.price is not None else None,
                'exit_kind': event.kind,
                'exit_reason': str((event.metadata or {}).get('stop_type') or event.kind).strip() or event.kind,
                'open_sequence': int(open_sequence),
                'close_sequence': int(sequence_index),
            })

        return opportunities

    def _build_results_frame_legacy(self):
        history_df = self.execution.history.copy()
        if history_df.empty:
            history_df = pd.DataFrame(columns=['time'])

        history_df['trade_cost'] = 0.0
        history_df['trade_cost_breakdown'] = [[] for _ in range(len(history_df.index))]
        history_df['trade_gross_pnl'] = 0.0
        history_df['trade_net_pnl'] = 0.0
        history_df['account_balance_delta'] = 0.0
        history_df['account_balance'] = float(self.initial_balance)

        analyzer = self._build_analyzer()
        open_positions = {}
        current_balance = float(self.initial_balance)
        bankrupt = False
        bankruptcy_index = None
        per_strategy_stats = {}
        per_strategy_deltas = {}
        strategy_scope_map = self._build_strategy_scope_map()
        trade_ledger = []

        for event in self.execution.events:
            if bankrupt:
                break
            if event.kind == 'open':
                open_positions[event.strategy_id] = event
                continue
            if event.kind not in {'close', 'stop'}:
                continue

            open_event = open_positions.pop(event.strategy_id, None)
            if open_event is None:
                continue

            gross_pnl = analyzer.gross_result(
                event.side,
                open_event.price,
                event.price,
            )
            trade_cost_breakdown = analyzer.trade_cost_breakdown(
                open_price=open_event.price,
                close_price=event.price,
                open_time=open_event.time,
                close_time=event.time,
                gross_pnl=gross_pnl,
            )
            trade_cost = float(sum(float(item.get('amount') or 0.0) for item in trade_cost_breakdown))
            net_pnl = gross_pnl - trade_cost
            if current_balance + net_pnl <= 0:
                net_pnl = -float(current_balance)
                gross_pnl = net_pnl + trade_cost
                current_balance = 0.0
                bankrupt = True
                bankruptcy_index = int(event.bar_index)
            else:
                current_balance += net_pnl

            row_index = int(event.bar_index)
            history_df.loc[row_index, 'trade_cost'] = float(history_df.loc[row_index, 'trade_cost']) + trade_cost
            history_df.at[row_index, 'trade_cost_breakdown'] = merge_cost_breakdown_items(
                history_df.at[row_index, 'trade_cost_breakdown'],
                trade_cost_breakdown,
            )
            history_df.loc[row_index, 'trade_gross_pnl'] = float(history_df.loc[row_index, 'trade_gross_pnl']) + gross_pnl
            history_df.loc[row_index, 'trade_net_pnl'] = float(history_df.loc[row_index, 'trade_net_pnl']) + net_pnl
            history_df.loc[row_index, 'account_balance_delta'] = float(history_df.loc[row_index, 'account_balance_delta']) + net_pnl
            strategy_series = per_strategy_deltas.setdefault(event.strategy_id, [0.0] * len(history_df.index))
            if row_index < len(strategy_series):
                strategy_series[row_index] = float(strategy_series[row_index]) + float(net_pnl)

            per_strategy = per_strategy_stats.setdefault(event.strategy_id, {
                'strategy_id': event.strategy_id,
                'strategy_label': event.strategy_label,
                'trades': 0,
                'net_pnl': 0.0,
                'gross_pnl': 0.0,
                'cost': 0.0,
                'wins': 0,
                'losses': 0,
            })
            per_strategy['trades'] += 1
            per_strategy['net_pnl'] += net_pnl
            per_strategy['gross_pnl'] += gross_pnl
            per_strategy['cost'] += trade_cost
            if net_pnl >= 0:
                per_strategy['wins'] += 1
            else:
                per_strategy['losses'] += 1
            scope_meta = dict(strategy_scope_map.get(event.strategy_id) or {})
            trade_ledger.append({
                'id': f'{event.kind}-{event.strategy_id}-{event.bar_index}-{len(trade_ledger) + 1}',
                'kind': event.kind,
                'strategy_id': event.strategy_id,
                'strategy_label': event.strategy_label,
                'portfolio_id': scope_meta.get('portfolio_id'),
                'portfolio_label': scope_meta.get('portfolio_label'),
                'pipeline_id': scope_meta.get('pipeline_id'),
                'pipeline_label': scope_meta.get('pipeline_label'),
                'symbol': scope_meta.get('symbol'),
                'timeframe': scope_meta.get('timeframe'),
                'volume_mode': scope_meta.get('volume_mode'),
                'side': event.side,
                'open_time': open_event.time,
                'open_bar_index': int(open_event.bar_index),
                'open_price': float(open_event.price) if open_event.price is not None else None,
                'close_time': event.time,
                'close_bar_index': int(event.bar_index),
                'close_price': float(event.price) if event.price is not None else None,
                'gross_pnl': float(gross_pnl),
                'trade_cost': float(trade_cost),
                'trade_cost_breakdown': trade_cost_breakdown,
                'net_pnl': float(net_pnl),
                'exit_reason': (
                    str((event.metadata or {}).get('stop_type') or '').strip()
                    if event.kind == 'stop'
                    else event.kind
                ),
            })

        running_balance = float(self.initial_balance)
        balances = []
        for _, row in history_df.iterrows():
            running_balance += float(row.get('account_balance_delta') or 0.0)
            if running_balance < 0:
                running_balance = 0.0
            balances.append(running_balance)
        history_df['account_balance'] = balances
        portfolio_analytics = self._build_portfolio_analytics(
            history_df=history_df,
            per_strategy_deltas=per_strategy_deltas,
        )
        skip_open_conflict_count = int(sum(
            1
            for event in self.execution.events
            if event.kind == 'skip_open' and str((event.metadata or {}).get('reason') or '').strip() == 'conflict_with_portfolio_direction'
        ))
        skip_open_existing_position_count = int(sum(
            1
            for event in self.execution.events
            if event.kind == 'skip_open' and str((event.metadata or {}).get('reason') or '').strip() == 'existing_position'
        ))

        analyzer.results = history_df.copy()
        analyzer.bankrupt = bool(bankrupt)
        analyzer.bankruptcy_index = bankruptcy_index
        analyzer._get_stats()

        self.stats = {
            **dict(analyzer.stats or {}),
            'strategy_count': int(len(self.strategy_entries)),
            'portfolio_event_counts': {
                'open': int(sum(1 for event in self.execution.events if event.kind == 'open')),
                'close': int(sum(1 for event in self.execution.events if event.kind == 'close')),
                'stop': int(sum(1 for event in self.execution.events if event.kind == 'stop')),
                'skip_open': int(sum(1 for event in self.execution.events if event.kind == 'skip_open')),
                'skip_open_conflict': skip_open_conflict_count,
                'skip_open_existing_position': skip_open_existing_position_count,
            },
            'portfolio_active_strategy_ids': [
                item['strategy_id']
                for item in self.strategy_entries
                if item.get('enabled', True)
            ],
            'portfolio_strategy_stats': [
                {
                    **item,
                    **dict(strategy_scope_map.get(item['strategy_id']) or {}),
                    'win_rate': (float(item['wins']) / float(item['trades'])) if item['trades'] else 0.0,
                }
                for item in per_strategy_stats.values()
            ],
            'portfolio_analytics': portfolio_analytics,
        }
        self.ledger = trade_ledger
        self.scope_tree = build_scope_tree_from_strategy_stats(self.stats.get('portfolio_strategy_stats'))
        self.rollups = build_scope_rollups_from_strategy_stats(self.stats.get('portfolio_strategy_stats'))
        self.stats['scope_tree'] = self.scope_tree
        self.stats['rollups'] = self.rollups
        self.stats['ledger'] = list(self.ledger)
        return history_df

    def _build_trade_markers_legacy(self):
        markers = []
        strategy_scope_map = self._build_strategy_scope_map()
        cutoff_index = self.stats.get('bankruptcy_index') if isinstance(self.stats, dict) else None
        for event in self.execution.events:
            scope_meta = dict(strategy_scope_map.get(event.strategy_id) or {})
            if cutoff_index is not None and int(event.bar_index) > int(cutoff_index):
                continue
            if event.kind == 'skip_open':
                markers.append({
                    'id': f'skip-{event.strategy_id}-{event.bar_index}',
                    'time': event.time,
                    'position': 'inBar',
                    'shape': 'circle',
                    'color': '#f59e0b',
                    'text': f'{event.strategy_label} skipped | {event.metadata.get("reason", "conflict")}',
                    'size': 1,
                    **scope_meta,
                })
                continue
            if event.kind == 'open':
                markers.append({
                    'id': f'open-{event.strategy_id}-{event.bar_index}',
                    'time': event.time,
                    'position': 'belowBar' if event.side == 'long' else 'aboveBar',
                    'shape': 'arrowUp' if event.side == 'long' else 'arrowDown',
                    'color': '#f3f4f6',
                    'text': f'{event.strategy_label} {event.side} open @ {event.price:.5f}',
                    'size': 1,
                    **scope_meta,
                })
                continue
            if event.kind == 'close':
                markers.append({
                    'id': f'close-{event.strategy_id}-{event.bar_index}',
                    'time': event.time,
                    'position': 'aboveBar' if event.side == 'long' else 'belowBar',
                    'shape': 'square',
                    'color': '#22c55e',
                    'text': f'{event.strategy_label} {event.side} close @ {event.price:.5f}',
                    'size': 1,
                    **scope_meta,
                })
                continue
            if event.kind == 'stop':
                stop_type = event.metadata.get('stop_type', 'stop')
                markers.append({
                    'id': f'stop-{event.strategy_id}-{event.bar_index}',
                    'time': event.time,
                    'position': 'aboveBar' if event.side == 'long' else 'belowBar',
                    'shape': 'circle',
                    'color': '#f97316' if stop_type == 'loss' else '#22c55e' if stop_type == 'gain' else '#eab308',
                    'text': f'{event.strategy_label} {event.side} {stop_type} @ {event.price:.5f}',
                    'size': 1,
                    **scope_meta,
                })
        return markers

    def _sort_trade_timeline_item(self, item):
        opportunity = dict(item.get('opportunity') or {})
        time_value = item.get('time')
        bar_index = int(item.get('bar_index') or 0)
        phase_order = 0 if item.get('phase') == 'close' else 1
        if time_value is None:
            return (1, bar_index, phase_order, int(opportunity.get('priority') or 0), str(opportunity.get('strategy_id') or ''))
        return (0, int(time_value), phase_order, int(opportunity.get('priority') or 0), str(opportunity.get('strategy_id') or ''))

    def _replay_trade_opportunities(self, opportunities):
        analyzer = self._build_analyzer()
        current_balance = float(self.initial_balance)
        current_margin_in_use = 0.0
        bankrupt = False
        bankruptcy_index = None
        active_positions = {}
        replayed_trades = []
        sizing_skips = []
        sleeve_virtual_equity = {}

        timeline = []
        for opportunity in list(opportunities or []):
            timeline.append({
                'phase': 'open',
                'time': opportunity.get('open_time'),
                'bar_index': int(opportunity.get('open_bar_index') or 0),
                'opportunity': opportunity,
            })
            timeline.append({
                'phase': 'close',
                'time': opportunity.get('close_time'),
                'bar_index': int(opportunity.get('close_bar_index') or 0),
                'opportunity': opportunity,
            })
        timeline.sort(key=self._sort_trade_timeline_item)

        for item in timeline:
            opportunity = dict(item.get('opportunity') or {})
            opportunity_id = str(opportunity.get('id') or '').strip()
            if not opportunity_id:
                continue
            if bankrupt:
                break

            if item.get('phase') == 'open':
                available_margin_before = max(current_balance - current_margin_in_use, 0.0)
                strategy_id = str(opportunity.get('strategy_id') or '').strip()
                reference_capital = opportunity.get('reference_capital')
                sleeve_virtual_equity_before = sleeve_virtual_equity.get(strategy_id)
                if sleeve_virtual_equity_before is None:
                    sleeve_virtual_equity_before = reference_capital or float(self.initial_balance)
                    sleeve_virtual_equity[strategy_id] = sleeve_virtual_equity_before

                volume_resolution = resolve_trade_volume(
                    capital_model=self.normalized_capital_model,
                    volume_mode=opportunity.get('volume_mode'),
                    initial_volume=self.initial_volume,
                    fixed_volume=opportunity.get('fixed_volume'),
                    base_volume=opportunity.get('base_volume'),
                    max_volume_cap=opportunity.get('max_volume_cap'),
                    reference_capital=reference_capital,
                    sleeve_virtual_equity=sleeve_virtual_equity_before,
                    available_margin=available_margin_before,
                    open_price=opportunity.get('open_price'),
                    side=opportunity.get('side'),
                )

                if volume_resolution.get('status') != 'ok':
                    sizing_skips.append({
                        'id': f'sizing-skip:{opportunity_id}',
                        'kind': 'skip_open',
                        'strategy_id': opportunity.get('strategy_id'),
                        'strategy_label': opportunity.get('strategy_label'),
                        'portfolio_id': opportunity.get('portfolio_id'),
                        'portfolio_label': opportunity.get('portfolio_label'),
                        'pipeline_id': opportunity.get('pipeline_id'),
                        'pipeline_label': opportunity.get('pipeline_label'),
                        'symbol': opportunity.get('symbol'),
                        'timeframe': opportunity.get('timeframe'),
                        'volume_mode': opportunity.get('volume_mode'),
                        'side': opportunity.get('side'),
                        'time': opportunity.get('open_time'),
                        'bar_index': int(opportunity.get('open_bar_index') or 0),
                        'reason': volume_resolution.get('reason') or 'insufficient_margin',
                        'requested_volume': float(volume_resolution.get('requested_volume') or 0.0),
                        'max_affordable_volume': float(volume_resolution.get('max_affordable_volume') or 0.0),
                        'available_margin_before': float(volume_resolution.get('available_margin_before') or 0.0),
                    })
                    continue

                current_margin_in_use += float(volume_resolution.get('required_margin') or 0.0)
                active_positions[opportunity_id] = {
                    **opportunity,
                    **volume_resolution,
                    'sleeve_virtual_equity_before': float(sleeve_virtual_equity_before),
                }
                continue

            active_trade = active_positions.pop(opportunity_id, None)
            if active_trade is None:
                continue

            gross_pnl = analyzer.gross_result(
                active_trade['side'],
                active_trade['open_price'],
                active_trade['close_price'],
                active_trade['executed_volume'],
            )
            trade_cost_breakdown = analyzer.trade_cost_breakdown(
                volume=active_trade['executed_volume'],
                open_price=active_trade['open_price'],
                close_price=active_trade['close_price'],
                open_time=active_trade['open_time'],
                close_time=active_trade['close_time'],
                gross_pnl=gross_pnl,
            )
            trade_cost = float(sum(float(cost_item.get('amount') or 0.0) for cost_item in trade_cost_breakdown))
            net_pnl = float(gross_pnl) - trade_cost
            if current_balance + net_pnl <= 0:
                net_pnl = -float(current_balance)
                gross_pnl = net_pnl + trade_cost
                current_balance = 0.0
                bankrupt = True
                bankruptcy_index = int(active_trade['close_bar_index'])
            else:
                current_balance += net_pnl

            current_margin_in_use = max(current_margin_in_use - float(active_trade.get('required_margin') or 0.0), 0.0)
            available_margin_after_close = max(current_balance - current_margin_in_use, 0.0)
            sleeve_virtual_equity_after = float(active_trade.get('sleeve_virtual_equity_before') or 0.0) + float(net_pnl)
            sleeve_virtual_equity[str(active_trade.get('strategy_id') or '').strip()] = sleeve_virtual_equity_after

            replayed_trades.append({
                **active_trade,
                'kind': active_trade.get('exit_kind'),
                'gross_pnl': float(gross_pnl),
                'trade_cost': float(trade_cost),
                'trade_cost_breakdown': trade_cost_breakdown,
                'net_pnl': float(net_pnl),
                'available_margin_after_close': float(available_margin_after_close),
                'sleeve_virtual_equity_after': float(sleeve_virtual_equity_after),
            })

        return {
            'trades': replayed_trades,
            'sizing_skips': sizing_skips,
            'bankrupt': bool(bankrupt),
            'bankruptcy_index': bankruptcy_index,
            'final_balance': float(current_balance),
        }

    def _build_results_frame(self):
        if not self.capital_replay_enabled:
            self.replayed_trades = []
            self.sizing_skips = []
            return self._build_results_frame_legacy()

        history_df = self.execution.history.copy()
        if history_df.empty:
            history_df = pd.DataFrame(columns=['time'])

        history_df['trade_cost'] = 0.0
        history_df['trade_cost_breakdown'] = [[] for _ in range(len(history_df.index))]
        history_df['trade_gross_pnl'] = 0.0
        history_df['trade_net_pnl'] = 0.0
        history_df['account_balance_delta'] = 0.0
        history_df['account_balance'] = float(self.initial_balance)
        history_df['trade_requested_volume'] = 0.0
        history_df['trade_executed_volume'] = 0.0
        history_df['trade_required_margin'] = 0.0
        history_df['trade_available_margin_before'] = 0.0
        history_df['trade_available_margin_after_open'] = 0.0
        history_df['trade_volume_details'] = [[] for _ in range(len(history_df.index))]

        analyzer = self._build_analyzer()
        strategy_scope_map = self._build_strategy_scope_map()
        replay = self._replay_trade_opportunities(self.opportunity_tape)
        self.replayed_trades = list(replay.get('trades') or [])
        self.sizing_skips = list(replay.get('sizing_skips') or [])
        bankrupt = bool(replay.get('bankrupt'))
        bankruptcy_index = replay.get('bankruptcy_index')
        per_strategy_stats = {}
        per_strategy_deltas = {}

        for trade in self.replayed_trades:
            row_index = int(trade.get('close_bar_index') or 0)
            if row_index >= len(history_df.index):
                continue
            history_df.loc[row_index, 'trade_cost'] = float(history_df.loc[row_index, 'trade_cost']) + float(trade.get('trade_cost') or 0.0)
            history_df.at[row_index, 'trade_cost_breakdown'] = merge_cost_breakdown_items(
                history_df.at[row_index, 'trade_cost_breakdown'],
                trade.get('trade_cost_breakdown'),
            )
            history_df.loc[row_index, 'trade_gross_pnl'] = float(history_df.loc[row_index, 'trade_gross_pnl']) + float(trade.get('gross_pnl') or 0.0)
            history_df.loc[row_index, 'trade_net_pnl'] = float(history_df.loc[row_index, 'trade_net_pnl']) + float(trade.get('net_pnl') or 0.0)
            history_df.loc[row_index, 'account_balance_delta'] = float(history_df.loc[row_index, 'account_balance_delta']) + float(trade.get('net_pnl') or 0.0)
            history_df.loc[row_index, 'trade_requested_volume'] = float(history_df.loc[row_index, 'trade_requested_volume']) + float(trade.get('requested_volume') or 0.0)
            history_df.loc[row_index, 'trade_executed_volume'] = float(history_df.loc[row_index, 'trade_executed_volume']) + float(trade.get('executed_volume') or 0.0)
            history_df.loc[row_index, 'trade_required_margin'] = float(history_df.loc[row_index, 'trade_required_margin']) + float(trade.get('required_margin') or 0.0)
            history_df.loc[row_index, 'trade_available_margin_before'] = float(history_df.loc[row_index, 'trade_available_margin_before']) + float(trade.get('available_margin_before') or 0.0)
            history_df.loc[row_index, 'trade_available_margin_after_open'] = float(history_df.loc[row_index, 'trade_available_margin_after_open']) + float(trade.get('available_margin_after_open') or 0.0)
            history_df.at[row_index, 'trade_volume_details'] = list(history_df.at[row_index, 'trade_volume_details']) + [{
                'strategy_id': trade.get('strategy_id'),
                'strategy_label': trade.get('strategy_label'),
                'volume_mode': trade.get('volume_mode'),
                'requested_volume': trade.get('requested_volume'),
                'executed_volume': trade.get('executed_volume'),
                'required_margin': trade.get('required_margin'),
            }]

            strategy_id = str(trade.get('strategy_id') or '').strip()
            strategy_series = per_strategy_deltas.setdefault(strategy_id, [0.0] * len(history_df.index))
            if row_index < len(strategy_series):
                strategy_series[row_index] = float(strategy_series[row_index]) + float(trade.get('net_pnl') or 0.0)

            per_strategy = per_strategy_stats.setdefault(strategy_id, {
                'strategy_id': strategy_id,
                'strategy_label': trade.get('strategy_label'),
                'trades': 0,
                'net_pnl': 0.0,
                'gross_pnl': 0.0,
                'cost': 0.0,
                'wins': 0,
                'losses': 0,
                'requested_volume_total': 0.0,
                'executed_volume_total': 0.0,
                'required_margin_total': 0.0,
            })
            per_strategy['trades'] += 1
            per_strategy['net_pnl'] += float(trade.get('net_pnl') or 0.0)
            per_strategy['gross_pnl'] += float(trade.get('gross_pnl') or 0.0)
            per_strategy['cost'] += float(trade.get('trade_cost') or 0.0)
            per_strategy['requested_volume_total'] += float(trade.get('requested_volume') or 0.0)
            per_strategy['executed_volume_total'] += float(trade.get('executed_volume') or 0.0)
            per_strategy['required_margin_total'] += float(trade.get('required_margin') or 0.0)
            if float(trade.get('net_pnl') or 0.0) >= 0:
                per_strategy['wins'] += 1
            else:
                per_strategy['losses'] += 1

        running_balance = float(self.initial_balance)
        balances = []
        for _, row in history_df.iterrows():
            running_balance += float(row.get('account_balance_delta') or 0.0)
            if running_balance < 0:
                running_balance = 0.0
            balances.append(running_balance)
        history_df['account_balance'] = balances

        portfolio_analytics = self._build_portfolio_analytics(
            history_df=history_df,
            per_strategy_deltas=per_strategy_deltas,
        )
        skip_open_conflict_count = int(sum(
            1
            for event in self.execution.events
            if event.kind == 'skip_open' and str((event.metadata or {}).get('reason') or '').strip() == 'conflict_with_portfolio_direction'
        ))
        skip_open_existing_position_count = int(sum(
            1
            for event in self.execution.events
            if event.kind == 'skip_open' and str((event.metadata or {}).get('reason') or '').strip() == 'existing_position'
        ))
        skip_open_sizing_count = int(len(self.sizing_skips))

        analyzer.results = history_df.copy()
        analyzer.bankrupt = bool(bankrupt)
        analyzer.bankruptcy_index = bankruptcy_index
        analyzer._get_stats()

        self.stats = {
            **dict(analyzer.stats or {}),
            'strategy_count': int(len(self.strategy_entries)),
            'portfolio_event_counts': {
                'open': int(len(self.replayed_trades)),
                'close': int(sum(1 for trade in self.replayed_trades if trade.get('exit_kind') == 'close')),
                'stop': int(sum(1 for trade in self.replayed_trades if trade.get('exit_kind') == 'stop')),
                'skip_open': int(skip_open_conflict_count + skip_open_existing_position_count + skip_open_sizing_count),
                'skip_open_conflict': skip_open_conflict_count,
                'skip_open_existing_position': skip_open_existing_position_count,
                'skip_open_sizing': skip_open_sizing_count,
            },
            'portfolio_active_strategy_ids': [
                item['strategy_id']
                for item in self.strategy_entries
                if item.get('enabled', True)
            ],
            'portfolio_strategy_stats': [
                {
                    **item,
                    **dict(strategy_scope_map.get(item['strategy_id']) or {}),
                    'win_rate': (float(item['wins']) / float(item['trades'])) if item['trades'] else 0.0,
                    'avg_requested_volume': (float(item['requested_volume_total']) / float(item['trades'])) if item['trades'] else 0.0,
                    'avg_executed_volume': (float(item['executed_volume_total']) / float(item['trades'])) if item['trades'] else 0.0,
                    'avg_required_margin': (float(item['required_margin_total']) / float(item['trades'])) if item['trades'] else 0.0,
                }
                for item in per_strategy_stats.values()
            ],
            'portfolio_analytics': portfolio_analytics,
        }
        self.ledger = list(self.replayed_trades)
        self.scope_tree = build_scope_tree_from_strategy_stats(self.stats.get('portfolio_strategy_stats'))
        self.rollups = build_scope_rollups_from_strategy_stats(self.stats.get('portfolio_strategy_stats'))
        self.stats['scope_tree'] = self.scope_tree
        self.stats['rollups'] = self.rollups
        self.stats['ledger'] = list(self.ledger)
        return history_df

    def _build_portfolio_analytics(self, *, history_df, per_strategy_deltas):
        total_bars = int(len(history_df.index))
        strategy_histories = {}

        for state in list(self.execution.strategy_states or []):
            history = ((state.metadata or {}).get('history'))
            if history is None or 'position' not in history:
                continue
            strategy_histories[state.strategy_id] = history

        simultaneous_bars = 0
        same_direction_bars = 0
        opposite_direction_bars = 0
        max_concurrent_strategies = 0

        for bar_index in range(total_bars):
            active_positions = []
            for history in strategy_histories.values():
                try:
                    position = int(history.loc[bar_index, 'position'] or 0)
                except Exception:
                    position = 0
                if position != 0:
                    active_positions.append(position)

            concurrent_count = len(active_positions)
            max_concurrent_strategies = max(max_concurrent_strategies, concurrent_count)
            if concurrent_count >= 2:
                simultaneous_bars += 1
                if len(set(active_positions)) == 1:
                    same_direction_bars += 1
                else:
                    opposite_direction_bars += 1

        strategy_ids = []
        seen_ids = set()
        for entry in self.strategy_entries:
            strategy_id = str(entry.get('strategy_id') or '').strip()
            if strategy_id and strategy_id not in seen_ids:
                seen_ids.add(strategy_id)
                strategy_ids.append(strategy_id)

        pairwise = []
        for left_index, left_id in enumerate(strategy_ids):
            for right_id in strategy_ids[left_index + 1:]:
                left_series = pd.Series(per_strategy_deltas.get(left_id, [0.0] * total_bars), dtype=float)
                right_series = pd.Series(per_strategy_deltas.get(right_id, [0.0] * total_bars), dtype=float)
                correlation = left_series.corr(right_series)
                if pd.isna(correlation):
                    correlation = None

                overlap_bars = 0
                same_direction_overlap_bars = 0
                opposite_direction_overlap_bars = 0
                left_history = strategy_histories.get(left_id)
                right_history = strategy_histories.get(right_id)
                for bar_index in range(total_bars):
                    left_position = 0
                    right_position = 0
                    if left_history is not None:
                        try:
                            left_position = int(left_history.loc[bar_index, 'position'] or 0)
                        except Exception:
                            left_position = 0
                    if right_history is not None:
                        try:
                            right_position = int(right_history.loc[bar_index, 'position'] or 0)
                        except Exception:
                            right_position = 0
                    if left_position != 0 and right_position != 0:
                        overlap_bars += 1
                        if left_position == right_position:
                            same_direction_overlap_bars += 1
                        else:
                            opposite_direction_overlap_bars += 1

                pairwise.append({
                    'left_strategy_id': left_id,
                    'right_strategy_id': right_id,
                    'correlation': float(correlation) if correlation is not None else None,
                    'overlap_bars': int(overlap_bars),
                    'same_direction_overlap_rate': (float(same_direction_overlap_bars) / float(total_bars)) if total_bars > 0 else 0.0,
                    'opposite_direction_overlap_rate': (float(opposite_direction_overlap_bars) / float(total_bars)) if total_bars > 0 else 0.0,
                })

        return {
            'simultaneous_position_rate': (float(simultaneous_bars) / float(total_bars)) if total_bars > 0 else 0.0,
            'same_direction_overlap_rate': (float(same_direction_bars) / float(total_bars)) if total_bars > 0 else 0.0,
            'opposite_direction_overlap_rate': (float(opposite_direction_bars) / float(total_bars)) if total_bars > 0 else 0.0,
            'max_concurrent_strategies': int(max_concurrent_strategies),
            'pairwise': pairwise,
        }

    def _build_trade_markers(self):
        if not self.capital_replay_enabled:
            return self._build_trade_markers_legacy()

        markers = []
        strategy_scope_map = self._build_strategy_scope_map()
        cutoff_index = self.stats.get('bankruptcy_index') if isinstance(self.stats, dict) else None

        for event in self.execution.events:
            if event.kind != 'skip_open':
                continue
            scope_meta = dict(strategy_scope_map.get(event.strategy_id) or {})
            if cutoff_index is not None and int(event.bar_index) > int(cutoff_index):
                continue
            markers.append({
                'id': f'skip-{event.strategy_id}-{event.bar_index}',
                'time': event.time,
                'position': 'inBar',
                'shape': 'circle',
                'color': '#f59e0b',
                'text': f'{event.strategy_label} skipped | {event.metadata.get("reason", "conflict")}',
                'size': 1,
                **scope_meta,
            })

        for skip in self.sizing_skips:
            if cutoff_index is not None and int(skip.get('bar_index') or 0) > int(cutoff_index):
                continue
            markers.append({
                'id': str(skip.get('id') or f'sizing-{skip.get("strategy_id")}-{skip.get("bar_index")}'),
                'time': skip.get('time'),
                'position': 'inBar',
                'shape': 'circle',
                'color': '#ef4444',
                'text': (
                    f'{skip.get("strategy_label")} sizing skip | {skip.get("reason")} '
                    f'| req {float(skip.get("requested_volume") or 0.0):.2f} '
                    f'| afford {float(skip.get("max_affordable_volume") or 0.0):.2f}'
                ),
                'size': 1,
                **dict(strategy_scope_map.get(skip.get('strategy_id')) or {}),
            })

        for trade in self.replayed_trades:
            if cutoff_index is not None and int(trade.get('close_bar_index') or 0) > int(cutoff_index):
                continue
            scope_meta = dict(strategy_scope_map.get(trade.get('strategy_id')) or {})
            markers.append({
                'id': f'open-{trade.get("strategy_id")}-{trade.get("open_bar_index")}',
                'time': trade.get('open_time'),
                'position': 'belowBar' if trade.get('side') == 'long' else 'aboveBar',
                'shape': 'arrowUp' if trade.get('side') == 'long' else 'arrowDown',
                'color': '#f3f4f6',
                'text': (
                    f'{trade.get("strategy_label")} {trade.get("side")} open '
                    f'{float(trade.get("executed_volume") or 0.0):.2f} @ {float(trade.get("open_price") or 0.0):.5f}'
                ),
                'size': 1,
                **scope_meta,
            })
            if trade.get('exit_kind') == 'stop':
                stop_type = str(trade.get('exit_reason') or 'stop').strip() or 'stop'
                markers.append({
                    'id': f'stop-{trade.get("strategy_id")}-{trade.get("close_bar_index")}',
                    'time': trade.get('close_time'),
                    'position': 'aboveBar' if trade.get('side') == 'long' else 'belowBar',
                    'shape': 'circle',
                    'color': '#f97316' if stop_type == 'loss' else '#22c55e' if stop_type == 'gain' else '#eab308',
                    'text': (
                        f'{trade.get("strategy_label")} {trade.get("side")} {stop_type} '
                        f'{float(trade.get("executed_volume") or 0.0):.2f} @ {float(trade.get("close_price") or 0.0):.5f}'
                    ),
                    'size': 1,
                    **scope_meta,
                })
                continue
            markers.append({
                'id': f'close-{trade.get("strategy_id")}-{trade.get("close_bar_index")}',
                'time': trade.get('close_time'),
                'position': 'aboveBar' if trade.get('side') == 'long' else 'belowBar',
                'shape': 'square',
                'color': '#22c55e',
                'text': (
                    f'{trade.get("strategy_label")} {trade.get("side")} close '
                    f'{float(trade.get("executed_volume") or 0.0):.2f} @ {float(trade.get("close_price") or 0.0):.5f}'
                ),
                'size': 1,
                **scope_meta,
            })
        return markers

    def run(self):
        prepared_entries = self._prepare_strategy_entries()
        self.execution = MultiStrategyExecutionEngine(
            prepared_entries,
            self.symbol,
            allow_hedge=self.allow_hedge,
        ).run()
        self.normalized_capital_model = normalize_capital_model(
            self.raw_capital_model,
            asset_type=self.asset_type,
            symbol=getattr(self.symbol, 'name', ''),
            initial_balance=self.initial_balance,
        )
        self.opportunity_tape = self._build_trade_opportunity_tape()
        self.results = self._build_results_frame()
        self.trade_markers = self._build_trade_markers()
        return self.results


class PortfolioStackBacktester():
    supports_partial_rerun = False

    def __init__(self, group_runs=None, portfolio_mode='shared_pipe'):
        self.group_runs = list(group_runs or [])
        normalized_portfolio_mode = str(portfolio_mode or 'shared_pipe').strip().lower() or 'shared_pipe'
        if normalized_portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
            normalized_portfolio_mode = 'shared_pipe'
        self.portfolio_mode = normalized_portfolio_mode
        self.trade_markers = []
        self.stats = {}
        self.results = None
        self.execution = None
        self.ledger = []
        self.opportunity_tape = []
        self.replayed_trades = []
        self.sizing_skips = []
        self.scope_tree = {'portfolios': []}
        self.rollups = {'total': {}, 'portfolios': [], 'pipelines': [], 'sleeves': []}
        self.raw_capital_model = None
        self.normalized_capital_model = None
        self.set_params()

    def set_params(
            self,
            initial_balance=10000,
            asset_type='forex',
            initial_volume=1.0,
            pip_size=0.0001,
            pip_value_per_lot=10.0,
            cost_profile='oanda',
            spread_in_pips=1.0,
            entry_slippage_in_pips=0.0,
            close_slippage_in_pips=0.0,
            take_profit_slippage_in_pips=0.0,
            stop_loss_slippage_in_pips=0.0,
            trailing_stop_slippage_in_pips=0.0,
            minimum_stop_distance_in_pips=0.0,
            volatility_slippage_multiplier=0.0,
            execution_mode='next_bar_open',
            portfolio_mode='shared_pipe',
            history_scope_mode='loaded_chart',
            history_scope_bars=None,
            broker_cost_context=None,
            capital_model=None,
        ):
        self.initial_balance = initial_balance
        self.broker_cost_context = resolve_broker_cost_context(broker_cost_context)
        self.requested_cost_profile = normalize_backtest_cost_profile(cost_profile)
        self.asset_type = resolve_backtest_asset_type(
            asset_type,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
            cost_profile=self.requested_cost_profile,
        )
        self.initial_volume = initial_volume
        self.pip_size = pip_size
        self.pip_value_per_lot = pip_value_per_lot
        self.cost_profile = resolve_effective_backtest_cost_profile(
            self.requested_cost_profile,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
        )
        self.spread_in_pips = spread_in_pips
        self.entry_slippage_in_pips = entry_slippage_in_pips
        self.close_slippage_in_pips = close_slippage_in_pips
        self.take_profit_slippage_in_pips = take_profit_slippage_in_pips
        self.stop_loss_slippage_in_pips = stop_loss_slippage_in_pips
        self.trailing_stop_slippage_in_pips = trailing_stop_slippage_in_pips
        self.minimum_stop_distance_in_pips = minimum_stop_distance_in_pips
        self.volatility_slippage_multiplier = volatility_slippage_multiplier
        self.execution_mode = execution_mode
        normalized_portfolio_mode = str(portfolio_mode or self.portfolio_mode or 'shared_pipe').strip().lower() or 'shared_pipe'
        if normalized_portfolio_mode not in {'shared_pipe', 'parallel_sleeves'}:
            normalized_portfolio_mode = 'shared_pipe'
        self.portfolio_mode = normalized_portfolio_mode
        self.history_scope_mode = history_scope_mode
        self.history_scope_bars = history_scope_bars
        self.raw_capital_model = dict(capital_model or {}) if isinstance(capital_model, dict) else None
        primary_symbol = ''
        if self.group_runs:
            primary_symbol = str(self.group_runs[0].get('symbol') or '').strip().upper()
        self.normalized_capital_model = normalize_capital_model(
            self.raw_capital_model,
            asset_type=self.asset_type,
            symbol=primary_symbol,
            initial_balance=self.initial_balance,
        )

    def _build_analyzer(self):
        analyzer = BacktestPerformanceAnalyzer(
            [],
            initial_balance=self.initial_balance,
            asset_type=self.asset_type,
            initial_volume=self.initial_volume,
            pip_size=self.pip_size,
            pip_value_per_lot=self.pip_value_per_lot,
            spread_in_pips=self.spread_in_pips,
            cost_profile=self.requested_cost_profile,
            broker_cost_context=self.broker_cost_context,
        )
        analyzer.execution_policy = {
            **build_backtest_cost_policy({
                'assetType': self.asset_type,
                'initialVolume': self.initial_volume,
                'pipValuePerLot': self.pip_value_per_lot,
                'costProfile': self.requested_cost_profile,
                'spreadInPips': self.spread_in_pips,
                'entrySlippageInPips': self.entry_slippage_in_pips,
                'closeSlippageInPips': self.close_slippage_in_pips,
                'takeProfitSlippageInPips': self.take_profit_slippage_in_pips,
                'stopLossSlippageInPips': self.stop_loss_slippage_in_pips,
                'trailingStopSlippageInPips': self.trailing_stop_slippage_in_pips,
                'minimumStopDistanceInPips': self.minimum_stop_distance_in_pips,
                'volatilitySlippageMultiplier': self.volatility_slippage_multiplier,
            }, broker_profile=self.broker_cost_context),
            'execution_mode': self.execution_mode,
            'portfolio_mode': self.portfolio_mode,
            'portfolio_structure': 'multi_market_stack',
            'market_group_count': int(len(self.group_runs)),
            'spread_in_pips': self.spread_in_pips,
            'entry_slippage_in_pips': self.entry_slippage_in_pips,
            'close_slippage_in_pips': self.close_slippage_in_pips,
            'take_profit_slippage_in_pips': self.take_profit_slippage_in_pips,
            'stop_loss_slippage_in_pips': self.stop_loss_slippage_in_pips,
            'trailing_stop_slippage_in_pips': self.trailing_stop_slippage_in_pips,
            'minimum_stop_distance_in_pips': self.minimum_stop_distance_in_pips,
            'volatility_slippage_multiplier': self.volatility_slippage_multiplier,
            'volatility_slippage_reference': 'previous_bar_range',
            'history_scope_mode': self.history_scope_mode,
            'history_scope_bars': self.history_scope_bars,
            **build_capital_policy(self.normalized_capital_model),
        }
        return analyzer

    def _safe_results_frame(self, results):
        if results is None:
            return pd.DataFrame()
        if isinstance(results, pd.DataFrame):
            return results.copy()
        return pd.DataFrame(results)

    def _supports_capital_replay(self):
        if not self.group_runs:
            return False
        return all(
            hasattr(group_run.get('backtester'), 'opportunity_tape')
            and bool(getattr(group_run.get('backtester'), 'capital_replay_enabled', False))
            for group_run in self.group_runs
        )

    def _build_group_entry_scope_map(self):
        scope_map = {}
        for group_run in self.group_runs:
            for entry in list(group_run.get('entries') or []):
                strategy_id = str(entry.get('strategy_id') or '').strip()
                if not strategy_id:
                    continue
                scope_map[strategy_id] = _build_scope_metadata(entry)
        return scope_map

    def _build_combined_opportunity_tape(self):
        opportunities = []
        for group_index, group_run in enumerate(self.group_runs):
            market_label = str(group_run.get('market_label') or '').strip()
            backtester = group_run.get('backtester')
            for raw_opportunity in list(getattr(backtester, 'opportunity_tape', []) or []):
                opportunity = dict(raw_opportunity or {})
                opportunity_id = str(opportunity.get('id') or f'group-{group_index}-trade-{len(opportunities) + 1}').strip() or f'group-{group_index}-trade-{len(opportunities) + 1}'
                opportunities.append({
                    **opportunity,
                    'id': f'{group_index}:{opportunity_id}',
                    'market_label': market_label,
                })
        return opportunities

    def _normalize_capital_model_for_symbol(self, symbol_name=''):
        return normalize_capital_model(
            self.raw_capital_model,
            asset_type=self.asset_type,
            symbol=symbol_name,
            initial_balance=self.initial_balance,
        )

    def _sort_trade_timeline_item(self, item):
        opportunity = dict(item.get('opportunity') or {})
        time_value = item.get('time')
        bar_index = int(item.get('bar_index') or 0)
        phase_order = 0 if item.get('phase') == 'close' else 1
        if time_value is None:
            return (1, bar_index, phase_order, int(opportunity.get('priority') or 0), str(opportunity.get('strategy_id') or ''))
        return (0, int(time_value), phase_order, int(opportunity.get('priority') or 0), str(opportunity.get('strategy_id') or ''))

    def _replay_trade_opportunities(self, opportunities):
        analyzer = self._build_analyzer()
        current_balance = float(self.initial_balance)
        current_margin_in_use = 0.0
        bankrupt = False
        bankruptcy_index = None
        active_positions = {}
        replayed_trades = []
        sizing_skips = []
        sleeve_virtual_equity = {}

        timeline = []
        for opportunity in list(opportunities or []):
            timeline.append({
                'phase': 'open',
                'time': opportunity.get('open_time'),
                'bar_index': int(opportunity.get('open_bar_index') or 0),
                'opportunity': opportunity,
            })
            timeline.append({
                'phase': 'close',
                'time': opportunity.get('close_time'),
                'bar_index': int(opportunity.get('close_bar_index') or 0),
                'opportunity': opportunity,
            })
        timeline.sort(key=self._sort_trade_timeline_item)

        for item in timeline:
            opportunity = dict(item.get('opportunity') or {})
            opportunity_id = str(opportunity.get('id') or '').strip()
            if not opportunity_id:
                continue
            if bankrupt:
                break

            if item.get('phase') == 'open':
                symbol_name = str(opportunity.get('symbol') or '').strip().upper()
                capital_model = self._normalize_capital_model_for_symbol(symbol_name)
                available_margin_before = max(current_balance - current_margin_in_use, 0.0)
                strategy_id = str(opportunity.get('strategy_id') or '').strip()
                reference_capital = opportunity.get('reference_capital')
                sleeve_virtual_equity_before = sleeve_virtual_equity.get(strategy_id)
                if sleeve_virtual_equity_before is None:
                    sleeve_virtual_equity_before = reference_capital or float(self.initial_balance)
                    sleeve_virtual_equity[strategy_id] = sleeve_virtual_equity_before

                volume_resolution = resolve_trade_volume(
                    capital_model=capital_model,
                    volume_mode=opportunity.get('volume_mode'),
                    initial_volume=self.initial_volume,
                    fixed_volume=opportunity.get('fixed_volume'),
                    base_volume=opportunity.get('base_volume'),
                    max_volume_cap=opportunity.get('max_volume_cap'),
                    reference_capital=reference_capital,
                    sleeve_virtual_equity=sleeve_virtual_equity_before,
                    available_margin=available_margin_before,
                    open_price=opportunity.get('open_price'),
                    side=opportunity.get('side'),
                )

                if volume_resolution.get('status') != 'ok':
                    sizing_skips.append({
                        'id': f'sizing-skip:{opportunity_id}',
                        'kind': 'skip_open',
                        'strategy_id': opportunity.get('strategy_id'),
                        'strategy_label': opportunity.get('strategy_label'),
                        'portfolio_id': opportunity.get('portfolio_id'),
                        'portfolio_label': opportunity.get('portfolio_label'),
                        'pipeline_id': opportunity.get('pipeline_id'),
                        'pipeline_label': opportunity.get('pipeline_label'),
                        'symbol': opportunity.get('symbol'),
                        'timeframe': opportunity.get('timeframe'),
                        'market_label': opportunity.get('market_label'),
                        'volume_mode': opportunity.get('volume_mode'),
                        'side': opportunity.get('side'),
                        'time': opportunity.get('open_time'),
                        'bar_index': int(opportunity.get('open_bar_index') or 0),
                        'reason': volume_resolution.get('reason') or 'insufficient_margin',
                        'requested_volume': float(volume_resolution.get('requested_volume') or 0.0),
                        'max_affordable_volume': float(volume_resolution.get('max_affordable_volume') or 0.0),
                        'available_margin_before': float(volume_resolution.get('available_margin_before') or 0.0),
                    })
                    continue

                current_margin_in_use += float(volume_resolution.get('required_margin') or 0.0)
                active_positions[opportunity_id] = {
                    **opportunity,
                    **volume_resolution,
                    'capital_model': capital_model,
                    'sleeve_virtual_equity_before': float(sleeve_virtual_equity_before),
                }
                continue

            active_trade = active_positions.pop(opportunity_id, None)
            if active_trade is None:
                continue

            gross_pnl = analyzer.gross_result(
                active_trade['side'],
                active_trade['open_price'],
                active_trade['close_price'],
                active_trade['executed_volume'],
            )
            trade_cost_breakdown = analyzer.trade_cost_breakdown(
                volume=active_trade['executed_volume'],
                open_price=active_trade['open_price'],
                close_price=active_trade['close_price'],
                open_time=active_trade['open_time'],
                close_time=active_trade['close_time'],
                gross_pnl=gross_pnl,
            )
            trade_cost = float(sum(float(cost_item.get('amount') or 0.0) for cost_item in trade_cost_breakdown))
            net_pnl = float(gross_pnl) - trade_cost
            if current_balance + net_pnl <= 0:
                net_pnl = -float(current_balance)
                gross_pnl = net_pnl + trade_cost
                current_balance = 0.0
                bankrupt = True
                bankruptcy_index = int(active_trade['close_bar_index'])
            else:
                current_balance += net_pnl

            current_margin_in_use = max(current_margin_in_use - float(active_trade.get('required_margin') or 0.0), 0.0)
            available_margin_after_close = max(current_balance - current_margin_in_use, 0.0)
            sleeve_virtual_equity_after = float(active_trade.get('sleeve_virtual_equity_before') or 0.0) + float(net_pnl)
            sleeve_virtual_equity[str(active_trade.get('strategy_id') or '').strip()] = sleeve_virtual_equity_after

            replayed_trades.append({
                **active_trade,
                'kind': active_trade.get('exit_kind'),
                'gross_pnl': float(gross_pnl),
                'trade_cost': float(trade_cost),
                'trade_cost_breakdown': trade_cost_breakdown,
                'net_pnl': float(net_pnl),
                'available_margin_after_close': float(available_margin_after_close),
                'sleeve_virtual_equity_after': float(sleeve_virtual_equity_after),
            })

        return {
            'trades': replayed_trades,
            'sizing_skips': sizing_skips,
            'bankrupt': bool(bankrupt),
            'bankruptcy_index': bankruptcy_index,
            'final_balance': float(current_balance),
        }

    def _build_replayed_strategy_stats(self):
        aggregated = {}
        scope_map = self._build_group_entry_scope_map()

        for trade in self.replayed_trades:
            strategy_id = str(trade.get('strategy_id') or '').strip()
            if not strategy_id:
                continue
            current = aggregated.setdefault(strategy_id, {
                'strategy_id': strategy_id,
                'strategy_label': str(trade.get('strategy_label') or strategy_id).strip() or strategy_id,
                **dict(scope_map.get(strategy_id) or {}),
                'symbol': str(trade.get('symbol') or '').strip().upper(),
                'timeframe': str(trade.get('timeframe') or '').strip().upper(),
                'trades': 0,
                'net_pnl': 0.0,
                'gross_pnl': 0.0,
                'cost': 0.0,
                'wins': 0,
                'losses': 0,
                'requested_volume_total': 0.0,
                'executed_volume_total': 0.0,
                'required_margin_total': 0.0,
            })
            current['trades'] += 1
            current['net_pnl'] += float(trade.get('net_pnl') or 0.0)
            current['gross_pnl'] += float(trade.get('gross_pnl') or 0.0)
            current['cost'] += float(trade.get('trade_cost') or 0.0)
            current['requested_volume_total'] += float(trade.get('requested_volume') or 0.0)
            current['executed_volume_total'] += float(trade.get('executed_volume') or 0.0)
            current['required_margin_total'] += float(trade.get('required_margin') or 0.0)
            if float(trade.get('net_pnl') or 0.0) >= 0:
                current['wins'] += 1
            else:
                current['losses'] += 1

        rows = []
        for item in aggregated.values():
            rows.append({
                **item,
                'win_rate': (float(item['wins']) / float(item['trades'])) if item['trades'] else 0.0,
                'avg_requested_volume': (float(item['requested_volume_total']) / float(item['trades'])) if item['trades'] else 0.0,
                'avg_executed_volume': (float(item['executed_volume_total']) / float(item['trades'])) if item['trades'] else 0.0,
                'avg_required_margin': (float(item['required_margin_total']) / float(item['trades'])) if item['trades'] else 0.0,
            })
        rows.sort(key=lambda item: (str(item.get('symbol') or ''), str(item.get('timeframe') or ''), str(item.get('strategy_id') or '')))
        return rows

    def _build_results_frame_from_replay(self):
        self.opportunity_tape = self._build_combined_opportunity_tape()
        replay = self._replay_trade_opportunities(self.opportunity_tape)
        self.replayed_trades = list(replay.get('trades') or [])
        self.sizing_skips = list(replay.get('sizing_skips') or [])

        grouped_rows = {}
        running_balance = float(self.initial_balance)

        for trade in self.replayed_trades:
            time_value = trade.get('close_time')
            try:
                time_key = int(time_value)
            except Exception:
                continue
            bucket = grouped_rows.setdefault(time_key, {
                'time': time_key,
                'trade_cost': 0.0,
                'trade_cost_breakdown': [],
                'trade_gross_pnl': 0.0,
                'trade_net_pnl': 0.0,
                'account_balance_delta': 0.0,
                'market_labels': set(),
                'strategy_ids': set(),
                'trade_requested_volume': 0.0,
                'trade_executed_volume': 0.0,
                'trade_required_margin': 0.0,
            })
            bucket['trade_cost'] += float(trade.get('trade_cost') or 0.0)
            bucket['trade_cost_breakdown'] = merge_cost_breakdown_items(
                bucket.get('trade_cost_breakdown'),
                trade.get('trade_cost_breakdown'),
            )
            bucket['trade_gross_pnl'] += float(trade.get('gross_pnl') or 0.0)
            bucket['trade_net_pnl'] += float(trade.get('net_pnl') or 0.0)
            bucket['account_balance_delta'] += float(trade.get('net_pnl') or 0.0)
            bucket['trade_requested_volume'] += float(trade.get('requested_volume') or 0.0)
            bucket['trade_executed_volume'] += float(trade.get('executed_volume') or 0.0)
            bucket['trade_required_margin'] += float(trade.get('required_margin') or 0.0)
            if trade.get('market_label'):
                bucket['market_labels'].add(str(trade.get('market_label')).strip())
            bucket['strategy_ids'].add(str(trade.get('strategy_id') or '').strip())

        rows = []
        for time_key in sorted(grouped_rows):
            row = grouped_rows[time_key]
            running_balance += float(row['account_balance_delta'])
            rows.append({
                'time': row['time'],
                'trade_cost': row['trade_cost'],
                'trade_cost_breakdown': row['trade_cost_breakdown'],
                'trade_gross_pnl': row['trade_gross_pnl'],
                'trade_net_pnl': row['trade_net_pnl'],
                'account_balance_delta': row['account_balance_delta'],
                'account_balance': running_balance,
                'market_labels': ' | '.join(sorted(row['market_labels'])),
                'strategy_ids': ' | '.join(sorted(item for item in row['strategy_ids'] if item)),
                'trade_requested_volume': row['trade_requested_volume'],
                'trade_executed_volume': row['trade_executed_volume'],
                'trade_required_margin': row['trade_required_margin'],
            })

        if not rows:
            return pd.DataFrame(columns=[
                'time',
                'trade_cost',
                'trade_cost_breakdown',
                'trade_gross_pnl',
                'trade_net_pnl',
                'account_balance_delta',
                'account_balance',
                'market_labels',
                'strategy_ids',
                'trade_requested_volume',
                'trade_executed_volume',
                'trade_required_margin',
            ])

        return pd.DataFrame(rows)

    def _build_results_frame(self):
        if self._supports_capital_replay():
            return self._build_results_frame_from_replay()

        grouped_rows = {}

        for group_run in self.group_runs:
            results = self._safe_results_frame(group_run.get('results'))
            if results.empty:
                continue

            trade_cost = pd.to_numeric(results.get('trade_cost', 0.0), errors='coerce').fillna(0.0)
            trade_gross_pnl = pd.to_numeric(results.get('trade_gross_pnl', 0.0), errors='coerce').fillna(0.0)
            trade_net_pnl = pd.to_numeric(results.get('trade_net_pnl', 0.0), errors='coerce').fillna(0.0)
            trade_mask = (trade_cost != 0.0) | (trade_gross_pnl != 0.0) | (trade_net_pnl != 0.0)
            market_label = str(group_run.get('market_label') or '').strip()
            strategy_ids = {
                str(entry.get('strategy_id') or '').strip()
                for entry in list(group_run.get('entries') or [])
                if str(entry.get('strategy_id') or '').strip()
            }

            for _, row in results.loc[trade_mask].iterrows():
                time_value = row.get('time')
                try:
                    time_key = int(time_value)
                except Exception:
                    continue

                bucket = grouped_rows.setdefault(time_key, {
                    'time': time_key,
                    'trade_cost': 0.0,
                    'trade_cost_breakdown': [],
                    'trade_gross_pnl': 0.0,
                    'trade_net_pnl': 0.0,
                    'account_balance_delta': 0.0,
                    'market_labels': set(),
                    'strategy_ids': set(),
                })
                bucket['trade_cost'] += float(row.get('trade_cost') or 0.0)
                bucket['trade_cost_breakdown'] = merge_cost_breakdown_items(
                    bucket.get('trade_cost_breakdown'),
                    row.get('trade_cost_breakdown'),
                )
                bucket['trade_gross_pnl'] += float(row.get('trade_gross_pnl') or 0.0)
                bucket['trade_net_pnl'] += float(row.get('trade_net_pnl') or 0.0)
                bucket['account_balance_delta'] += float(row.get('trade_net_pnl') or 0.0)
                if market_label:
                    bucket['market_labels'].add(market_label)
                bucket['strategy_ids'].update(strategy_ids)

        rows = []
        running_balance = float(self.initial_balance)
        for time_key in sorted(grouped_rows):
            row = grouped_rows[time_key]
            running_balance += float(row['account_balance_delta'])
            rows.append({
                'time': row['time'],
                'trade_cost': row['trade_cost'],
                'trade_cost_breakdown': row['trade_cost_breakdown'],
                'trade_gross_pnl': row['trade_gross_pnl'],
                'trade_net_pnl': row['trade_net_pnl'],
                'account_balance_delta': row['account_balance_delta'],
                'account_balance': running_balance,
                'market_labels': ' | '.join(sorted(row['market_labels'])),
                'strategy_ids': ' | '.join(sorted(row['strategy_ids'])),
            })

        if not rows:
            return pd.DataFrame(columns=[
                'time',
                'trade_cost',
                'trade_cost_breakdown',
                'trade_gross_pnl',
                'trade_net_pnl',
                'account_balance_delta',
                'account_balance',
                'market_labels',
                'strategy_ids',
            ])

        return pd.DataFrame(rows)

    def _build_trade_markers(self):
        if self._supports_capital_replay():
            markers = []
            for group_index, group_run in enumerate(self.group_runs):
                market_label = str(group_run.get('market_label') or '').strip()
                backtester = group_run.get('backtester')
                for marker in list(getattr(backtester, 'trade_markers', []) or []):
                    safe_marker = dict(marker or {})
                    marker_id = str(safe_marker.get('id') or f'marker-{group_index}').strip() or f'marker-{group_index}'
                    marker_text = str(safe_marker.get('text') or '').strip()
                    if str(safe_marker.get('shape') or '') == 'circle' and 'skip' in str(marker_id).lower():
                        markers.append({
                            **safe_marker,
                            'id': f'{group_index}:{marker_id}',
                            'text': f'[{market_label}] {marker_text}'.strip() if market_label else marker_text,
                        })
                # do not reuse child open/close markers because their volume may have changed after global replay

            for skip in self.sizing_skips:
                market_label = str(skip.get('market_label') or '').strip()
                markers.append({
                    'id': str(skip.get('id') or f'sizing-{skip.get("strategy_id")}-{skip.get("bar_index")}'),
                    'time': skip.get('time'),
                    'position': 'inBar',
                    'shape': 'circle',
                    'color': '#ef4444',
                    'text': (
                        f'[{market_label}] {skip.get("strategy_label")} sizing skip | {skip.get("reason")} '
                        f'| req {float(skip.get("requested_volume") or 0.0):.2f} '
                        f'| afford {float(skip.get("max_affordable_volume") or 0.0):.2f}'
                    ).strip(),
                    'size': 1,
                    'strategy_id': skip.get('strategy_id'),
                    'strategy_label': skip.get('strategy_label'),
                    'portfolio_id': skip.get('portfolio_id'),
                    'portfolio_label': skip.get('portfolio_label'),
                    'pipeline_id': skip.get('pipeline_id'),
                    'pipeline_label': skip.get('pipeline_label'),
                    'symbol': skip.get('symbol'),
                    'timeframe': skip.get('timeframe'),
                    'volume_mode': skip.get('volume_mode'),
                })

            for trade in self.replayed_trades:
                market_label = str(trade.get('market_label') or '').strip()
                markers.append({
                    'id': f'open-{trade.get("id")}',
                    'time': trade.get('open_time'),
                    'position': 'belowBar' if trade.get('side') == 'long' else 'aboveBar',
                    'shape': 'arrowUp' if trade.get('side') == 'long' else 'arrowDown',
                    'color': '#f3f4f6',
                    'text': (
                        f'[{market_label}] {trade.get("strategy_label")} {trade.get("side")} open '
                        f'{float(trade.get("executed_volume") or 0.0):.2f} @ {float(trade.get("open_price") or 0.0):.5f}'
                    ).strip(),
                    'size': 1,
                    'strategy_id': trade.get('strategy_id'),
                    'strategy_label': trade.get('strategy_label'),
                    'portfolio_id': trade.get('portfolio_id'),
                    'portfolio_label': trade.get('portfolio_label'),
                    'pipeline_id': trade.get('pipeline_id'),
                    'pipeline_label': trade.get('pipeline_label'),
                    'symbol': trade.get('symbol'),
                    'timeframe': trade.get('timeframe'),
                    'volume_mode': trade.get('volume_mode'),
                })
                if trade.get('exit_kind') == 'stop':
                    stop_type = str(trade.get('exit_reason') or 'stop').strip() or 'stop'
                    markers.append({
                        'id': f'stop-{trade.get("id")}',
                        'time': trade.get('close_time'),
                        'position': 'aboveBar' if trade.get('side') == 'long' else 'belowBar',
                        'shape': 'circle',
                        'color': '#f97316' if stop_type == 'loss' else '#22c55e' if stop_type == 'gain' else '#eab308',
                        'text': (
                            f'[{market_label}] {trade.get("strategy_label")} {trade.get("side")} {stop_type} '
                            f'{float(trade.get("executed_volume") or 0.0):.2f} @ {float(trade.get("close_price") or 0.0):.5f}'
                        ).strip(),
                        'size': 1,
                        'strategy_id': trade.get('strategy_id'),
                        'strategy_label': trade.get('strategy_label'),
                        'portfolio_id': trade.get('portfolio_id'),
                        'portfolio_label': trade.get('portfolio_label'),
                        'pipeline_id': trade.get('pipeline_id'),
                        'pipeline_label': trade.get('pipeline_label'),
                        'symbol': trade.get('symbol'),
                        'timeframe': trade.get('timeframe'),
                        'volume_mode': trade.get('volume_mode'),
                    })
                    continue
                markers.append({
                    'id': f'close-{trade.get("id")}',
                    'time': trade.get('close_time'),
                    'position': 'aboveBar' if trade.get('side') == 'long' else 'belowBar',
                    'shape': 'square',
                    'color': '#22c55e',
                    'text': (
                        f'[{market_label}] {trade.get("strategy_label")} {trade.get("side")} close '
                        f'{float(trade.get("executed_volume") or 0.0):.2f} @ {float(trade.get("close_price") or 0.0):.5f}'
                    ).strip(),
                    'size': 1,
                    'strategy_id': trade.get('strategy_id'),
                    'strategy_label': trade.get('strategy_label'),
                    'portfolio_id': trade.get('portfolio_id'),
                    'portfolio_label': trade.get('portfolio_label'),
                    'pipeline_id': trade.get('pipeline_id'),
                    'pipeline_label': trade.get('pipeline_label'),
                    'symbol': trade.get('symbol'),
                    'timeframe': trade.get('timeframe'),
                    'volume_mode': trade.get('volume_mode'),
                })
            return markers

        markers = []
        for group_index, group_run in enumerate(self.group_runs):
            market_label = str(group_run.get('market_label') or '').strip()
            for marker in list(group_run.get('trade_markers') or []):
                safe_marker = dict(marker or {})
                marker_id = str(safe_marker.get('id') or f'marker-{group_index}').strip() or f'marker-{group_index}'
                marker_text = str(safe_marker.get('text') or '').strip()
                markers.append({
                    **safe_marker,
                    'id': f'{group_index}:{marker_id}',
                    'text': f'[{market_label}] {marker_text}'.strip() if market_label else marker_text,
                })
        return markers

    def _build_portfolio_strategy_stats(self):
        if self._supports_capital_replay():
            return self._build_replayed_strategy_stats()

        aggregated = {}

        for group_run in self.group_runs:
            market_symbol = str(group_run.get('symbol') or '').strip().upper()
            market_timeframe = str(group_run.get('timeframe') or '').strip().upper()
            backtester = group_run.get('backtester')
            stats = dict(getattr(backtester, 'stats', {}) or {})
            group_entries = list(group_run.get('entries') or [])
            entry_scope_map = {
                str(entry.get('strategy_id') or '').strip(): _build_scope_metadata(entry)
                for entry in group_entries
                if str(entry.get('strategy_id') or '').strip()
            }
            portfolio_items = list(stats.get('portfolio_strategy_stats') or [])

            if not portfolio_items and group_entries:
                primary_entry = dict(group_entries[0] or {})
                portfolio_items = [{
                    'strategy_id': str(primary_entry.get('strategy_id') or '').strip(),
                    'strategy_label': str(primary_entry.get('strategy_label') or '').strip() or str(primary_entry.get('strategy_id') or 'Strategy'),
                    'trades': int(stats.get('n_trades') or 0),
                    'net_pnl': float(stats.get('net_pnl') or 0.0),
                    'gross_pnl': float(stats.get('gross_pnl') or 0.0),
                    'cost': float(stats.get('total_cost') or 0.0),
                    'wins': int(stats.get('n_net_profits') or 0),
                    'losses': int(stats.get('n_net_losses') or 0),
                }]

            for item in portfolio_items:
                strategy_id = str(item.get('strategy_id') or '').strip()
                if not strategy_id:
                    continue
                current = aggregated.setdefault(strategy_id, {
                    'strategy_id': strategy_id,
                    'strategy_label': str(item.get('strategy_label') or strategy_id).strip() or strategy_id,
                    **dict(entry_scope_map.get(strategy_id) or {}),
                    'symbol': market_symbol,
                    'timeframe': market_timeframe,
                    'trades': 0,
                    'net_pnl': 0.0,
                    'gross_pnl': 0.0,
                    'cost': 0.0,
                    'wins': 0,
                    'losses': 0,
                })
                current['trades'] += int(item.get('trades') or 0)
                current['net_pnl'] += float(item.get('net_pnl') or 0.0)
                current['gross_pnl'] += float(item.get('gross_pnl') or 0.0)
                current['cost'] += float(item.get('cost') or 0.0)
                current['wins'] += int(item.get('wins') or 0)
                current['losses'] += int(item.get('losses') or 0)

        rows = []
        for item in aggregated.values():
            rows.append({
                **item,
                'win_rate': (float(item['wins']) / float(item['trades'])) if item['trades'] else 0.0,
            })
        rows.sort(key=lambda item: (str(item.get('symbol') or ''), str(item.get('timeframe') or ''), str(item.get('strategy_id') or '')))
        return rows

    def _extract_strategy_position_snapshots(self):
        snapshots = {}

        for group_run in self.group_runs:
            market_label = str(group_run.get('market_label') or '').strip()
            backtester = group_run.get('backtester')
            execution = getattr(backtester, 'execution', None)
            if execution is None:
                continue

            strategy_states = list(getattr(execution, 'strategy_states', []) or [])
            if strategy_states:
                for state in strategy_states:
                    history_source = (state.metadata or {}).get('history')
                    history = history_source.copy() if isinstance(history_source, pd.DataFrame) else pd.DataFrame(history_source or {})
                    if history.empty or 'time' not in history or 'position' not in history:
                        continue
                    times = []
                    positions = []
                    for _, row in history[['time', 'position']].iterrows():
                        time_value = row.get('time')
                        if pd.isna(time_value):
                            continue
                        position_value = row.get('position')
                        times.append(int(time_value))
                        positions.append(int(position_value) if pd.notna(position_value) else 0)
                    snapshots[str(state.strategy_id)] = {
                        'strategy_id': str(state.strategy_id),
                        'market_label': market_label,
                        'times': times,
                        'positions': positions,
                    }
                continue

            history_source = getattr(execution, 'history', None)
            history = history_source.copy() if isinstance(history_source, pd.DataFrame) else pd.DataFrame(history_source or {})
            group_entries = list(group_run.get('entries') or [])
            if history.empty or 'time' not in history or 'position' not in history or not group_entries:
                continue
            primary_entry = dict(group_entries[0] or {})
            strategy_id = str(primary_entry.get('strategy_id') or '').strip()
            if not strategy_id:
                continue
            times = []
            positions = []
            for _, row in history[['time', 'position']].iterrows():
                time_value = row.get('time')
                if pd.isna(time_value):
                    continue
                position_value = row.get('position')
                times.append(int(time_value))
                positions.append(int(position_value) if pd.notna(position_value) else 0)
            snapshots[strategy_id] = {
                'strategy_id': strategy_id,
                'market_label': market_label,
                'times': times,
                'positions': positions,
            }

        return snapshots

    def _build_portfolio_analytics(self):
        snapshots = self._extract_strategy_position_snapshots()
        if not snapshots:
            return {
                'simultaneous_position_rate': 0.0,
                'same_direction_overlap_rate': 0.0,
                'opposite_direction_overlap_rate': 0.0,
                'max_concurrent_strategies': 0,
                'pairwise': [],
            }

        union_times = sorted({
            time_value
            for snapshot in snapshots.values()
            for time_value in snapshot.get('times', [])
        })
        if not union_times:
            return {
                'simultaneous_position_rate': 0.0,
                'same_direction_overlap_rate': 0.0,
                'opposite_direction_overlap_rate': 0.0,
                'max_concurrent_strategies': 0,
                'pairwise': [],
            }

        pointer_by_strategy = {strategy_id: -1 for strategy_id in snapshots}
        current_position_by_strategy = {strategy_id: 0 for strategy_id in snapshots}
        simultaneous_points = 0
        same_direction_points = 0
        opposite_direction_points = 0
        max_concurrent_strategies = 0

        for time_value in union_times:
            active_positions = []
            for strategy_id, snapshot in snapshots.items():
                times = list(snapshot.get('times') or [])
                positions = list(snapshot.get('positions') or [])
                pointer = pointer_by_strategy[strategy_id]
                while pointer + 1 < len(times) and int(times[pointer + 1]) <= int(time_value):
                    pointer += 1
                    current_position_by_strategy[strategy_id] = int(positions[pointer] or 0)
                pointer_by_strategy[strategy_id] = pointer
                position = int(current_position_by_strategy[strategy_id] or 0)
                if position != 0:
                    active_positions.append(position)

            concurrent_count = len(active_positions)
            if concurrent_count > max_concurrent_strategies:
                max_concurrent_strategies = concurrent_count
            if concurrent_count >= 2:
                simultaneous_points += 1
                if len(set(active_positions)) == 1:
                    same_direction_points += 1
                else:
                    opposite_direction_points += 1

        pairwise = []
        strategy_ids = sorted(snapshots)
        for left_index, left_id in enumerate(strategy_ids):
            for right_id in strategy_ids[left_index + 1:]:
                left_snapshot = snapshots[left_id]
                right_snapshot = snapshots[right_id]
                pair_times = sorted(set(left_snapshot.get('times') or []) | set(right_snapshot.get('times') or []))
                left_pointer = -1
                right_pointer = -1
                left_position = 0
                right_position = 0
                overlap_points = 0
                same_direction_points_pair = 0
                opposite_direction_points_pair = 0

                for time_value in pair_times:
                    left_times = list(left_snapshot.get('times') or [])
                    left_positions = list(left_snapshot.get('positions') or [])
                    right_times = list(right_snapshot.get('times') or [])
                    right_positions = list(right_snapshot.get('positions') or [])
                    while left_pointer + 1 < len(left_times) and int(left_times[left_pointer + 1]) <= int(time_value):
                        left_pointer += 1
                        left_position = int(left_positions[left_pointer] or 0)
                    while right_pointer + 1 < len(right_times) and int(right_times[right_pointer + 1]) <= int(time_value):
                        right_pointer += 1
                        right_position = int(right_positions[right_pointer] or 0)
                    if left_position != 0 and right_position != 0:
                        overlap_points += 1
                        if left_position == right_position:
                            same_direction_points_pair += 1
                        else:
                            opposite_direction_points_pair += 1

                total_points = len(pair_times)
                pairwise.append({
                    'left_strategy_id': left_id,
                    'right_strategy_id': right_id,
                    'correlation': None,
                    'overlap_bars': int(overlap_points),
                    'same_direction_overlap_rate': (float(same_direction_points_pair) / float(total_points)) if total_points > 0 else 0.0,
                    'opposite_direction_overlap_rate': (float(opposite_direction_points_pair) / float(total_points)) if total_points > 0 else 0.0,
                    'left_market_label': left_snapshot.get('market_label'),
                    'right_market_label': right_snapshot.get('market_label'),
                })

        total_points = len(union_times)
        return {
            'simultaneous_position_rate': (float(simultaneous_points) / float(total_points)) if total_points > 0 else 0.0,
            'same_direction_overlap_rate': (float(same_direction_points) / float(total_points)) if total_points > 0 else 0.0,
            'opposite_direction_overlap_rate': (float(opposite_direction_points) / float(total_points)) if total_points > 0 else 0.0,
            'max_concurrent_strategies': int(max_concurrent_strategies),
            'pairwise': pairwise,
        }

    def _build_event_counts(self):
        if self._supports_capital_replay():
            conflict_count = 0
            existing_position_count = 0
            for group_run in self.group_runs:
                backtester = group_run.get('backtester')
                for event in list(getattr(getattr(backtester, 'execution', None), 'events', []) or []):
                    if event.kind != 'skip_open':
                        continue
                    reason = str((event.metadata or {}).get('reason') or '').strip()
                    if reason == 'conflict_with_portfolio_direction':
                        conflict_count += 1
                    elif reason == 'existing_position':
                        existing_position_count += 1
            return {
                'open': int(len(self.replayed_trades)),
                'close': int(sum(1 for trade in self.replayed_trades if trade.get('exit_kind') == 'close')),
                'stop': int(sum(1 for trade in self.replayed_trades if trade.get('exit_kind') == 'stop')),
                'skip_open': int(conflict_count + existing_position_count + len(self.sizing_skips)),
                'skip_open_conflict': int(conflict_count),
                'skip_open_existing_position': int(existing_position_count),
                'skip_open_sizing': int(len(self.sizing_skips)),
            }

        counts = {
            'open': 0,
            'close': 0,
            'stop': 0,
            'skip_open': 0,
            'skip_open_conflict': 0,
            'skip_open_existing_position': 0,
        }

        for group_run in self.group_runs:
            backtester = group_run.get('backtester')
            stats = dict(getattr(backtester, 'stats', {}) or {})
            group_counts = dict(stats.get('portfolio_event_counts') or {})
            if group_counts:
                for key in counts:
                    counts[key] += int(group_counts.get(key) or 0)
                continue

            events = list(getattr(getattr(backtester, 'execution', None), 'events', []) or [])
            for event in events:
                if event.kind in counts:
                    counts[event.kind] += 1

        return counts

    def _build_aggregate_trade_stats(self):
        if self._supports_capital_replay():
            aggregate = {
                'gross_profit': 0.0,
                'gross_loss': 0.0,
                'gross_pnl': 0.0,
                'net_profit': 0.0,
                'net_loss': 0.0,
                'net_pnl': 0.0,
                'n_gross_profits': 0,
                'n_gross_losses': 0,
                'n_net_profits': 0,
                'n_net_losses': 0,
                'n_trades': 0,
                'profitable_trades_cost': 0.0,
                'unprofitable_trades_cost': 0.0,
                'total_cost': 0.0,
                'total_operational_cost': 0.0,
                'total_estimated_tax': 0.0,
            }
            cost_breakdown_totals = []
            operational_cost_breakdown_totals = []
            estimated_tax_breakdown_totals = []

            for trade in self.replayed_trades:
                gross_pnl = float(trade.get('gross_pnl') or 0.0)
                net_pnl = float(trade.get('net_pnl') or 0.0)
                trade_cost = float(trade.get('trade_cost') or 0.0)
                trade_cost_breakdown = list(trade.get('trade_cost_breakdown') or [])
                trade_cost_partition = partition_cost_breakdown_items(trade_cost_breakdown)
                trade_estimated_tax = sum_cost_breakdown_amount(trade_cost_partition['estimated_tax'])
                trade_operational_cost = float(trade_cost) - float(trade_estimated_tax)
                aggregate['gross_pnl'] += gross_pnl
                aggregate['net_pnl'] += net_pnl
                aggregate['total_cost'] += trade_cost
                aggregate['total_operational_cost'] += trade_operational_cost
                aggregate['total_estimated_tax'] += trade_estimated_tax
                aggregate['n_trades'] += 1
                cost_breakdown_totals = merge_cost_breakdown_items(cost_breakdown_totals, trade_cost_breakdown)
                operational_cost_breakdown_totals = merge_cost_breakdown_items(
                    operational_cost_breakdown_totals,
                    trade_cost_partition['operational'],
                )
                estimated_tax_breakdown_totals = merge_cost_breakdown_items(
                    estimated_tax_breakdown_totals,
                    trade_cost_partition['estimated_tax'],
                )
                if gross_pnl >= 0:
                    aggregate['gross_profit'] += gross_pnl
                    aggregate['n_gross_profits'] += 1
                else:
                    aggregate['gross_loss'] += gross_pnl
                    aggregate['n_gross_losses'] += 1
                if net_pnl >= 0:
                    aggregate['net_profit'] += net_pnl
                    aggregate['n_net_profits'] += 1
                    aggregate['profitable_trades_cost'] += trade_cost
                else:
                    aggregate['net_loss'] += net_pnl
                    aggregate['n_net_losses'] += 1
                    aggregate['unprofitable_trades_cost'] += trade_cost

            n_gross_profits = int(aggregate['n_gross_profits'])
            n_gross_losses = int(aggregate['n_gross_losses'])
            n_net_profits = int(aggregate['n_net_profits'])
            n_net_losses = int(aggregate['n_net_losses'])
            n_trades = int(aggregate['n_trades'])
            gross_profit = float(aggregate['gross_profit'])
            gross_loss = float(aggregate['gross_loss'])
            net_profit = float(aggregate['net_profit'])
            net_loss = float(aggregate['net_loss'])
            gross_pnl = float(aggregate['gross_pnl'])
            net_pnl = float(aggregate['net_pnl'])
            total_cost = float(aggregate['total_cost'])

            aggregate.update({
                'avg_gross_profit': (gross_profit / n_gross_profits) if n_gross_profits > 0 else 0.0,
                'avg_gross_loss': (gross_loss / n_gross_losses) if n_gross_losses > 0 else 0.0,
                'avg_net_profit': (net_profit / n_net_profits) if n_net_profits > 0 else 0.0,
                'avg_net_loss': (net_loss / n_net_losses) if n_net_losses > 0 else 0.0,
                'win_rate': (float(n_net_profits) / float(n_trades)) if n_trades > 0 else 0.0,
                'loss_rate': (float(n_net_losses) / float(n_trades)) if n_trades > 0 else 0.0,
                'gross_profit_factor': abs(gross_profit / gross_loss) if gross_loss != 0 else 0.0,
                'net_profit_factor': abs(net_profit / net_loss) if net_loss != 0 else 0.0,
                'cost_factor': abs(total_cost / gross_pnl) if gross_pnl != 0 else 0.0,
                'risk_reward_ratio': abs((net_profit / n_net_profits) / (net_loss / n_net_losses)) if n_net_profits > 0 and n_net_losses > 0 and net_loss != 0 else 0.0,
                'expectancy_per_trade': (net_pnl / n_trades) if n_trades > 0 else 0.0,
                'cost_breakdown_totals': cost_breakdown_totals,
                'operational_cost_breakdown_totals': operational_cost_breakdown_totals,
                'estimated_tax_breakdown_totals': estimated_tax_breakdown_totals,
            })
            return aggregate

        aggregate = {
            'gross_profit': 0.0,
            'gross_loss': 0.0,
            'gross_pnl': 0.0,
            'net_profit': 0.0,
            'net_loss': 0.0,
            'net_pnl': 0.0,
            'n_gross_profits': 0,
            'n_gross_losses': 0,
            'n_net_profits': 0,
            'n_net_losses': 0,
            'n_trades': 0,
            'profitable_trades_cost': 0.0,
            'unprofitable_trades_cost': 0.0,
            'total_cost': 0.0,
            'total_operational_cost': 0.0,
            'total_estimated_tax': 0.0,
        }
        cost_breakdown_totals = []
        operational_cost_breakdown_totals = []
        estimated_tax_breakdown_totals = []

        for group_run in self.group_runs:
            stats = dict(getattr(group_run.get('backtester'), 'stats', {}) or {})
            for key in aggregate:
                value = stats.get(key)
                if isinstance(aggregate[key], int):
                    aggregate[key] += int(value or 0)
                else:
                    aggregate[key] += float(value or 0.0)
            cost_breakdown_totals = merge_cost_breakdown_items(
                cost_breakdown_totals,
                stats.get('cost_breakdown_totals'),
            )
            operational_cost_breakdown_totals = merge_cost_breakdown_items(
                operational_cost_breakdown_totals,
                stats.get('operational_cost_breakdown_totals'),
            )
            estimated_tax_breakdown_totals = merge_cost_breakdown_items(
                estimated_tax_breakdown_totals,
                stats.get('estimated_tax_breakdown_totals'),
            )

        n_gross_profits = int(aggregate['n_gross_profits'])
        n_gross_losses = int(aggregate['n_gross_losses'])
        n_net_profits = int(aggregate['n_net_profits'])
        n_net_losses = int(aggregate['n_net_losses'])
        n_trades = int(aggregate['n_trades'])
        gross_profit = float(aggregate['gross_profit'])
        gross_loss = float(aggregate['gross_loss'])
        net_profit = float(aggregate['net_profit'])
        net_loss = float(aggregate['net_loss'])
        gross_pnl = float(aggregate['gross_pnl'])
        net_pnl = float(aggregate['net_pnl'])
        total_cost = float(aggregate['total_cost'])

        aggregate.update({
            'avg_gross_profit': (gross_profit / n_gross_profits) if n_gross_profits > 0 else 0.0,
            'avg_gross_loss': (gross_loss / n_gross_losses) if n_gross_losses > 0 else 0.0,
            'avg_net_profit': (net_profit / n_net_profits) if n_net_profits > 0 else 0.0,
            'avg_net_loss': (net_loss / n_net_losses) if n_net_losses > 0 else 0.0,
            'win_rate': (float(n_net_profits) / float(n_trades)) if n_trades > 0 else 0.0,
            'loss_rate': (float(n_net_losses) / float(n_trades)) if n_trades > 0 else 0.0,
            'gross_profit_factor': abs(gross_profit / gross_loss) if gross_loss != 0 else 0.0,
            'net_profit_factor': abs(net_profit / net_loss) if net_loss != 0 else 0.0,
            'cost_factor': abs(total_cost / gross_pnl) if gross_pnl != 0 else 0.0,
            'risk_reward_ratio': abs((net_profit / n_net_profits) / (net_loss / n_net_losses)) if n_net_profits > 0 and n_net_losses > 0 and net_loss != 0 else 0.0,
            'expectancy_per_trade': (net_pnl / n_trades) if n_trades > 0 else 0.0,
            'cost_breakdown_totals': cost_breakdown_totals,
            'operational_cost_breakdown_totals': operational_cost_breakdown_totals,
            'estimated_tax_breakdown_totals': estimated_tax_breakdown_totals,
        })
        return aggregate

    def run(self):
        self.execution = None
        self.results = self._build_results_frame()
        self.trade_markers = self._build_trade_markers()
        strategy_stats = self._build_portfolio_strategy_stats()
        portfolio_analytics = self._build_portfolio_analytics()
        analyzer = self._build_analyzer()
        analyzer.results = self.results.copy()
        analyzer.bankrupt = False
        analyzer.bankruptcy_index = None
        analyzer._get_stats()
        aggregate_trade_stats = self._build_aggregate_trade_stats()

        self.stats = {
            **dict(analyzer.stats or {}),
            **aggregate_trade_stats,
            'strategy_count': int(len(strategy_stats)),
            'portfolio_event_counts': self._build_event_counts(),
            'portfolio_active_strategy_ids': [item['strategy_id'] for item in strategy_stats],
            'portfolio_strategy_stats': strategy_stats,
            'portfolio_analytics': portfolio_analytics,
            'portfolio_market_groups': [
                {
                    'symbol': group.get('symbol'),
                    'timeframe': group.get('timeframe'),
                    'market_label': group.get('market_label'),
                    'strategy_count': len(list(group.get('entries') or [])),
                }
                for group in self.group_runs
            ],
        }
        if self._supports_capital_replay():
            self.ledger = list(self.replayed_trades)
        else:
            self.ledger = []
            for group_run in self.group_runs:
                backtester = group_run.get('backtester')
                for row in list(getattr(backtester, 'ledger', []) or []):
                    self.ledger.append(dict(row or {}))
        self.scope_tree = build_scope_tree_from_strategy_stats(strategy_stats)
        self.rollups = build_scope_rollups_from_strategy_stats(strategy_stats)
        self.stats['scope_tree'] = self.scope_tree
        self.stats['rollups'] = self.rollups
        self.stats['ledger'] = list(self.ledger)
        return self.results
