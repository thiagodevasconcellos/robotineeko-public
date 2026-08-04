import copy
import time

from .workspace_store import (
    create_workspace_research_paper,
    create_workspace_strategy_benchmark,
    delete_workspace_strategy_benchmark,
    list_workspace_research_papers,
    list_workspace_strategy_benchmarks,
)


GUEST_WORKSPACE_ID = 'default'


GUEST_DEMO_INDICATORS = [
    {'name': 'EMA', 'params': ['close', 20], 'alias': 'ema20'},
    {'name': 'EMA', 'params': ['close', 50], 'alias': 'ema50'},
    {'name': 'RSI', 'params': ['close', 14], 'alias': 'rsi14'},
]


GUEST_DEMO_STRATEGY = {
    'long': {
        'openPrice': 'open[0]',
        'closePrice': 'open[0]',
        'openIf': 'EMA_close_20[0] > EMA_close_50[0] and RSI_close_14[0] < 68',
        'closeIf': 'EMA_close_20[0] < EMA_close_50[0] or RSI_close_14[0] > 72',
        'gainPrice': 'long_open_price[0] + (0.0001 * 18)',
        'lossPrice': 'long_open_price[0] - (0.0001 * 12)',
        'trailingPrice': '',
    },
    'short': {
        'openPrice': 'open[0]',
        'closePrice': 'open[0]',
        'openIf': 'EMA_close_20[0] < EMA_close_50[0] and RSI_close_14[0] > 32',
        'closeIf': 'EMA_close_20[0] > EMA_close_50[0] or RSI_close_14[0] < 28',
        'gainPrice': 'short_open_price[0] - (0.0001 * 18)',
        'lossPrice': 'short_open_price[0] + (0.0001 * 12)',
        'trailingPrice': '',
    },
    'other': {
        'allowInversion': False,
        'priority': 'Long',
    },
    'featureManifest': {
        'indicators': GUEST_DEMO_INDICATORS,
    },
}


def _guest_demo_workspace_state():
    return {
        'chartSettings': {
            'symbol': 'EURUSD',
            'timeframe': 'M5',
            'bars': 1000,
            'indicators': GUEST_DEMO_INDICATORS,
            'precision': 5,
        },
        'strategy': GUEST_DEMO_STRATEGY,
        'backtestStrategySet': [],
        'backtest': {
            'initialBalance': 10000,
            'assetType': 'forex',
            'initialVolume': 0.1,
            'pipSize': 0.0001,
            'pipValuePerLot': 10.0,
            'costProfile': 'oanda',
            'spreadInPips': 1.0,
            'slippageInPips': 0.2,
            'entrySlippageInPips': 0.2,
            'closeSlippageInPips': 0.2,
            'takeProfitSlippageInPips': 0.0,
            'stopLossSlippageInPips': 0.4,
            'trailingStopSlippageInPips': 0.5,
            'minimumStopDistanceInPips': 0.0,
            'volatilitySlippageMultiplier': 0.0,
            'executionMode': 'next_bar_open',
            'portfolioMode': 'parallel_sleeves',
            'symbol': 'EURUSD',
            'timeframe': 'M5',
            'historyScopeMode': 'loaded_chart',
            'historyScopeBars': None,
        },
        'trade': {
            'mode': 'parallel_sleeves',
            'executionMode': 'paper',
            'sameSymbolExecutionPolicy': 'independent',
            'status': 'draft',
            'selectedTab': 'runtime',
            'autoArmOnSave': False,
            'latencyBudgetMs': 150,
            'signalValiditySeconds': 10,
            'sleeves': [
                {
                    'id': 'guest-demo-sleeve-eurusd-m5',
                    'label': 'Guest demo EMA/RSI sleeve',
                    'enabled': True,
                    'symbol': 'EURUSD',
                    'timeframe': 'M5',
                    'volume': 0.01,
                    'strategy': GUEST_DEMO_STRATEGY,
                    'indicators': GUEST_DEMO_INDICATORS,
                    'sourceStrategyId': 'guest-demo-ema-rsi',
                    'strategyName': 'Guest demo EMA/RSI',
                },
            ],
            'runtime': {
                'armed': False,
                'live': False,
                'health': 'idle',
                'lastEventAt': None,
                'bridgeOnline': None,
                'lastError': '',
            },
            'audit': {
                'events': [],
            },
            'historyFilters': {
                'rangeKey': '7d',
                'customDays': 7,
                'strategyFilter': '',
                'symbolFilter': '',
                'statusFilter': 'all',
            },
        },
        'batch': {
            'features': [],
            'jobs': [],
            'options': {
                'barsOverride': None,
                'researchMode': 'none',
                'studyWindowsCsv': '',
                'studyTimeframesCsv': '',
                'studySymbolsCsv': '',
                'walkforwardTrainBars': '',
                'walkforwardTestBars': '',
                'comparisonPresetSelectionMap': {},
                'activeTemplateId': '',
            },
        },
        'research': {
            'paperShortlist': [],
            'decisionLog': [
                {
                    'id': 'guest-demo-access-policy',
                    'timestamp': time.strftime('%Y-%m-%d'),
                    'title': 'Guest demo safety policy',
                    'detail': 'This workspace is isolated from the owner profile. Heavy research, neural, and trade-runtime actions are disabled for guest sessions.',
                    'status': 'active',
                },
            ],
            'savedStudies': {},
            'studyRuns': [],
            'benchmarkStrategies': [],
        },
        'drawings': [],
        'visibleIndicatorColumns': {},
        'strategyResponse': None,
        'backtestRunResponse': None,
        'backtestChartBuffer': None,
        'chartBacktestOverlay': None,
        'uiState': {
            'chart': {
                'metaFontSize': 0.84,
                'pendingLineColor': '#d9d9d9',
                'scrollChartToEndOnTickIncoming': True,
                'showVolumePanel': True,
                'volumeMode': 'volume',
                'tradeMarkerMode': 'trader',
            },
            'consoleJobs': {
                'backtest': None,
                'batch': None,
                'presetCompare': None,
                'timeframeStudy': None,
                'symbolStudy': None,
                'walkforwardStudy': None,
            },
        },
    }


def build_guest_workspace_state():
    return copy.deepcopy(_guest_demo_workspace_state())


def build_guest_workspace_snapshot(workspace_id: str = GUEST_WORKSPACE_ID):
    safe_workspace_id = str(workspace_id or GUEST_WORKSPACE_ID).strip() or GUEST_WORKSPACE_ID
    return {
        'user_id': 'guest-temporary',
        'workspace_id': safe_workspace_id,
        'revision': 0,
        'state': build_guest_workspace_state(),
        'last_saved_at': None,
        'last_broadcast_at': None,
        'last_error': None,
        'channel': None,
        'server_time': time.time(),
        'temporary': True,
    }


def _ensure_guest_strategy_benchmarks(user_id: str, workspace_id: str):
    label = 'Guest demo EMA/RSI'
    existing = [
        item
        for item in list_workspace_strategy_benchmarks(user_id, workspace_id, limit=500)
        if str(item.get('label') or '').strip().lower() == label.lower()
    ]

    if existing and all(item.get('strategy') == GUEST_DEMO_STRATEGY for item in existing):
        return False

    for item in existing:
        delete_workspace_strategy_benchmark(user_id, workspace_id, int(item['id']))

    create_workspace_strategy_benchmark(
        user_id,
        workspace_id,
        label=label,
        side='both',
        source='guest_seed',
        notes='Safe demo strategy saved for recruiter guest sessions. Runtime execution is blocked by guest policy.',
        is_favorite=True,
        strategy=GUEST_DEMO_STRATEGY,
        strategies=[],
    )
    return True


def _ensure_guest_research_paper(user_id: str, workspace_id: str):
    project_key = 'guest-demo-access-policy'
    existing_keys = {
        str(item.get('project_key') or '').strip()
        for item in list_workspace_research_papers(user_id, workspace_id, limit=500)
    }
    if project_key in existing_keys:
        return False

    create_workspace_research_paper(
        user_id,
        workspace_id,
        project_key=project_key,
        title='Guest Demo Access Policy',
        status='published',
        discipline='platform',
        symbol='EURUSD',
        timeframe='M5',
        summary='Recruiter-friendly demo workspace with safe read-only access to heavy operations.',
        article={
            'abstract': 'This guest workspace demonstrates the Robotineeko console without exposing the owner workspace or allowing expensive runtime actions.',
            'keywords': ['guest', 'demo', 'recruiter', 'safety'],
            'mandate': {
                'objective': 'Show product surface and saved research artifacts safely.',
                'strategy_specification': 'Use a lightweight EMA/RSI demo strategy as a visible saved artifact.',
                'target_parameters': 'EURUSD M5, 1000 bars, paper-only runtime state.',
                'acceptance_criteria': 'Guest can browse but cannot start trade runtime, neural jobs, research batches, or heavy backtests.',
            },
            'sections': [
                {
                    'id': 'what-is-enabled',
                    'title': 'What is enabled',
                    'content': 'Guest sessions can view charts, documentation, saved strategy examples, and the trader monitor surface.',
                },
                {
                    'id': 'what-is-blocked',
                    'title': 'What is blocked',
                    'content': 'Trade runtime commands, live dispatch, manual evaluation, research queues, neural training, neural testing, and heavyweight backtest jobs are blocked by backend policy.',
                },
            ],
            'feature_analysis': [],
            'experimental_log': [],
        },
        reuse_existing_project_key=True,
    )
    return True


def ensure_guest_workspace(user_id: str, workspace_id: str = GUEST_WORKSPACE_ID):
    safe_user_id = str(user_id or '').strip()
    safe_workspace_id = str(workspace_id or GUEST_WORKSPACE_ID).strip() or GUEST_WORKSPACE_ID

    if not safe_user_id:
        return {
            'seeded_state': False,
            'seeded_strategy': False,
            'seeded_paper': False,
        }

    seeded_strategy = _ensure_guest_strategy_benchmarks(safe_user_id, safe_workspace_id)

    return {
        'temporary_state': True,
        'seeded_strategy': seeded_strategy,
        'seeded_paper': False,
    }
