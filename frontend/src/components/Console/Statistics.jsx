import './Statistics.css';

function formatNumber(value, decimals = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '-';
    }

    return Number(value).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function formatInteger(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '-';
    }

    return Number(value).toLocaleString('en-US', {
        maximumFractionDigits: 0,
    });
}

function formatPercent(value, decimals = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '-';
    }

    return `${formatNumber(Number(value) * 100, decimals)}%`;
}

function formatSignedNumber(value, decimals = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '-';
    }

    const number = Number(value);
    const signal = number > 0 ? '+' : '';

    return `${signal}${formatNumber(number, decimals)}`;
}

function StatField({ label, value, tone = 'neutral' }) {
    return (
        <div className={`field ${tone}`}>
            <div className='label'>{label}</div>
            <div className='value'>{value}</div>
        </div>
    );
}

function formatPolicyValue(value) {
    if (typeof value === 'boolean') {
        return value ? 'yes' : 'no';
    }

    if (value === null || value === undefined || value === '') {
        return '-';
    }

    return String(value).replace(/_/g, ' ');
}

function formatCostBreakdown(items = []) {
    if (!Array.isArray(items) || !items.length) {
        return '-';
    }

    return items
        .slice(0, 2)
        .map((item) => `${String(item?.label || item?.id || 'Cost item')}: ${formatNumber(item?.amount)}`)
        .join(' · ');
}

export function Statistics({ isActive, strategyApplyResponse }) {
    const stats = strategyApplyResponse?.stats || null;
    const executionPolicy = stats?.execution_policy || strategyApplyResponse?.execution_policy || null;

    if (!stats) {
        return (
            <div className={`Statistics ${isActive ? 'active' : ''}`}>
                <div className='group empty'>
                    <div className='title'>Statistics</div>
                    <div className='emptyMessage'>
                        Run a backtest to see the statistics.
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className={`Statistics ${isActive ? 'active' : ''}`}>
            <div className='group summary'>
                <div className='title'>Summary</div>

                <StatField label='Initial balance' value={formatNumber(stats.initial_balance)} />
                <StatField label='Final balance' value={formatNumber(stats.final_balance)} />
                <StatField
                    label='Balance change'
                    value={formatSignedNumber(stats.balance_change)}
                    tone={Number(stats.balance_change) >= 0 ? 'positive' : 'negative'}
                />
                <StatField
                    label='Gross result'
                    value={formatSignedNumber(stats.gross_result)}
                    tone={Number(stats.gross_result) >= 0 ? 'positive' : 'negative'}
                />
                <StatField
                    label='Net result'
                    value={formatSignedNumber(stats.net_result)}
                    tone={Number(stats.net_result) >= 0 ? 'positive' : 'negative'}
                />
                <StatField label='Operational costs' value={formatSignedNumber(stats.total_operational_cost)} tone='negative' />
                <StatField label='Estimated taxes' value={formatSignedNumber(stats.total_estimated_tax)} tone='negative' />
                <StatField label='Total cost drag' value={formatSignedNumber(stats.total_cost)} tone='negative' />
                <StatField label='Win rate' value={formatPercent(stats.win_rate)} />
            </div>

            <div className='group trades'>
                <div className='title'>Trades</div>

                <StatField label='Total trades' value={formatInteger(stats.n_trades)} />
                <StatField label='Gross wins' value={formatInteger(stats.n_gross_profits)} />
                <StatField label='Gross losses' value={formatInteger(stats.n_gross_losses)} />
                <StatField label='Net wins' value={formatInteger(stats.n_net_profits)} />
                <StatField label='Net losses' value={formatInteger(stats.n_net_losses)} />
                <StatField label='Loss rate' value={formatPercent(stats.loss_rate)} />
            </div>

            <div className='group pnl'>
                <div className='title'>PnL</div>

                <StatField label='Gross profit' value={formatNumber(stats.gross_profit)} tone='positive' />
                <StatField label='Gross loss' value={formatNumber(stats.gross_loss)} tone='negative' />
                <StatField label='Net profit' value={formatNumber(stats.net_profit)} tone='positive' />
                <StatField label='Net loss' value={formatNumber(stats.net_loss)} tone='negative' />
                <StatField label='Gross profit factor' value={formatNumber(stats.gross_profit_factor)} />
                <StatField label='Net profit factor' value={formatNumber(stats.net_profit_factor)} />
            </div>

            <div className='group averages'>
                <div className='title'>Averages</div>

                <StatField label='Avg gross profit' value={formatNumber(stats.avg_gross_profit)} tone='positive' />
                <StatField label='Avg gross loss' value={formatNumber(stats.avg_gross_loss)} tone='negative' />
                <StatField label='Avg net profit' value={formatNumber(stats.avg_net_profit)} tone='positive' />
                <StatField label='Avg net loss' value={formatNumber(stats.avg_net_loss)} tone='negative' />
                <StatField label='Risk reward ratio' value={formatNumber(stats.risk_reward_ratio)} />
                <StatField label='Cost factor' value={formatNumber(stats.cost_factor)} />
            </div>

            <div className='group risk'>
                <div className='title'>Risk</div>

                <StatField label='Max drawdown' value={formatNumber(stats.max_drawdown)} tone='negative' />
                <StatField label='Max drawdown %' value={formatPercent(stats.max_drawdown_pct)} tone='negative' />
                <StatField label='Drawdown duration bars' value={formatInteger(stats.drawdown_duration_bars)} />
                <StatField label='Sharpe ratio' value={formatNumber(stats.sharpe_ratio, 4)} />
                <StatField label='Sortino ratio' value={formatNumber(stats.sortino_ratio, 4)} />
                <StatField label='Avg return' value={formatNumber(stats.avg_return, 6)} />
            </div>

            {executionPolicy && (
                <div className='group execution'>
                    <div className='title'>Execution policy</div>

                    <StatField label='Requested cost mode' value={formatPolicyValue(executionPolicy.requested_cost_profile_label || executionPolicy.requested_cost_profile)} />
                    <StatField label='Effective cost model' value={formatPolicyValue(executionPolicy.cost_profile_label || executionPolicy.cost_profile)} />
                    <StatField label='Broker scope' value={formatPolicyValue(executionPolicy.broker_profile_label || executionPolicy.broker_label || executionPolicy.broker_code)} />
                    <StatField label='Asset type' value={formatPolicyValue(executionPolicy.asset_type_label || executionPolicy.asset_type)} />
                    <StatField label='Operational cost items' value={formatCostBreakdown(stats.operational_cost_breakdown_totals)} />
                    <StatField label='Estimated tax items' value={formatCostBreakdown(stats.estimated_tax_breakdown_totals)} />
                    <StatField label='Execution mode' value={formatPolicyValue(executionPolicy.execution_mode)} />
                    <StatField label='Spread (pips)' value={formatPolicyValue(executionPolicy.spread_in_pips)} />
                    <StatField label='Entry slippage (pips)' value={formatPolicyValue(executionPolicy.entry_slippage_in_pips)} />
                    <StatField label='Close slippage (pips)' value={formatPolicyValue(executionPolicy.close_slippage_in_pips)} />
                    <StatField label='Take profit slippage (pips)' value={formatPolicyValue(executionPolicy.take_profit_slippage_in_pips)} />
                    <StatField label='Stop loss slippage (pips)' value={formatPolicyValue(executionPolicy.stop_loss_slippage_in_pips)} />
                    <StatField label='Trailing stop slippage (pips)' value={formatPolicyValue(executionPolicy.trailing_stop_slippage_in_pips)} />
                    <StatField label='Volatility slippage multiplier' value={formatPolicyValue(executionPolicy.volatility_slippage_multiplier)} />
                    <StatField label='Volatility reference' value={formatPolicyValue(executionPolicy.volatility_slippage_reference)} />
                    <StatField label='Take profit fill' value={formatPolicyValue(executionPolicy.take_profit_fill)} />
                    <StatField label='Stop loss fill' value={formatPolicyValue(executionPolicy.stop_loss_fill)} />
                    <StatField label='Trailing stop fill' value={formatPolicyValue(executionPolicy.trailing_stop_fill)} />
                    <StatField label='Trailing on entry candle' value={formatPolicyValue(executionPolicy.same_bar_trailing_exit)} />
                    <StatField label='Intrabar conflict' value={formatPolicyValue(executionPolicy.intrabar_conflict_policy)} />
                </div>
            )}
        </div>
    );
}
