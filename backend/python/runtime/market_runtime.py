import time

try:
    from ..app_state import state
    from ..indicator_registry import describe_indicator_feature_name
except ImportError:
    from app_state import state
    from indicator_registry import describe_indicator_feature_name


def build_market_feature_details(feature_names):
    return [describe_indicator_feature_name(feature_name) for feature_name in feature_names]


def reset_market_runtime(reason: str = 'manual_reset'):
    market_state = state.market
    market_state.revision += 1
    market_state.candle_revision += 1
    market_state.last_event = f'reset:{reason}'
    market_state.affected_from_index = 0
    market_state.latest_candle_time = None
    market_state.previous_candle_time = None
    market_state.last_update_at = time.time()
    market_state.last_replaced_times = []
    market_state.changed_features = []
    market_state.changed_feature_details = []

    state.bridge.last_affected_index = 0
    state.bridge.last_update_replaced_times = []


def mark_history_loaded(candles: list[dict]):
    market_state = state.market
    market_state.revision += 1
    market_state.candle_revision += 1
    market_state.last_event = 'history_loaded'
    market_state.affected_from_index = 0 if candles else None
    market_state.latest_candle_time = candles[-1]['time'] if candles else None
    market_state.previous_candle_time = candles[-2]['time'] if len(candles) >= 2 else None
    market_state.last_update_at = time.time()
    market_state.last_replaced_times = []
    market_state.changed_features = []
    market_state.changed_feature_details = []

    state.bridge.last_affected_index = market_state.affected_from_index
    state.bridge.last_update_replaced_times = []


def mark_candle_update(
    candles: list[dict],
    affected_from_index: int | None,
    replaced_times: list[int],
    changed_features: list[str] | None = None,
):
    market_state = state.market
    market_state.revision += 1
    market_state.tick_revision += 1

    if affected_from_index is not None:
        market_state.candle_revision += 1

    market_state.last_event = 'candle_update'
    market_state.affected_from_index = affected_from_index
    market_state.latest_candle_time = candles[-1]['time'] if candles else None
    market_state.previous_candle_time = candles[-2]['time'] if len(candles) >= 2 else None
    market_state.last_update_at = time.time()
    market_state.last_replaced_times = list(replaced_times)
    market_state.changed_features = list(changed_features or [])
    market_state.changed_feature_details = build_market_feature_details(market_state.changed_features)

    state.bridge.last_affected_index = affected_from_index
    state.bridge.last_update_replaced_times = list(replaced_times)


def build_market_runtime_payload():
    market_state = state.market
    return {
        'revision': market_state.revision,
        'tick_revision': market_state.tick_revision,
        'candle_revision': market_state.candle_revision,
        'last_event': market_state.last_event,
        'affected_from_index': market_state.affected_from_index,
        'latest_candle_time': market_state.latest_candle_time,
        'previous_candle_time': market_state.previous_candle_time,
        'last_update_at': market_state.last_update_at,
        'last_replaced_times': list(market_state.last_replaced_times),
        'changed_features': list(market_state.changed_features),
        'changed_feature_details': list(market_state.changed_feature_details),
    }
