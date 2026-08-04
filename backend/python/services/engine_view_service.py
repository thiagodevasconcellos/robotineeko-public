try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from ..app_state import state
    from ..lib.symbol import Symbol
    from .market_data_service import get_market_snapshot
except ImportError:
    from app_state import state
    from lib.symbol import Symbol
    from services.market_data_service import get_market_snapshot


def _safe_result_rows(results):
    if results is None:
        return []

    if pd is not None and isinstance(results, pd.DataFrame):
        return results.to_dict(orient='records')

    if hasattr(results, 'to_dict'):
        try:
            return results.to_dict(orient='records')
        except Exception:
            pass

    try:
        return list(results)
    except Exception:
        return []


def _filter_column_details_for_columns(column_details: list[dict] | None, available_columns: list[str]):
    available = {str(column).strip() for column in (available_columns or [])}
    filtered = []

    for detail in column_details or []:
        column_name = str(
            detail.get('normalized_column_name')
            or detail.get('column_name')
            or ''
        ).strip()
        if column_name and column_name in available:
            filtered.append(dict(detail))

    return filtered


def _build_scoped_symbol(snapshot_symbol: Symbol, chart_request: dict, history_scope_mode: str = 'loaded_chart', history_scope_bars: int | None = None):
    safe_scope_mode = str(history_scope_mode or 'loaded_chart').strip().lower() or 'loaded_chart'
    available_candles = snapshot_symbol.candles
    available_bars = len(available_candles)

    if safe_scope_mode != 'custom':
        return snapshot_symbol, {
            'history_scope_mode': 'loaded_chart',
            'history_scope_bars': available_bars,
            'history_scope_available_bars': available_bars,
        }

    requested_bars = max(1, int(history_scope_bars or 1))
    scoped_candles = available_candles.tail(requested_bars).copy()
    scoped_bars = len(scoped_candles)
    symbol = Symbol(
        name=chart_request['symbol'],
        timeframe=chart_request['timeframe'],
        bars=scoped_bars,
        candles=scoped_candles,
        copy_candles=False,
    )
    return symbol, {
        'history_scope_mode': 'custom',
        'history_scope_bars': scoped_bars,
        'history_scope_requested_bars': requested_bars,
        'history_scope_available_bars': available_bars,
    }


def build_strategy_feature_view(
    chart_request: dict,
    snapshot_symbol: Symbol,
    applied_indicators: list[dict] | None = None,
    available_column_details: list[dict] | None = None,
    backtest_params: dict | None = None,
    snapshot_signature: dict | None = None,
):
    safe_backtest = backtest_params or {}
    symbol, history_scope_info = _build_scoped_symbol(
        snapshot_symbol=snapshot_symbol,
        chart_request=chart_request,
        history_scope_mode=safe_backtest.get('history_scope_mode') or 'loaded_chart',
        history_scope_bars=safe_backtest.get('history_scope_bars'),
    )
    available_columns = list(symbol.candles.columns)
    filtered_column_details = _filter_column_details_for_columns(
        available_column_details,
        available_columns,
    )

    return {
        'view_type': 'strategy_feature_view',
        'symbol': symbol,
        'available_columns': available_columns,
        'available_column_details': filtered_column_details,
        'applied_indicators': list(applied_indicators or []),
        'history_scope_info': history_scope_info,
        'meta': {
            'symbol': chart_request.get('symbol'),
            'timeframe': chart_request.get('timeframe'),
            'bars': int(chart_request.get('bars') or 0),
            'row_count': int(len(symbol.candles.index)),
            'snapshot_market_context_revision': (snapshot_signature or {}).get('market_context_revision'),
            'snapshot_market_revision': (snapshot_signature or {}).get('market_revision'),
            'snapshot_cache_key': (snapshot_signature or {}).get('market_context_key'),
            'snapshot_refresh_mode': (snapshot_signature or {}).get('refresh_mode'),
            'market_columns': list(symbol.market_columns),
            'derived_columns': list(symbol.derived_columns),
            'history_scope_mode': history_scope_info.get('history_scope_mode'),
            'history_scope_bars': history_scope_info.get('history_scope_bars'),
            'history_scope_available_bars': history_scope_info.get('history_scope_available_bars'),
            'applied_indicator_count': len(applied_indicators or []),
        },
    }


def build_neural_market_view(config: dict, include_candles: bool = True):
    requested_symbol = str(config.get('symbol') or '').strip().upper()
    requested_timeframe = str(config.get('timeframe') or '').strip().upper()
    requested_bars = max(1, int(config.get('bars') or 1))

    market_context = get_market_snapshot(
        symbol=requested_symbol,
        timeframe=requested_timeframe,
        bars=requested_bars,
    )

    available_candles = list(market_context.get('candles') or [])
    ready = bool(market_context.get('ready'))
    enough_bars = len(available_candles) >= requested_bars
    error = None

    if not ready:
        error = (
            'Neural isolated mode requires a ready market-data cache for the requested context. '
            f'Current cache for {requested_symbol} {requested_timeframe} {requested_bars:,} bars is not ready.'
        )
    elif not enough_bars:
        error = (
            'Neural isolated mode requires the market-data cache to already contain enough candles. '
            f'Current cache has {len(available_candles):,} candles, but the neural config requests '
            f'{requested_bars:,}.'
        )

    snapshot_candles = [dict(candle) for candle in available_candles[-requested_bars:]] if include_candles and ready and enough_bars else []

    return {
        'view_type': 'neural_market_view',
        'ready': ready and enough_bars,
        'error': error,
        'symbol': requested_symbol,
        'timeframe': requested_timeframe,
        'bars_requested': requested_bars,
        'bars_available': len(available_candles),
        'cache_key': market_context.get('cache_key'),
        'source': market_context.get('source'),
        'revision': market_context.get('revision'),
        'candles': snapshot_candles,
        'meta': {
            'symbol': requested_symbol,
            'timeframe': requested_timeframe,
            'bars_requested': requested_bars,
            'bars_available': len(available_candles),
            'cache_key': market_context.get('cache_key'),
            'source': market_context.get('source'),
            'ready': ready and enough_bars,
        },
    }


def build_results_view(
    request: dict | None = None,
    stats: dict | None = None,
    results: list | None = None,
    trade_markers: list[dict] | None = None,
    strategy_view_meta: dict | None = None,
):
    safe_request = dict(request or {})
    safe_backtest = dict(safe_request.get('backtest') or {})
    safe_stats = dict(stats or {})
    safe_trade_markers = list(trade_markers or [])
    safe_results = _safe_result_rows(results)
    row_count = len(safe_results)

    return {
        'view_type': 'results_view',
        'meta': {
            'row_count': int(row_count),
            'trade_marker_count': len(safe_trade_markers),
            'history_scope_mode': safe_backtest.get('historyScopeMode') or safe_backtest.get('history_scope_mode'),
            'history_scope_bars': safe_backtest.get('historyScopeBars') or safe_backtest.get('history_scope_bars'),
            'execution_mode': safe_backtest.get('executionMode') or safe_backtest.get('execution_mode'),
            'snapshot_market_context_revision': (strategy_view_meta or {}).get('snapshot_market_context_revision'),
            'snapshot_market_revision': (strategy_view_meta or {}).get('snapshot_market_revision'),
            'snapshot_cache_key': (strategy_view_meta or {}).get('snapshot_cache_key'),
            'snapshot_refresh_mode': (strategy_view_meta or {}).get('snapshot_refresh_mode'),
            'n_trades': safe_stats.get('n_trades'),
            'net_pnl': safe_stats.get('net_pnl'),
            'win_rate': safe_stats.get('win_rate'),
            'max_drawdown': safe_stats.get('max_drawdown'),
            'max_drawdown_pct': safe_stats.get('max_drawdown_pct'),
        },
    }


def build_engine_consumer_views_payload():
    chart_state = state.chart
    strategy_view = None

    if chart_state.snapshot_symbol is not None and chart_state.request:
        try:
            strategy_view = build_strategy_feature_view(
                chart_request=dict(chart_state.request),
                snapshot_symbol=chart_state.snapshot_symbol,
                applied_indicators=list(chart_state.snapshot_applied_indicators),
                available_column_details=list(chart_state.snapshot_available_column_details),
                backtest_params={'history_scope_mode': 'loaded_chart'},
                snapshot_signature=dict(chart_state.snapshot_signature or {}),
            ).get('meta')
        except Exception:
            strategy_view = None

    neural_view = None
    if chart_state.request:
        try:
            neural_view = build_neural_market_view({
                'symbol': chart_state.request.get('symbol'),
                'timeframe': chart_state.request.get('timeframe'),
                'bars': chart_state.request.get('bars'),
            }, include_candles=False).get('meta')
        except Exception:
            neural_view = None

    results_view = None
    strategy_state = state.strategy
    if strategy_state.request:
        try:
            results_view = build_results_view(
                request=dict(strategy_state.request or {}),
                stats=dict(strategy_state.stats or {}),
                results=strategy_state.results,
                trade_markers=list(strategy_state.trade_markers or []),
                strategy_view_meta=dict(strategy_state.strategy_view_meta or {}),
            ).get('meta')
        except Exception:
            results_view = None

    return {
        'strategy_feature_view': strategy_view,
        'results_view': results_view,
        'neural_market_view': neural_view,
    }
