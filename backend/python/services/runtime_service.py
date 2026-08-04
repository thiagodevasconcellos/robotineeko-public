import time

try:
    from ..app_state import state
    from ..runtime.chart_runtime import ensure_chart_snapshot
except ImportError:
    from app_state import state
    from runtime.chart_runtime import ensure_chart_snapshot


def warm_chart_runtime():
    if not state.bridge.history_ready:
        return None

    ensure_chart_snapshot()
    state.runtime_service.last_chart_warm_at = time.time()
    return {
        'snapshot_built_at': state.chart.snapshot_built_at,
        'available_columns': len(state.chart.snapshot_available_columns),
    }


def refresh_strategy_runtime_if_active():
    strategy_request = state.strategy.request

    if not strategy_request or not state.strategy.backtest_active:
        return None

    try:
        try:
            from .. import strategy_backend
        except ImportError:
            import strategy_backend

        refreshed = strategy_backend.refresh_stale_strategy_if_needed()
        state.runtime_service.last_strategy_refresh_at = time.time()
        return refreshed
    except Exception as error:
        state.runtime_service.last_error = str(error)
        return {
            'status': 'error',
            'error': str(error),
        }


def run_runtime_maintenance(trigger: str):
    state.runtime_service.last_trigger = trigger
    state.runtime_service.last_run_at = time.time()
    state.runtime_service.last_error = None

    result = {
        'trigger': trigger,
        'chart_warmed': False,
        'strategy_refreshed': False,
        'strategy_refresh_mode': None,
        'error': None,
    }

    try:
        chart_result = warm_chart_runtime()
        result['chart_warmed'] = chart_result is not None

        strategy_result = refresh_strategy_runtime_if_active()
        if strategy_result:
            result['strategy_refreshed'] = strategy_result.get('status') == 'ok'
            result['strategy_refresh_mode'] = strategy_result.get('refresh_mode')

        return result
    except Exception as error:
        state.runtime_service.last_error = str(error)
        result['error'] = str(error)
        return result


def build_runtime_service_payload():
    runtime_state = state.runtime_service
    return {
        'last_trigger': runtime_state.last_trigger,
        'last_run_at': runtime_state.last_run_at,
        'last_chart_warm_at': runtime_state.last_chart_warm_at,
        'last_strategy_refresh_at': runtime_state.last_strategy_refresh_at,
        'last_error': runtime_state.last_error,
    }
