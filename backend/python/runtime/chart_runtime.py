import math
import time
import pandas as pd

try:
    from ..app_state import state
    from ..indicator_registry import (
        describe_indicator_columns,
        describe_indicator_feature_name,
        get_indicator_class,
        get_indicator_runtime_contract,
    )
    from ..lib.symbol import Symbol
    from ..services.engine_view_service import build_engine_consumer_views_payload
    from ..services.market_data_service import get_market_snapshot
    from .market_runtime import build_market_runtime_payload
except ImportError:
    from app_state import state
    from indicator_registry import (
        describe_indicator_columns,
        describe_indicator_feature_name,
        get_indicator_class,
        get_indicator_runtime_contract,
    )
    from lib.symbol import Symbol
    from services.engine_view_service import build_engine_consumer_views_payload
    from services.market_data_service import get_market_snapshot
    from runtime.market_runtime import build_market_runtime_payload


def invalidate_chart_snapshot(reason: str = 'manual_reset', preserve_existing: bool = False):
    chart_state = state.chart
    chart_state.snapshot_signature = None
    chart_state.snapshot_built_at = None
    chart_state.snapshot_error = None
    chart_state.snapshot_dirty_reason = reason
    chart_state.snapshot_affected_from_index = state.market.affected_from_index
    chart_state.snapshot_refresh_mode = None
    chart_state.snapshot_partial_eligible = False
    chart_state.snapshot_partial_blockers = []
    chart_state.snapshot_partial_opportunity = None
    chart_state.snapshot_runtime_contracts = []
    chart_state.snapshot_runtime_window = None
    chart_state.snapshot_performance = None
    chart_state.snapshot_recent_reasons = [
        {
            'kind': 'invalidate',
            'reason': str(reason or 'manual_reset'),
            'at': time.time(),
        },
        *list(chart_state.snapshot_recent_reasons or []),
    ][:12]

    if not preserve_existing:
        chart_state.snapshot_symbol = None
        chart_state.snapshot_candles = []
        chart_state.snapshot_indicators = []
        chart_state.snapshot_applied_indicators = []
        chart_state.snapshot_available_columns = []
        chart_state.snapshot_available_column_details = []


def build_indicator_runtime_diagnostics(indicators_payload: list[dict]):
    diagnostics = []

    for indicator in indicators_payload or []:
        contract = get_indicator_runtime_contract(indicator['name'], indicator.get('params', []))
        diagnostics.append({
            'name': indicator['name'],
            'params': list(indicator.get('params', [])),
            'incremental_mode': contract.get('incremental_mode'),
            'supports_partial_rebuild': bool(contract.get('supports_partial_rebuild')),
            'requires_full_rebuild': bool(contract.get('requires_full_rebuild')),
            'warmup_bars': int(contract.get('warmup_bars', 0) or 0),
            'patch_bars': int(contract.get('patch_bars', 0) or 0),
            'output_layer': contract.get('output_layer'),
            'input_layer': contract.get('input_layer'),
            'input_columns': list(contract.get('input_columns') or []),
        })

    return diagnostics


def build_indicator_instance(symbol: Symbol, name: str, params: list):
    indicator_class = get_indicator_class(name)

    if indicator_class is None:
        raise ValueError(f'Unknown indicator: {name}')

    return indicator_class(symbol, *params)


def apply_indicators(symbol: Symbol, indicators_payload: list[dict]):
    applied_indicators = []
    indicator_costs = []

    for indicator in indicators_payload:
        name = indicator['name']
        params = indicator.get('params', [])
        alias = str(indicator.get('alias') or '').strip()

        before_columns = list(symbol.candles.columns)

        print('---')
        print('INDICATOR:', name)
        print('PARAMS:', params)
        print('BEFORE:', before_columns)

        started_at = time.perf_counter()
        try:
            build_indicator_instance(symbol, name, params)
        except Exception as error:
            print('INDICATOR ERROR:', name)
            print('INDICATOR ERROR REPR:', repr(error))
            raise
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0

        after_columns = list(symbol.candles.columns)
        created_columns = [column for column in after_columns if column not in before_columns]

        print('AFTER:', after_columns)
        print('CREATED:', created_columns)

        applied_indicators.append({
            'name': name,
            'params': params,
            'alias': alias,
            'columns': created_columns,
            'column_details': describe_indicator_columns(name, params, created_columns),
        })
        indicator_costs.append({
            'name': name,
            'params': list(params),
            'elapsed_ms': elapsed_ms,
            'created_columns': len(created_columns),
            'row_count': int(len(symbol.candles.index)),
        })

    print('APPLIED INDICATORS:', applied_indicators)
    return applied_indicators, indicator_costs


def get_chart_market_context():
    chart_state = state.chart
    return get_market_snapshot(
        symbol=chart_state.request['symbol'],
        timeframe=chart_state.request['timeframe'],
        bars=chart_state.request['bars'],
    )


def build_symbol_snapshot():
    chart_state = state.chart
    market_context = get_chart_market_context()
    if not market_context['ready']:
        raise ValueError('Chart market data is not ready')

    symbol = Symbol(
        name=chart_state.request['symbol'],
        timeframe=chart_state.request['timeframe'],
        bars=chart_state.request['bars'],
        candles=list(market_context['candles']),
    )

    applied_indicators, indicator_costs = apply_indicators(symbol, chart_state.request['indicators'])

    return symbol, applied_indicators, indicator_costs


def can_use_partial_snapshot(indicators_payload: list[dict]):
    for indicator in indicators_payload:
        contract = get_indicator_runtime_contract(indicator['name'], indicator.get('params', []))
        if not contract.get('supports_partial_rebuild'):
            return False
    return True


def get_partial_rebuild_blockers(indicators_payload: list[dict]):
    blockers = []

    for indicator in indicators_payload or []:
        contract = get_indicator_runtime_contract(indicator['name'], indicator.get('params', []))
        if contract.get('supports_partial_rebuild'):
            continue

        blockers.append({
            'name': indicator['name'],
            'params': list(indicator.get('params', [])),
            'incremental_mode': contract.get('incremental_mode'),
            'reason': 'supports_partial_rebuild=false',
            'warmup_bars': int(contract.get('warmup_bars', 0) or 0),
            'patch_bars': int(contract.get('patch_bars', 0) or 0),
        })

    return blockers


def calculate_indicator_partial_window(indicator: dict, affected_from_index: int):
    contract = get_indicator_runtime_contract(indicator['name'], indicator.get('params', []))
    patch_bars = max(0, int(contract.get('patch_bars', 0) or 0))
    warmup_bars = max(0, int(contract.get('warmup_bars', 0) or 0))
    patch_from_index = max(0, affected_from_index - patch_bars)
    context_start = max(0, patch_from_index - warmup_bars)
    return context_start, patch_from_index


def calculate_partial_window(indicators_payload: list[dict], affected_from_index: int | None):
    if affected_from_index is None:
        return None, None

    context_start = affected_from_index
    patch_from_index = affected_from_index

    for indicator in indicators_payload:
        indicator_context_start, indicator_patch_from = calculate_indicator_partial_window(
            indicator,
            affected_from_index,
        )
        context_start = min(context_start, indicator_context_start)
        patch_from_index = min(patch_from_index, indicator_patch_from)

    return max(0, context_start), max(0, patch_from_index)


def build_partial_symbol_snapshot(context_start: int):
    chart_state = state.chart
    market_context = get_chart_market_context()
    if not market_context['ready']:
        raise ValueError('Chart market data is not ready')
    market_candles = list(market_context['candles'])

    symbol = Symbol(
        name=chart_state.request['symbol'],
        timeframe=chart_state.request['timeframe'],
        bars=len(market_candles[context_start:]),
        candles=market_candles[context_start:],
    )

    applied_indicators, indicator_costs = apply_indicators(symbol, chart_state.request['indicators'])
    return symbol, applied_indicators, indicator_costs


def merge_partial_snapshot(previous_symbol: Symbol, partial_symbol: Symbol, context_start: int, patch_from_index: int):
    market_context = get_chart_market_context()
    if not market_context['ready']:
        raise ValueError('Chart market data is not ready')

    merged_df = pd.DataFrame(list(market_context['candles'])).copy()
    previous_df = previous_symbol.candles.copy()
    partial_df = partial_symbol.candles.copy().reset_index(drop=True)

    indicator_columns = [column for column in previous_df.columns if column not in merged_df.columns]

    for column in indicator_columns:
        merged_df[column] = pd.NA
        if column in previous_df.columns:
            preserved_length = min(patch_from_index, len(previous_df))
            if preserved_length > 0:
                merged_df.loc[:preserved_length - 1, column] = previous_df.loc[:preserved_length - 1, column].to_list()

    for column in partial_df.columns:
        if column not in merged_df.columns:
            merged_df[column] = pd.NA

        local_patch_from_index = patch_from_index - context_start
        tail_values = partial_df.iloc[local_patch_from_index:][column].to_list()
        if tail_values:
            merged_df.loc[patch_from_index:, column] = tail_values

    merged_symbol = Symbol(
        name=previous_symbol.name,
        timeframe=previous_symbol.timeframe,
        bars=len(market_context['candles']),
        candles=list(market_context['candles']),
    )
    merged_symbol.candles = merged_df
    return merged_symbol


def build_snapshot_signature():
    market_context = get_chart_market_context()
    return {
        'request': dict(state.chart.request),
        'market_context_revision': market_context.get('revision'),
        'market_context_source': market_context.get('source'),
        'market_revision': state.market.revision if market_context.get('source') == 'bridge' else None,
    }


def build_column_details(columns: list[str]):
    return [describe_indicator_feature_name(column_name) for column_name in columns]


def build_indicator_column_details(applied_indicators: list[dict]):
    details = []

    for indicator in applied_indicators or []:
        for column_detail in indicator.get('column_details', []) or []:
            details.append(dict(column_detail))

    return details


def sanitize_value(value):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, (int, float)):
        numeric_value = float(value)
        if math.isnan(numeric_value) or math.isinf(numeric_value):
            return None
        return value

    if hasattr(value, 'item'):
        try:
            scalar_value = value.item()
            return sanitize_value(scalar_value)
        except Exception:
            pass

    return value


def sanitize_records(records: list[dict]):
    sanitized = []

    for record in records:
        sanitized_record = {}

        for key, value in record.items():
            sanitized_record[key] = sanitize_value(value)

        sanitized.append(sanitized_record)

    return sanitized


def extract_chart_data(symbol: Symbol):
    ohlc_columns = ['time', 'open', 'high', 'low', 'close', 'volume', 'tick_volume', 'real_volume']
    df = symbol.candles.copy()

    if df.empty:
        return [], []

    candle_columns = [column for column in ohlc_columns if column in df.columns]
    candles = df[candle_columns].to_dict(orient='records')
    candles = sanitize_records(candles)

    # Backward compatibility: older cached history may only carry `volume`.
    # Expose explicit volume fields so the frontend volume mode switch remains usable.
    for candle in candles:
        tick_volume = candle.get('tick_volume')
        real_volume = candle.get('real_volume')
        volume = candle.get('volume')

        if tick_volume is None:
            candle['tick_volume'] = volume if volume is not None else 0.0

        if real_volume is None:
            candle['real_volume'] = 0.0

        if volume is None:
            resolved_real = candle.get('real_volume')
            resolved_tick = candle.get('tick_volume')
            candle['volume'] = (
                resolved_real
                if resolved_real not in (None, 0, 0.0)
                else (resolved_tick if resolved_tick is not None else 0.0)
            )

    indicator_columns = [column for column in df.columns if column not in candle_columns]
    if not indicator_columns:
        return candles, []

    indicators = df[['time', *indicator_columns]].to_dict(orient='records')
    indicators = sanitize_records(indicators)

    return candles, indicators


def ensure_chart_snapshot(force_rebuild: bool = False):
    chart_state = state.chart
    started_at = time.perf_counter()
    market_context = get_chart_market_context()
    signature = build_snapshot_signature()
    runtime_contracts = build_indicator_runtime_diagnostics(chart_state.request['indicators'])
    partial_blockers = get_partial_rebuild_blockers(chart_state.request['indicators'])
    partial_eligible = len(partial_blockers) == 0

    if (
        not force_rebuild
        and chart_state.snapshot_signature == signature
        and chart_state.snapshot_symbol is not None
        and chart_state.snapshot_error is None
    ):
        return chart_state.snapshot_symbol, chart_state.snapshot_applied_indicators

    should_try_partial = (
        not force_rebuild
        and chart_state.snapshot_symbol is not None
        and chart_state.snapshot_error is None
        and market_context.get('source') == 'bridge'
        and partial_eligible
        and state.market.affected_from_index is not None
    )
    partial_opportunity = None
    if not should_try_partial:
        if force_rebuild:
            partial_opportunity = {
                'status': 'lost',
                'reason': 'force_rebuild',
            }
        elif chart_state.snapshot_symbol is None or chart_state.snapshot_error is not None:
            partial_opportunity = {
                'status': 'unavailable',
                'reason': 'no_clean_previous_snapshot',
            }
        elif market_context.get('source') != 'bridge':
            partial_opportunity = {
                'status': 'unavailable',
                'reason': 'market_context_not_live_bridge',
                'source': market_context.get('source'),
            }
        elif not partial_eligible:
            partial_opportunity = {
                'status': 'blocked',
                'reason': 'indicator_runtime_contracts',
                'blocker_count': len(partial_blockers),
            }
        elif state.market.affected_from_index is None:
            partial_opportunity = {
                'status': 'unavailable',
                'reason': 'no_affected_from_index',
            }

    recompute_from_index = state.market.affected_from_index
    context_start = state.market.affected_from_index

    if should_try_partial:
        context_start, recompute_from_index = calculate_partial_window(
            chart_state.request['indicators'],
            state.market.affected_from_index,
        )

    try:
        if should_try_partial and recompute_from_index is not None and context_start is not None:
            partial_symbol, applied_indicators, indicator_costs = build_partial_symbol_snapshot(context_start)
            symbol = merge_partial_snapshot(
                previous_symbol=chart_state.snapshot_symbol,
                partial_symbol=partial_symbol,
                context_start=context_start,
                patch_from_index=recompute_from_index,
            )
        else:
            recompute_from_index = state.market.affected_from_index
            symbol, applied_indicators, indicator_costs = build_symbol_snapshot()

        candles, indicators = extract_chart_data(symbol)
    except Exception as error:
        chart_state.snapshot_error = str(error)
        chart_state.snapshot_dirty_reason = 'snapshot_build_failed'
        raise

    chart_state.snapshot_signature = signature
    chart_state.snapshot_symbol = symbol
    chart_state.snapshot_candles = candles
    chart_state.snapshot_indicators = indicators
    chart_state.snapshot_applied_indicators = applied_indicators
    chart_state.snapshot_available_columns = list(symbol.candles.columns)
    chart_state.snapshot_available_column_details = build_column_details(chart_state.snapshot_available_columns)
    chart_state.snapshot_built_at = time.time()
    chart_state.snapshot_error = None
    chart_state.snapshot_dirty_reason = None
    chart_state.snapshot_affected_from_index = recompute_from_index
    chart_state.snapshot_refresh_mode = 'partial' if bool(should_try_partial and recompute_from_index is not None) else 'full'
    refresh_mode = chart_state.snapshot_refresh_mode
    chart_state.snapshot_partial_eligible = partial_eligible
    chart_state.snapshot_partial_blockers = partial_blockers
    chart_state.snapshot_partial_opportunity = partial_opportunity
    chart_state.snapshot_runtime_contracts = runtime_contracts
    chart_state.snapshot_runtime_window = {
        'affected_from_index': state.market.affected_from_index,
        'context_start': context_start,
        'recompute_from_index': recompute_from_index,
    }
    total_elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    indicator_total_ms = sum(float(item.get('elapsed_ms') or 0.0) for item in (indicator_costs or []))
    chart_state.snapshot_performance = {
        'total_elapsed_ms': total_elapsed_ms,
        'indicator_total_ms': indicator_total_ms,
        'non_indicator_elapsed_ms': max(0.0, total_elapsed_ms - indicator_total_ms),
        'indicator_costs': list(indicator_costs or []),
        'indicator_count': len(indicator_costs or []),
        'candles': len(candles),
        'indicator_rows': len(indicators),
    }
    refresh_counts = dict(chart_state.snapshot_refresh_counts or {})
    refresh_counts[refresh_mode] = int(refresh_counts.get(refresh_mode, 0) or 0) + 1
    chart_state.snapshot_refresh_counts = refresh_counts
    chart_state.snapshot_recent_reasons = [
        {
            'kind': 'refresh',
            'mode': refresh_mode,
            'reason': chart_state.snapshot_dirty_reason or ('force_rebuild' if force_rebuild else 'market_update'),
            'at': chart_state.snapshot_built_at,
            'partial_eligible': partial_eligible,
            'blocker_count': len(partial_blockers),
        },
        *list(chart_state.snapshot_recent_reasons or []),
    ][:12]

    print(
        'CHART SNAPSHOT BUILT:',
        {
            'market_context_revision': market_context.get('revision'),
            'market_context_source': market_context.get('source'),
            'market_revision': state.market.revision,
            'affected_from_index': chart_state.snapshot_affected_from_index,
            'refresh_mode': chart_state.snapshot_refresh_mode,
            'partial_eligible': partial_eligible,
            'partial_blockers': partial_blockers,
            'partial_opportunity': partial_opportunity,
            'performance': chart_state.snapshot_performance,
            'runtime_window': chart_state.snapshot_runtime_window,
            'candles': len(candles),
            'indicator_rows': len(indicators),
            'applied_indicators': len(applied_indicators),
        }
    )

    return symbol, applied_indicators


def build_chart_runtime_payload():
    chart_state = state.chart
    symbol_snapshot = chart_state.snapshot_symbol.snapshot() if chart_state.snapshot_symbol is not None else None
    return {
        'snapshot_signature': dict(chart_state.snapshot_signature) if chart_state.snapshot_signature else None,
        'snapshot_built_at': chart_state.snapshot_built_at,
        'snapshot_error': chart_state.snapshot_error,
        'snapshot_dirty_reason': chart_state.snapshot_dirty_reason,
        'snapshot_affected_from_index': chart_state.snapshot_affected_from_index,
        'snapshot_refresh_mode': chart_state.snapshot_refresh_mode,
        'snapshot_partial_eligible': chart_state.snapshot_partial_eligible,
        'snapshot_partial_blockers': list(chart_state.snapshot_partial_blockers),
        'snapshot_partial_opportunity': dict(chart_state.snapshot_partial_opportunity) if chart_state.snapshot_partial_opportunity else None,
        'snapshot_runtime_contracts': list(chart_state.snapshot_runtime_contracts),
        'snapshot_runtime_window': dict(chart_state.snapshot_runtime_window) if chart_state.snapshot_runtime_window else None,
        'snapshot_performance': dict(chart_state.snapshot_performance) if chart_state.snapshot_performance else None,
        'snapshot_refresh_counts': dict(chart_state.snapshot_refresh_counts),
        'snapshot_recent_reasons': list(chart_state.snapshot_recent_reasons),
        'symbol_snapshot': {
            'name': symbol_snapshot.name,
            'timeframe': symbol_snapshot.timeframe,
            'bars': symbol_snapshot.bars,
            'row_count': symbol_snapshot.row_count,
            'market_columns': list(symbol_snapshot.market_columns),
            'derived_columns': list(symbol_snapshot.derived_columns),
            'total_columns': list(symbol_snapshot.total_columns),
        } if symbol_snapshot else None,
        'snapshot_available_column_details': list(chart_state.snapshot_available_column_details),
        'snapshot_indicator_column_details': build_indicator_column_details(chart_state.snapshot_applied_indicators),
        'consumer_views': build_engine_consumer_views_payload(),
        'market_runtime': build_market_runtime_payload(),
    }
