import pandas as pd
import numpy as np

from .backtest_cost_profiles import (
    build_trade_cost_breakdown,
    merge_cost_breakdown_items,
    partition_cost_breakdown_items,
    resolve_backtest_asset_type,
    resolve_broker_cost_context,
    resolve_effective_backtest_cost_profile,
    sum_cost_breakdown_amount,
)


class BacktestPerformanceAnalyzer:
    ENTRY_MARKER_COLOR = '#f3f4f6'
    PROFIT_EXIT_MARKER_COLOR = '#22c55e'
    LOSS_EXIT_MARKER_COLOR = '#ef4444'
    REGIME_LABELS = {
        -3: 'volatile_down',
        -2: 'trend_down',
        0: 'range',
        1: 'compression',
        2: 'trend_up',
        3: 'volatile_up',
    }
    STABILITY_BUCKETS = (
        (0.0, 0.35, 'fragile'),
        (0.35, 0.65, 'building'),
        (0.65, 1.01, 'mature'),
    )

    DERIVED_RESULT_COLUMNS = [
        'trade_cost',
        'trade_cost_breakdown',
        'trade_gross_pnl',
        'trade_net_pnl',
        'account_balance_delta',
        'account_balance',
        'short_entry_flag',
        'short_exit_flag',
        'long_entry_flag',
        'long_exit_flag',
    ]

    def __init__(
        self,
        results,
        initial_balance=10000,
        asset_type='forex',
        initial_volume=1.0,
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        spread_in_pips=1.0,
        cost_profile='oanda',
        broker_cost_context=None,
    ):
        self.results = results.copy()
        self.initial_balance = initial_balance
        self.current_balance = self.initial_balance
        self.asset_type = asset_type
        self.initial_volume = initial_volume
        self.volume = self.initial_volume
        self.pip_size = pip_size
        self.pip_value_per_lot = pip_value_per_lot
        self.spread_in_pips = spread_in_pips
        self.cost_profile = resolve_effective_backtest_cost_profile(
            cost_profile,
            broker_code=(broker_cost_context or {}).get('broker_code', ''),
            market_domain=(broker_cost_context or {}).get('market_domain', ''),
        )
        self.requested_cost_profile = str(cost_profile or '').strip().lower() or self.cost_profile
        self.broker_cost_context = resolve_broker_cost_context(broker_cost_context)
        self.asset_type = resolve_backtest_asset_type(
            asset_type,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
            cost_profile=self.requested_cost_profile,
        )
        self.trade_markers = []
        self.stats = {}
        self.execution_policy = None

    def run(self):
        if self.results is None or len(self.results) == 0:
            self.results = pd.DataFrame(columns=['time'])

        self._ensure_result_columns()
        self._apply_trade_results()
        self._get_trade_flags()
        self.trade_markers = self._build_trade_markers()
        self._get_stats()
        return self.results

    def run_from(self, start_index=0, previous_results=None):
        if self.results is None or len(self.results) == 0:
            self.results = pd.DataFrame(columns=['time'])

        self._ensure_result_columns()
        if previous_results is not None:
            self._seed_previous_results(start_index, previous_results)

        self._apply_trade_results(start_index=start_index)
        self._get_trade_flags()
        self.trade_markers = self._build_trade_markers()
        self._get_stats()
        return self.results

    def _ensure_result_columns(self):
        row_count = len(self.results)
        object_defaults = {
            'trade_cost_breakdown': [[] for _ in range(row_count)],
        }
        scalar_defaults = {
            'trade_cost': 0.0,
            'trade_gross_pnl': 0.0,
            'trade_net_pnl': 0.0,
            'account_balance_delta': 0.0,
            'account_balance': float(self.initial_balance),
            'short_entry_flag': 0,
            'short_exit_flag': 0,
            'long_entry_flag': 0,
            'long_exit_flag': 0,
        }

        for column_name, default_values in object_defaults.items():
            if column_name not in self.results.columns:
                self.results[column_name] = pd.Series(default_values, index=self.results.index, dtype=object)

        for column_name, default_value in scalar_defaults.items():
            if column_name not in self.results.columns:
                self.results[column_name] = default_value

    def pip_value(self, volume=None):
        if volume is None:
            volume = self.volume

        formulas = {
            'forex': lambda current_volume: self.pip_value_per_lot * current_volume,
            'b3_mini_future': lambda current_volume: self.pip_value_per_lot * current_volume,
        }

        return formulas.get(self.asset_type, lambda current_volume: 0.0)(volume)

    def cost(self, volume=None):
        if volume is None:
            volume = self.volume
        breakdown = self.trade_cost_breakdown(
            volume=volume,
            open_price=1.0 if self.asset_type == 'forex' else None,
            close_price=1.0 if self.asset_type == 'forex' else None,
        )
        return float(sum(float(item.get('amount') or 0.0) for item in breakdown))

    def gross_result(self, order_side, open_price, close_price, volume=None):
        if volume is None:
            volume = self.volume

        position = {
            'long': 1,
            'short': -1,
        }[order_side]

        if self.asset_type in {'forex', 'b3_mini_future'}:
            return (
                (((close_price - open_price) * position) / self.pip_size)
                * self.pip_value(volume)
            )
        if self.asset_type in {'b3_equity', 'b3_option', 'b3_term'}:
            return ((close_price - open_price) * position) * float(volume)
        raise KeyError(f'Unsupported asset type: {self.asset_type}')

    def trade_cost_breakdown(
        self,
        *,
        volume=None,
        open_price=None,
        close_price=None,
        open_time=None,
        close_time=None,
        gross_pnl=None,
    ):
        if volume is None:
            volume = self.volume
        return build_trade_cost_breakdown(
            cost_profile=self.requested_cost_profile,
            asset_type=self.asset_type,
            broker_code=self.broker_cost_context.get('broker_code', ''),
            market_domain=self.broker_cost_context.get('market_domain', ''),
            volume=volume,
            pip_value=self.pip_value(volume),
            spread_in_pips=self.spread_in_pips,
            open_price=open_price,
            close_price=close_price,
            open_time=open_time,
            close_time=close_time,
            gross_pnl=gross_pnl,
        )

    def net_result(self, order_side, open_price, close_price, volume=None):
        gross_pnl = self.gross_result(order_side, open_price, close_price, volume)
        breakdown = self.trade_cost_breakdown(
            volume=volume,
            open_price=open_price,
            close_price=close_price,
            gross_pnl=gross_pnl,
        )
        return gross_pnl - float(sum(float(item.get('amount') or 0.0) for item in breakdown))

    def _seed_previous_results(self, start_index, previous_results):
        if start_index <= 0:
            return

        previous_df = previous_results.copy() if hasattr(previous_results, 'copy') else pd.DataFrame(previous_results)
        prefix_length = min(start_index, len(previous_df), len(self.results))

        if prefix_length <= 0:
            return

        for column_name in self.DERIVED_RESULT_COLUMNS:
            if column_name in previous_df.columns:
                if column_name == 'trade_cost_breakdown':
                    for row_index in range(prefix_length):
                        value = previous_df.iloc[row_index][column_name]
                        copied = list(value) if isinstance(value, list) else []
                        self.results.at[row_index, column_name] = copied
                else:
                    self.results.loc[:prefix_length - 1, column_name] = previous_df.loc[:prefix_length - 1, column_name].to_list()

    def _apply_trade_results(self, start_index=0):
        current_balance = self.initial_balance
        bankrupt = False
        bankruptcy_index = None

        if start_index > 0 and 'account_balance' in self.results.columns and len(self.results) >= start_index:
            previous_balance = self.results.loc[start_index - 1, 'account_balance']
            if pd.notna(previous_balance):
                current_balance = float(previous_balance)
                if current_balance <= 0:
                    current_balance = 0.0
                    bankrupt = True
                    bankruptcy_index = start_index - 1

        computed_rows = []

        for index in range(start_index, len(self.results)):
            if bankrupt:
                computed_rows.append({
                    'index': index,
                    'trade_cost': 0.0,
                    'trade_cost_breakdown': [],
                    'trade_gross_pnl': 0.0,
                    'trade_net_pnl': 0.0,
                    'account_balance_delta': 0.0,
                    'account_balance': 0.0,
                })
                continue

            line = self.results.iloc[index]
            total_cost = 0.0
            total_cost_breakdown = []
            total_gross_result = 0.0
            total_net_result = 0.0
            trade_executed = False

            if pd.notna(line.long_close_price):
                long_gross_result = self.gross_result(
                    'long',
                    line.long_open_price,
                    line.long_close_price,
                )
                long_cost_breakdown = self.trade_cost_breakdown(
                    open_price=line.long_open_price,
                    close_price=line.long_close_price,
                    open_time=line.long_open_timestamp,
                    close_time=line.long_close_timestamp,
                    gross_pnl=long_gross_result,
                )
                long_cost = float(sum(float(item.get('amount') or 0.0) for item in long_cost_breakdown))
                long_net_result = long_gross_result - long_cost

                total_cost += long_cost
                total_cost_breakdown = merge_cost_breakdown_items(total_cost_breakdown, long_cost_breakdown)
                total_gross_result += long_gross_result
                total_net_result += long_net_result
                trade_executed = True

            if pd.notna(line.short_close_price):
                short_gross_result = self.gross_result(
                    'short',
                    line.short_open_price,
                    line.short_close_price,
                )
                short_cost_breakdown = self.trade_cost_breakdown(
                    open_price=line.short_open_price,
                    close_price=line.short_close_price,
                    open_time=line.short_open_timestamp,
                    close_time=line.short_close_timestamp,
                    gross_pnl=short_gross_result,
                )
                short_cost = float(sum(float(item.get('amount') or 0.0) for item in short_cost_breakdown))
                short_net_result = short_gross_result - short_cost

                total_cost += short_cost
                total_cost_breakdown = merge_cost_breakdown_items(total_cost_breakdown, short_cost_breakdown)
                total_gross_result += short_gross_result
                total_net_result += short_net_result
                trade_executed = True

            if not trade_executed:
                total_cost = 0.0
                total_cost_breakdown = []
                total_gross_result = 0.0
                total_net_result = 0.0

            if current_balance + total_net_result <= 0:
                total_net_result = -float(current_balance)
                total_gross_result = total_net_result + total_cost
                current_balance = 0.0
                bankrupt = True
                bankruptcy_index = index
            else:
                current_balance += total_net_result

            computed_rows.append({
                'index': index,
                'trade_cost': total_cost,
                'trade_cost_breakdown': total_cost_breakdown,
                'trade_gross_pnl': total_gross_result,
                'trade_net_pnl': total_net_result,
                'account_balance_delta': total_net_result,
                'account_balance': current_balance,
            })

        for row in computed_rows:
            row_index = row.pop('index')
            for column_name, value in row.items():
                self.results.at[row_index, column_name] = value

        self.bankrupt = bankrupt
        self.bankruptcy_index = bankruptcy_index

    def _get_drawdown_stats(self):
        if len(self.results) == 0:
            return {
                'equity_curve': [],
                'rolling_peak': [],
                'drawdown_curve': [],
                'drawdown_pct_curve': [],
                'max_drawdown': 0.0,
                'max_drawdown_pct': 0.0,
                'max_drawdown_start_idx': 0,
                'max_drawdown_end_idx': 0,
                'drawdown_recovery_idx': None,
                'drawdown_duration_bars': None,
            }

        equity_curve = self.results['account_balance'].copy()
        rolling_peak = equity_curve.cummax()
        drawdown = equity_curve - rolling_peak
        drawdown_pct = drawdown / rolling_peak.replace(0, pd.NA)

        max_drawdown = drawdown.min()
        max_drawdown_pct = drawdown_pct.min()
        max_drawdown_end_idx = drawdown.idxmin()

        if pd.notna(max_drawdown_end_idx):
            peak_value_at_max_dd = rolling_peak.loc[max_drawdown_end_idx]
            peak_candidates = equity_curve.loc[:max_drawdown_end_idx]
            peak_candidates = peak_candidates[peak_candidates == peak_value_at_max_dd]

            if len(peak_candidates) > 0:
                max_drawdown_start_idx = peak_candidates.index[0]
            else:
                max_drawdown_start_idx = 0
        else:
            max_drawdown_start_idx = 0

        recovery_idx = None

        if pd.notna(max_drawdown_end_idx):
            post_dd_curve = equity_curve.loc[max_drawdown_end_idx:]
            recovery_candidates = post_dd_curve[
                post_dd_curve >= rolling_peak.loc[max_drawdown_end_idx]
            ]

            if len(recovery_candidates) > 0:
                recovery_idx = recovery_candidates.index[0]

        if recovery_idx is not None:
            drawdown_duration_bars = recovery_idx - max_drawdown_start_idx
        else:
            drawdown_duration_bars = None

        return {
            'equity_curve': equity_curve.tolist(),
            'rolling_peak': rolling_peak.tolist(),
            'drawdown_curve': drawdown.tolist(),
            'drawdown_pct_curve': drawdown_pct.fillna(0).tolist(),
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'max_drawdown_start_idx': max_drawdown_start_idx,
            'max_drawdown_end_idx': max_drawdown_end_idx,
            'drawdown_recovery_idx': recovery_idx,
            'drawdown_duration_bars': drawdown_duration_bars,
        }

    def _get_risk_adjusted_ratios(self):
        if len(self.results) == 0:
            return {
                'avg_return': 0.0,
                'return_std': 0.0,
                'downside_std': 0.0,
                'sharpe_ratio': 0.0,
                'sortino_ratio': 0.0,
            }

        returns = self.results['trade_net_pnl'] / self.results['account_balance'].shift(1)
        returns = returns.replace([pd.NA, np.inf, -np.inf], pd.NA).dropna()

        if len(returns) == 0:
            return {
                'avg_return': 0.0,
                'return_std': 0.0,
                'downside_std': 0.0,
                'sharpe_ratio': 0.0,
                'sortino_ratio': 0.0,
            }

        avg_return = returns.mean()
        return_std = returns.std(ddof=0)

        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std(ddof=0) if len(downside_returns) > 0 else 0.0

        sharpe_ratio = (
            avg_return / return_std
            if return_std not in [0, None] and pd.notna(return_std)
            else 0.0
        )

        sortino_ratio = (
            avg_return / downside_std
            if downside_std not in [0, None] and pd.notna(downside_std)
            else 0.0
        )

        return {
            'avg_return': avg_return,
            'return_std': return_std,
            'downside_std': downside_std,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
        }

    def _get_regime_summary(self):
        if self.results is None or len(self.results) == 0:
            return None

        regime_columns = [
            column_name
            for column_name in self.results.columns
            if str(column_name).endswith('_regime_code')
        ]

        if not regime_columns:
            return None

        trade_rows = self._get_executed_trade_rows()
        if trade_rows.empty:
            return None

        summaries = []

        for regime_column in regime_columns:
            scoped_rows = trade_rows.loc[trade_rows[regime_column].notna()].copy()
            if scoped_rows.empty:
                continue

            grouped = scoped_rows.groupby(regime_column, dropna=True)
            column_summaries = []

            for regime_code, group in grouped:
                trade_count = int(len(group))
                net_pnl = float(group['trade_net_pnl'].sum())
                gross_pnl = float(group['trade_gross_pnl'].sum())
                avg_trade = float(group['trade_net_pnl'].mean()) if trade_count > 0 else 0.0
                win_rate = float((group['trade_net_pnl'] > 0).sum() / trade_count) if trade_count > 0 else 0.0

                try:
                    normalized_code = int(float(regime_code))
                except Exception:
                    normalized_code = regime_code

                column_summaries.append({
                    'regime_code': normalized_code,
                    'regime_label': self.REGIME_LABELS.get(normalized_code, str(normalized_code)),
                    'trade_count': trade_count,
                    'win_rate': win_rate,
                    'net_pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'avg_trade_net_pnl': avg_trade,
                })

            column_summaries.sort(key=lambda item: item['trade_count'], reverse=True)
            summaries.append({
                'column': regime_column,
                'rows': column_summaries,
            })

        if not summaries:
            return None

        return summaries

    def _get_regime_stability_summary(self):
        if self.results is None or len(self.results) == 0:
            return None

        stability_columns = [
            column_name
            for column_name in self.results.columns
            if str(column_name).endswith('_stability_score')
        ]

        if not stability_columns:
            return None

        trade_rows = self._get_executed_trade_rows()
        if trade_rows.empty:
            return None

        summaries = []

        for stability_column in stability_columns:
            scoped_rows = trade_rows.loc[trade_rows[stability_column].notna()].copy()
            if scoped_rows.empty:
                continue

            bucket_rows = []

            for lower, upper, label in self.STABILITY_BUCKETS:
                if upper >= 1.0:
                    bucket = scoped_rows.loc[
                        (scoped_rows[stability_column] >= lower)
                        & (scoped_rows[stability_column] <= upper)
                    ]
                else:
                    bucket = scoped_rows.loc[
                        (scoped_rows[stability_column] >= lower)
                        & (scoped_rows[stability_column] < upper)
                    ]

                if bucket.empty:
                    continue

                trade_count = int(len(bucket))
                net_pnl = float(bucket['trade_net_pnl'].sum())
                avg_trade = float(bucket['trade_net_pnl'].mean()) if trade_count > 0 else 0.0
                win_rate = float((bucket['trade_net_pnl'] > 0).sum() / trade_count) if trade_count > 0 else 0.0
                avg_stability = float(bucket[stability_column].mean()) if trade_count > 0 else 0.0

                bucket_rows.append({
                    'bucket_label': label,
                    'bucket_range': f'{lower:.2f} to {min(1.0, upper):.2f}',
                    'trade_count': trade_count,
                    'win_rate': win_rate,
                    'net_pnl': net_pnl,
                    'avg_trade_net_pnl': avg_trade,
                    'avg_stability_score': avg_stability,
                })

            if bucket_rows:
                bucket_rows.sort(key=lambda item: item['trade_count'], reverse=True)
                summaries.append({
                    'column': stability_column,
                    'rows': bucket_rows,
                })

        if not summaries:
            return None

        return summaries

    def _get_executed_trade_rows(self):
        if self.results is None or len(self.results) == 0:
            return pd.DataFrame(columns=getattr(self.results, 'columns', []))

        cutoff_index = getattr(self, 'bankruptcy_index', None)
        valid_until = self.results.index <= cutoff_index if cutoff_index is not None else pd.Series(True, index=self.results.index)

        # Some portfolio backtests already materialize aggregate trade_* columns
        # without per-leg close timestamps. In that case, use the aggregate trade
        # columns as the executed-trade source instead of requiring timestamp flags.
        if (
            'trade_net_pnl' in self.results.columns
            and 'trade_cost' in self.results.columns
            and 'trade_gross_pnl' in self.results.columns
            and 'long_close_timestamp' not in self.results.columns
            and 'short_close_timestamp' not in self.results.columns
        ):
            trade_mask = (
                (pd.to_numeric(self.results.get('trade_cost', 0.0), errors='coerce').fillna(0.0) != 0)
                | (pd.to_numeric(self.results.get('trade_gross_pnl', 0.0), errors='coerce').fillna(0.0) != 0)
                | (pd.to_numeric(self.results.get('trade_net_pnl', 0.0), errors='coerce').fillna(0.0) != 0)
            )
            return self.results.loc[valid_until & trade_mask].copy()

        long_close_timestamps = pd.to_numeric(
            self.results.get('long_close_timestamp', pd.Series(0.0, index=self.results.index)),
            errors='coerce',
        ).fillna(0.0)
        short_close_timestamps = pd.to_numeric(
            self.results.get('short_close_timestamp', pd.Series(0.0, index=self.results.index)),
            errors='coerce',
        ).fillna(0.0)
        trade_mask = (
            (long_close_timestamps > 0)
            | (short_close_timestamps > 0)
        )
        return self.results.loc[valid_until & trade_mask].copy()

    def _get_stats(self):
        final_balance = self.results['account_balance'].iloc[-1] if len(self.results) > 0 else self.initial_balance
        account_balance_change = final_balance - self.initial_balance

        trade_rows = self._get_executed_trade_rows()

        gross_profit = self.results.loc[self.results['trade_gross_pnl'] > 0, 'trade_gross_pnl'].sum()
        gross_loss = self.results.loc[self.results['trade_gross_pnl'] < 0, 'trade_gross_pnl'].sum()
        gross_result = self.results['trade_gross_pnl'].sum()

        net_profit = self.results.loc[self.results['trade_net_pnl'] > 0, 'trade_net_pnl'].sum()
        net_loss = self.results.loc[self.results['trade_net_pnl'] < 0, 'trade_net_pnl'].sum()
        net_result = self.results['trade_net_pnl'].sum()

        n_gross_profits = int((trade_rows['trade_gross_pnl'] > 0).sum())
        n_gross_losses = int((trade_rows['trade_gross_pnl'] < 0).sum())
        n_net_profits = int((trade_rows['trade_net_pnl'] > 0).sum())
        n_net_losses = int((trade_rows['trade_net_pnl'] < 0).sum())
        n_trades = int(len(trade_rows))

        profitable_trades_cost = trade_rows.loc[trade_rows['trade_net_pnl'] > 0, 'trade_cost'].sum()
        unprofitable_trades_cost = trade_rows.loc[trade_rows['trade_net_pnl'] < 0, 'trade_cost'].sum()
        total_cost = trade_rows['trade_cost'].sum()
        cost_breakdown_totals = []
        if 'trade_cost_breakdown' in trade_rows.columns:
            for breakdown in trade_rows['trade_cost_breakdown'].tolist():
                cost_breakdown_totals = merge_cost_breakdown_items(cost_breakdown_totals, breakdown)
        cost_breakdown_partition = partition_cost_breakdown_items(cost_breakdown_totals)
        total_estimated_tax = sum_cost_breakdown_amount(cost_breakdown_partition['estimated_tax'])
        total_operational_cost = float(total_cost) - float(total_estimated_tax)

        avg_gross_profit = gross_profit / n_gross_profits if n_gross_profits > 0 else 0.0
        avg_gross_loss = gross_loss / n_gross_losses if n_gross_losses > 0 else 0.0
        avg_net_profit = net_profit / n_net_profits if n_net_profits > 0 else 0.0
        avg_net_loss = net_loss / n_net_losses if n_net_losses > 0 else 0.0

        win_rate = n_net_profits / n_trades if n_trades > 0 else 0.0
        loss_rate = n_net_losses / n_trades if n_trades > 0 else 0.0

        gross_profit_ratio = abs(gross_profit / gross_loss) if gross_loss != 0 else 0.0
        net_profit_ratio = abs(net_profit / net_loss) if net_loss != 0 else 0.0
        cost_ratio = abs(total_cost / gross_result) if gross_result != 0 else 0.0
        risk_reward_ratio = abs(avg_net_profit / avg_net_loss) if avg_net_loss != 0 else 0.0
        expectancy_per_trade = net_result / n_trades if n_trades > 0 else 0.0
        recovery_factor = 0.0

        drawdown_stats = self._get_drawdown_stats()

        if drawdown_stats['max_drawdown'] not in [0, None] and pd.notna(drawdown_stats['max_drawdown']):
            recovery_factor = abs(net_result / drawdown_stats['max_drawdown'])

        if risk_reward_ratio > 0 and n_trades > 0:
            kelly_fraction = win_rate - ((1 - win_rate) / risk_reward_ratio)
        else:
            kelly_fraction = 0.0

        ratio_stats = self._get_risk_adjusted_ratios()
        regime_summary = self._get_regime_summary()
        regime_stability_summary = self._get_regime_stability_summary()

        self.stats = {
            'initial_balance': self.initial_balance,
            'final_balance': final_balance,
            'account_balance_change': account_balance_change,
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'gross_pnl': gross_result,
            'net_profit': net_profit,
            'net_loss': net_loss,
            'net_pnl': net_result,
            'n_gross_profits': n_gross_profits,
            'n_gross_losses': n_gross_losses,
            'n_net_profits': n_net_profits,
            'n_net_losses': n_net_losses,
            'n_trades': n_trades,
            'profitable_trades_cost': profitable_trades_cost,
            'unprofitable_trades_cost': unprofitable_trades_cost,
            'total_cost': total_cost,
            'total_operational_cost': total_operational_cost,
            'total_estimated_tax': total_estimated_tax,
            'cost_breakdown_totals': cost_breakdown_totals,
            'operational_cost_breakdown_totals': cost_breakdown_partition['operational'],
            'estimated_tax_breakdown_totals': cost_breakdown_partition['estimated_tax'],
            'avg_gross_profit': avg_gross_profit,
            'avg_gross_loss': avg_gross_loss,
            'avg_net_profit': avg_net_profit,
            'avg_net_loss': avg_net_loss,
            'win_rate': win_rate,
            'loss_rate': loss_rate,
            'gross_profit_factor': gross_profit_ratio,
            'net_profit_factor': net_profit_ratio,
            'cost_factor': cost_ratio,
            'risk_reward_ratio': risk_reward_ratio,
            'expectancy_per_trade': expectancy_per_trade,
            'recovery_factor': recovery_factor,
            'kelly_fraction': kelly_fraction,
            'account_balance_series': drawdown_stats['equity_curve'],
            'account_balance_peak_series': drawdown_stats['rolling_peak'],
            'drawdown_amount_series': drawdown_stats['drawdown_curve'],
            'drawdown_pct_series': drawdown_stats['drawdown_pct_curve'],
            'max_drawdown': drawdown_stats['max_drawdown'],
            'max_drawdown_pct': drawdown_stats['max_drawdown_pct'],
            'max_drawdown_start_idx': drawdown_stats['max_drawdown_start_idx'],
            'max_drawdown_end_idx': drawdown_stats['max_drawdown_end_idx'],
            'drawdown_recovery_idx': drawdown_stats['drawdown_recovery_idx'],
            'drawdown_duration_bars': drawdown_stats['drawdown_duration_bars'],
            'avg_return': ratio_stats['avg_return'],
            'return_std': ratio_stats['return_std'],
            'downside_std': ratio_stats['downside_std'],
            'sharpe_ratio': ratio_stats['sharpe_ratio'],
            'sortino_ratio': ratio_stats['sortino_ratio'],
            'bankrupt': bool(getattr(self, 'bankrupt', False)),
            'bankruptcy_index': getattr(self, 'bankruptcy_index', None),
        }

        if regime_summary is not None:
            self.stats['regime_summary'] = regime_summary

        if regime_stability_summary is not None:
            self.stats['regime_stability_summary'] = regime_stability_summary

        if self.execution_policy is not None:
            self.stats['execution_policy'] = self.execution_policy

    def _get_trade_flags(self):
        df = self.results
        closed_trade_rows = pd.Series(False, index=df.index)
        executed_rows = self._get_executed_trade_rows()
        if len(executed_rows) > 0:
            closed_trade_rows.loc[executed_rows.index] = True

        df['short_entry_flag'] = df['time'].isin(
            df.loc[closed_trade_rows & (df.short_close_timestamp > 0), 'short_open_timestamp']
        ).astype(int)
        df['short_exit_flag'] = df['time'].isin(
            df.loc[closed_trade_rows & (df.short_close_timestamp > 0), 'short_close_timestamp']
        ).astype(int)
        df['long_entry_flag'] = df['time'].isin(
            df.loc[closed_trade_rows & (df.long_close_timestamp > 0), 'long_open_timestamp']
        ).astype(int)
        df['long_exit_flag'] = df['time'].isin(
            df.loc[closed_trade_rows & (df.long_close_timestamp > 0), 'long_close_timestamp']
        ).astype(int)

    def _format_marker_number(self, value, precision=2):
        if pd.isna(value):
            return 'n/a'

        try:
            return f'{float(value):.{precision}f}'
        except Exception:
            return str(value)

    def _format_exit_reason(self, order_type):
        normalized = str(order_type or '').strip().lower()

        if normalized.startswith('stop_') and normalized.endswith('_loss'):
            return 'stop loss'

        if normalized.startswith('stop_') and normalized.endswith('_gain'):
            return 'stop gain'

        if normalized.startswith('close_'):
            return 'close normal'

        return 'exit'

    def _build_trade_markers(self):
        if self.results is None or len(self.results) == 0:
            return []

        markers = []

        for index, row in self.results.iterrows():
            time_value = int(row.time) if pd.notna(row.time) else None
            if time_value is None:
                continue

            if int(row.get('long_entry_flag', 0)) == 1:
                markers.append({
                    'id': f'long-open-{index}',
                    'time': time_value,
                    'position': 'belowBar',
                    'shape': 'arrowUp',
                    'color': self.ENTRY_MARKER_COLOR,
                    'text': f'Long open @ {self._format_marker_number(row.long_open_price, 5)}',
                    'size': 1,
                })

            if int(row.get('long_exit_flag', 0)) == 1:
                close_color = (
                    self.PROFIT_EXIT_MARKER_COLOR
                    if float(row.trade_net_pnl) >= 0
                    else self.LOSS_EXIT_MARKER_COLOR
                )
                exit_reason = self._format_exit_reason(row.get('order_type'))
                markers.append({
                    'id': f'long-close-{index}',
                    'time': time_value,
                    'position': 'aboveBar',
                    'shape': 'square',
                    'color': close_color,
                    'text': f'Long {exit_reason} | Net {self._format_marker_number(row.trade_net_pnl, 2)}',
                    'size': 1,
                })

            if int(row.get('short_entry_flag', 0)) == 1:
                markers.append({
                    'id': f'short-open-{index}',
                    'time': time_value,
                    'position': 'aboveBar',
                    'shape': 'arrowDown',
                    'color': self.ENTRY_MARKER_COLOR,
                    'text': f'Short open @ {self._format_marker_number(row.short_open_price, 5)}',
                    'size': 1,
                })

            if int(row.get('short_exit_flag', 0)) == 1:
                close_color = (
                    self.PROFIT_EXIT_MARKER_COLOR
                    if float(row.trade_net_pnl) >= 0
                    else self.LOSS_EXIT_MARKER_COLOR
                )
                exit_reason = self._format_exit_reason(row.get('order_type'))
                markers.append({
                    'id': f'short-close-{index}',
                    'time': time_value,
                    'position': 'belowBar',
                    'shape': 'square',
                    'color': close_color,
                    'text': f'Short {exit_reason} | Net {self._format_marker_number(row.trade_net_pnl, 2)}',
                    'size': 1,
                })

        return markers
