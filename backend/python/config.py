import os


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_csv_env(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)

    if value is None:
        return list(default or [])

    return [
        item.strip()
        for item in value.split(',')
        if item.strip()
    ]


def build_service_config():
    host = os.getenv('ROBOTINEEKO_HOST', '127.0.0.1')
    port = get_int_env('ROBOTINEEKO_PORT', 8000)
    log_level = os.getenv('ROBOTINEEKO_LOG_LEVEL', 'info')
    reload = get_bool_env('ROBOTINEEKO_RELOAD', False)
    cors_origins = get_csv_env(
        'ROBOTINEEKO_CORS_ORIGINS',
        default=[
            'http://localhost:5173',
            'http://127.0.0.1:5173',
            'http://localhost:5174',
            'http://127.0.0.1:5174',
        ],
    )

    return {
        'host': host,
        'port': port,
        'log_level': log_level,
        'reload': reload,
        'cors_origins': cors_origins,
        'trade_internal_token': os.getenv('ROBOTINEEKO_TRADE_INTERNAL_TOKEN', '').strip(),
    }


def build_trade_service_config():
    base_config = build_service_config()
    host = os.getenv('ROBOTINEEKO_TRADE_HOST', base_config['host'])
    port = get_int_env('ROBOTINEEKO_TRADE_PORT', 8011)

    return {
        **base_config,
        'host': host,
        'port': port,
        'internal_token': os.getenv('ROBOTINEEKO_TRADE_INTERNAL_TOKEN', '').strip(),
        'backend_base_url': os.getenv(
            'ROBOTINEEKO_BACKEND_BASE_URL',
            f"http://{base_config['host']}:{base_config['port']}",
        ).rstrip('/'),
    }


def build_feature_flags():
    return {
        'backtest_portfolios_v2': get_bool_env('ROBOTINEEKO_BACKTEST_PORTFOLIOS_V2', False),
        'results_scope_projection_v2': get_bool_env('ROBOTINEEKO_RESULTS_SCOPE_PROJECTION_V2', False),
        'trader_portfolios_v2': get_bool_env('ROBOTINEEKO_TRADER_PORTFOLIOS_V2', False),
        'trader_volume_modes_v2': get_bool_env('ROBOTINEEKO_TRADER_VOLUME_MODES_V2', False),
    }
