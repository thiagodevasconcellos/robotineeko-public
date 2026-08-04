import unittest
from unittest.mock import patch
import math
import time

from backend.python.app_state import state
from backend.python.bridge import build_service_health_payload
from backend.python.services.trade_runtime_service import (
    _build_order_intent,
    _build_order_command,
    _build_sleeve_magic,
    _build_sleeve_reconciliation_state,
    _ensure_trade_market_data,
    _get_default_trade_bars,
    _is_intent_expired,
    _normalize_live_decision_against_broker,
    acknowledge_trade_order_command,
    auto_process_trade_order_intents_if_needed,
    arm_trade_runtime,
    arm_trade_live_dispatch,
    build_trade_runtime_payload,
    claim_next_trade_order_command,
    configure_trade_runtime,
    disarm_trade_live_dispatch,
    disarm_trade_runtime,
    evaluate_trade_runtime,
    finalize_trade_order_command,
    note_trade_bridge_event,
    note_trade_bridge_heartbeat,
    note_trade_market_update,
    process_trade_order_intents,
    reconcile_trade_runtime_commands,
    reset_trade_runtime_commands,
)
from backend.python.services.workspace_store import get_workspace_live_trade_by_command_id


class TradeRuntimeServiceTest(unittest.TestCase):
    def setUp(self):
        state.trade.mode = 'parallel_sleeves'
        state.trade.execution_mode = 'paper'
        state.trade.portfolio_structure_version = 1
        state.trade.status = 'idle'
        state.trade.armed = False
        state.trade.live_dispatch_armed = False
        state.trade.live = False
        state.trade.enforce_symbol_isolation = False
        state.trade.latency_budget_ms = 150
        state.trade.portfolios = []
        state.trade.sleeves = []
        state.trade.active_symbols = []
        state.trade.order_intents = []
        state.trade.order_commands = []
        state.trade.audit_events = []
        state.trade.latency_events = []
        state.trade.metrics = {
            'event_count': 0,
            'decision_count': 0,
            'dispatch_count': 0,
            'ack_count': 0,
            'fill_count': 0,
            'command_count': 0,
            'command_ack_count': 0,
            'command_fill_count': 0,
            'command_reject_count': 0,
            'last_latency_ms': None,
            'max_latency_ms': None,
        }
        state.trade.last_configured_at = None
        state.trade.last_armed_at = None
        state.trade.last_live_dispatch_armed_at = None
        state.trade.last_live_dispatch_disarmed_at = None
        state.trade.last_disarmed_at = None
        state.trade.last_event_at = None
        state.trade.market_feed_status = 'idle'
        state.trade.market_feed_issue = None
        state.trade.last_market_sanitize_at = None
        state.trade.bridge_online = False
        state.trade.bridge_last_status = None
        state.trade.bridge_last_message = None
        state.trade.bridge_last_request_id = None
        state.trade.bridge_last_heartbeat_at = None
        state.trade.bridge_timeout_seconds = 8.0
        state.trade.broker_account_position_mode = None
        state.trade.broker_account_hedge_allowed = None
        state.trade.market_history_ready = False
        state.trade.market_last_update_at = None
        state.trade.market_latest_candle_time = None
        state.trade.last_market_event_stage = None
        state.trade.last_market_event_new_candle = False
        state.trade.last_market_event_candle_time = None
        state.trade.broker_profile_id = ''
        state.trade.broker_profile_label = ''
        state.trade.broker_positions = []
        state.trade.last_broker_positions_at = None
        state.trade.broker_symbol_rules = {}
        state.trade.trade_cycle_sequence = 0
        state.trade.last_error = None
        state.workspace.active_user_id = 'trade-test-user'
        state.workspace.active_workspace_id = 'trade-test-workspace'
        state.bridge.ea_last_status = None
        state.bridge.ea_last_message = None
        state.bridge.ea_account_position_mode = None
        state.bridge.ea_account_hedge_allowed = None
        state.bridge.ea_last_heartbeat_at = None
        state.bridge.ea_timeout_seconds = 8.0
        state.bridge.history_ready = False
        state.market.last_update_at = None
        state.market.latest_candle_time = None
        if not isinstance(getattr(state.chart, 'request', None), dict):
            state.chart.request = {}
        state.chart.request['bars'] = 1000

    def _prime_healthy_market_feed(self, now=None):
        safe_now = float(now or time.time())
        state.trade.market_history_ready = True
        state.trade.bridge_online = True
        state.trade.bridge_last_heartbeat_at = safe_now
        state.trade.market_last_update_at = safe_now
        state.trade.market_latest_candle_time = int(safe_now)
        state.bridge.history_ready = True
        state.bridge.ea_last_heartbeat_at = safe_now
        state.market.last_update_at = safe_now
        state.market.latest_candle_time = int(safe_now)
        return safe_now

    def test_configure_trade_runtime_normalizes_mode_and_sleeves(self):
        payload = configure_trade_runtime({
            'mode': 'parallel_sleeves',
            'latencyBudgetMs': 90,
            'signalValiditySeconds': 12,
            'sameSymbolExecutionPolicy': 'block_conflicts',
            'sleeves': [
                {
                    'id': 'deep',
                    'label': 'Deep sleeve',
                    'symbol': 'eurusd',
                    'timeframe': 'm1',
                    'enabled': True,
                },
                {
                    'id': 'band',
                    'label': 'Band sleeve',
                    'symbol': 'gbpusd',
                    'timeframe': 'm5',
                    'enabled': False,
                },
            ],
        })

        self.assertEqual(payload['mode'], 'parallel_sleeves')
        self.assertEqual(payload['latency_budget_ms'], 90)
        self.assertEqual(payload['signal_validity_seconds'], 12)
        self.assertEqual(payload['same_symbol_execution_policy'], 'block_conflicts')
        self.assertEqual(len(payload['sleeves']), 2)
        self.assertEqual(payload['sleeves'][0]['symbol'], 'EURUSD')
        self.assertEqual(payload['active_symbols'], ['EURUSD'])
        self.assertEqual(payload['status'], 'configured')
        self.assertEqual(payload['audit_events'][0]['kind'], 'configure')

    def test_configure_trade_runtime_builds_implicit_legacy_portfolio_from_top_level_sleeves(self):
        payload = configure_trade_runtime({
            'mode': 'shared_pipe',
            'sleeves': [
                {
                    'id': 's1',
                    'label': 'Primary',
                    'symbol': 'eurusd',
                    'timeframe': 'm15',
                    'sourceStrategyId': 'alpha',
                },
            ],
        })

        self.assertEqual(payload['portfolio_structure_version'], 1)
        self.assertEqual(payload['portfolios'][0]['id'], 'legacy-default')
        self.assertEqual(payload['portfolios'][0]['pipelines'][0]['portfolio_mode'], 'shared_pipe')
        self.assertEqual(payload['portfolios'][0]['pipelines'][0]['sleeves'][0]['pipeline_id'], 'legacy-pipeline')
        self.assertEqual(payload['sleeves'][0]['portfolio_id'], 'legacy-default')
        self.assertEqual(payload['sleeves'][0]['pipeline_id'], 'legacy-pipeline')

    def test_configure_trade_runtime_carries_broker_profile_scope(self):
        payload = configure_trade_runtime({
            'executionMode': 'paper',
            'brokerProfileId': '17',
            'brokerProfileLabel': 'FOREX.com Prime',
            'sleeves': [
                {
                    'id': 's1',
                    'label': 'Scoped sleeve',
                    'symbol': 'eurusd',
                    'timeframe': 'm15',
                },
            ],
        })

        self.assertEqual(payload['broker_profile_id'], '17')
        self.assertEqual(payload['broker_profile_label'], 'FOREX.com Prime')
        self.assertEqual(state.trade.broker_profile_id, '17')
        self.assertEqual(state.trade.broker_profile_label, 'FOREX.com Prime')

    @patch.dict('backend.python.services.trade_runtime_service.FEATURE_FLAGS', {'trader_portfolios_v2': True}, clear=False)
    def test_configure_trade_runtime_compiles_explicit_portfolios_and_tags_sleeves(self):
        payload = configure_trade_runtime({
            'mode': 'parallel_sleeves',
            'portfolioStructureVersion': 2,
            'portfolios': [
                {
                    'id': 'p1',
                    'label': 'Portfolio 1',
                    'pipelines': [
                        {
                            'id': 'lon',
                            'label': 'London',
                            'portfolioMode': 'shared_pipe',
                            'sleeves': [
                                {
                                    'id': 's1',
                                    'label': 'Fixed',
                                    'symbol': 'EURUSD',
                                    'timeframe': 'M15',
                                    'fixedVolume': 0.03,
                                    'volumeMode': 'fixed_volume',
                                },
                                {
                                    'id': 's2',
                                    'label': 'Compound',
                                    'symbol': 'EURUSD',
                                    'timeframe': 'M15',
                                    'baseVolume': 0.02,
                                    'volumeMode': 'base_volume_compounding',
                                },
                            ],
                        },
                    ],
                },
            ],
        })

        self.assertEqual(payload['portfolio_structure_version'], 2)
        self.assertEqual(len(payload['portfolios']), 1)
        self.assertEqual(payload['portfolios'][0]['pipelines'][0]['portfolio_mode'], 'shared_pipe')
        self.assertEqual(payload['sleeves'][0]['portfolio_id'], 'p1')
        self.assertEqual(payload['sleeves'][0]['pipeline_id'], 'lon')
        self.assertEqual(payload['sleeves'][0]['portfolio_mode'], 'shared_pipe')
        self.assertEqual(payload['sleeves'][1]['volume_mode'], 'base_volume_compounding')
        self.assertTrue(payload['sleeves'][1]['legacy_volume_fallback_applied'])

    def test_arm_and_disarm_trade_runtime_updates_status(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'symbol': 'EURUSD'}]})
        armed = arm_trade_runtime()
        self.assertTrue(armed['armed'])
        self.assertEqual(armed['status'], 'armed')

        disarmed = disarm_trade_runtime()
        self.assertFalse(disarmed['armed'])
        self.assertEqual(disarmed['status'], 'idle')
        self.assertEqual(disarmed['audit_events'][0]['kind'], 'disarm')

    def test_disarm_trade_runtime_clears_active_live_dispatch_queue(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'symbol': 'EURUSD'}]})
        state.trade.order_commands = [
            {'id': 'cmd_active', 'status': 'queued', 'sleeve_id': 's1'},
            {'id': 'cmd_done', 'status': 'filled', 'sleeve_id': 's1'},
        ]
        state.trade.order_intents = [
            {'id': 'oi_active', 'status': 'broker_queued', 'command_id': 'cmd_active', 'sleeve_id': 's1'},
            {'id': 'oi_done', 'status': 'filled', 'command_id': 'cmd_done', 'sleeve_id': 's1'},
        ]
        state.trade.sleeve_states = {
            's1': {
                'sleeve_id': 's1',
                'pending_action': 'open_long',
                'pending_cycle_id': 'cycle_1',
                'current_cycle_id': None,
            },
        }

        payload = disarm_trade_runtime()
        self.assertEqual(len(payload['order_commands']), 1)
        self.assertEqual(payload['order_commands'][0]['id'], 'cmd_done')
        self.assertEqual(payload['order_intents'][0]['status'], 'stale')
        self.assertEqual(payload['order_intents'][1]['status'], 'filled')
        self.assertIsNone(payload['sleeve_states']['s1']['pending_action'])
        self.assertIsNone(payload['sleeve_states']['s1']['pending_cycle_id'])

    def test_disarm_live_dispatch_clears_active_live_dispatch_queue(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'symbol': 'EURUSD'}]})
        state.trade.order_commands = [{'id': 'cmd_active', 'status': 'acknowledged', 'sleeve_id': 's1'}]
        state.trade.order_intents = [{'id': 'oi_active', 'status': 'broker_acknowledged', 'command_id': 'cmd_active', 'sleeve_id': 's1'}]

        payload = disarm_trade_live_dispatch()
        self.assertFalse(payload['live_dispatch_armed'])
        self.assertEqual(payload['order_commands'], [])
        self.assertEqual(payload['order_intents'][0]['status'], 'stale')

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_arm_trade_runtime_evaluates_immediately_when_market_is_ready(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'request_status': 'ready',
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05, 'volume': 100},
                {'time': 2, 'open': 1.05, 'high': 1.2, 'low': 1.0, 'close': 1.1, 'volume': 120},
            ],
            'last_update_at': 10.0,
            'latest_candle_time': 2,
        }
        configure_trade_runtime({
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {'openIf': 'False', 'closeIf': 'False', 'openPrice': 'close[0]', 'closePrice': 'close[0]'},
                    'short': {'openIf': 'False', 'closeIf': 'False', 'openPrice': 'close[0]', 'closePrice': 'close[0]'},
                    'other': {'allowInversion': False, 'priority': 'Short'},
                },
            }],
        })
        state.trade.market_history_ready = True

        armed = arm_trade_runtime()

        sleeve_state = armed['sleeve_states']['s1']
        self.assertTrue(armed['armed'])
        self.assertEqual(sleeve_state['status'], 'ready')
        self.assertIsNotNone(sleeve_state['last_evaluated_at'])

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_arm_trade_runtime_rehydrates_stale_market_feed_before_guarding(self, ensure_market_data_mock):
        now = __import__('time').time()
        ensure_market_data_mock.return_value = {
            'ready': True,
            'request_status': 'ready',
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05, 'volume': 100},
                {'time': 2, 'open': 1.05, 'high': 1.2, 'low': 1.0, 'close': 1.1, 'volume': 120},
            ],
            'last_update_at': now,
            'latest_candle_time': int(now),
        }
        configure_trade_runtime({
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {'openIf': 'False', 'closeIf': 'False', 'openPrice': 'close[0]', 'closePrice': 'close[0]'},
                    'short': {'openIf': 'False', 'closeIf': 'False', 'openPrice': 'close[0]', 'closePrice': 'close[0]'},
                    'other': {'allowInversion': False, 'priority': 'Short'},
                },
            }],
        })
        state.trade.market_history_ready = True
        state.trade.market_last_update_at = 1.0
        state.trade.market_latest_candle_time = 1
        state.trade.market_snapshot_symbol = 'EURUSD'
        state.trade.market_snapshot_timeframe = 'M1'
        state.trade.market_snapshot_bars = 2
        state.trade.market_snapshot_candles = [
            {'time': 1, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05, 'volume': 100},
            {'time': 2, 'open': 1.05, 'high': 1.2, 'low': 1.0, 'close': 1.1, 'volume': 120},
        ]
        state.bridge.history_ready = True
        state.bridge.ea_last_heartbeat_at = now

        armed = arm_trade_runtime()

        self.assertTrue(armed['armed'])
        self.assertEqual(armed['status'], 'armed')
        self.assertEqual(armed['market_feed']['status'], 'healthy')
        self.assertEqual(ensure_market_data_mock.call_count, 1)

    def test_configure_trade_runtime_preserves_market_snapshot_state(self):
        state.trade.market_history_ready = True
        state.trade.market_last_update_at = 123.0
        state.trade.market_latest_candle_time = 456
        state.trade.market_snapshot_symbol = 'EURUSD'
        state.trade.market_snapshot_timeframe = 'M1'
        state.trade.market_snapshot_bars = 2
        state.trade.market_snapshot_candles = [
            {'time': 1, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05, 'volume': 100},
            {'time': 2, 'open': 1.05, 'high': 1.2, 'low': 1.0, 'close': 1.1, 'volume': 120},
        ]

        configure_trade_runtime({'sleeves': [{'id': 's1', 'symbol': 'EURUSD'}]})

        self.assertTrue(state.trade.market_history_ready)
        self.assertEqual(state.trade.market_last_update_at, 123.0)
        self.assertEqual(state.trade.market_latest_candle_time, 456)
        self.assertEqual(state.trade.market_snapshot_symbol, 'EURUSD')
        self.assertEqual(state.trade.market_snapshot_timeframe, 'M1')
        self.assertEqual(state.trade.market_snapshot_bars, 2)
        self.assertEqual(len(state.trade.market_snapshot_candles), 2)

    @patch('backend.python.services.trade_runtime_service._request_backend_market_snapshot')
    def test_isolated_mode_falls_back_when_cached_snapshot_is_stale(self, request_backend_market_snapshot_mock):
        stale_time = time.time() - 3600.0
        request_backend_market_snapshot_mock.return_value = {
            'ready': True,
            'request_status': 'ready',
            'candles': [
                {'time': time.time() - 60.0, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05, 'volume': 100},
                {'time': time.time(), 'open': 1.05, 'high': 1.2, 'low': 1.0, 'close': 1.1, 'volume': 120},
            ],
            'last_update_at': time.time(),
            'latest_candle_time': time.time(),
        }
        state.trade.market_snapshot_symbol = 'GBPUSD'
        state.trade.market_snapshot_timeframe = 'M1'
        state.trade.market_snapshot_candles = [
            {'time': stale_time - 60.0, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05, 'volume': 100},
            {'time': stale_time, 'open': 1.05, 'high': 1.2, 'low': 1.0, 'close': 1.1, 'volume': 120},
        ]

        with patch.dict('os.environ', {'ROBOTINEEKO_TRADE_SERVICE_ISOLATED': '1'}):
            context = _ensure_trade_market_data('GBPUSD', 'M1', 1000)

        request_backend_market_snapshot_mock.assert_called_once_with('GBPUSD', 'M1', 1000)
        self.assertTrue(context['ready'])
        self.assertGreater(context['candles'][-1]['time'], stale_time)

    def test_live_intent_expiration_uses_wall_clock_instead_of_market_bar_time(self):
        state.trade.execution_mode = 'live_mt5'
        state.trade.signal_validity_seconds = 10
        state.trade.market_latest_candle_time = 5_000.0

        created_at = time.time()
        expired = _is_intent_expired({
            'execution_mode': 'live_mt5',
            'decision': 'open_long',
            'bar_time': 4_920.0,
            'created_at': created_at,
            'timeframe': 'M1',
        })

        self.assertFalse(expired)

    def test_non_live_intent_expiration_still_uses_market_bar_time(self):
        state.trade.execution_mode = 'paper'
        state.trade.signal_validity_seconds = 10
        state.trade.market_latest_candle_time = 5_000.0

        expired = _is_intent_expired({
            'execution_mode': 'paper',
            'decision': 'open_long',
            'bar_time': 4_920.0,
            'created_at': time.time(),
            'timeframe': 'M1',
        })

        self.assertTrue(expired)

    def test_live_dispatch_requires_explicit_gate(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        now = self._prime_healthy_market_feed()
        state.trade.order_intents = [{
            'id': 'oi_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]
        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'dispatch_blocked')
        self.assertEqual(len(payload['order_commands']), 0)

        arm_trade_live_dispatch()
        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'broker_queued')
        self.assertEqual(len(payload['order_commands']), 1)

        disarm_trade_live_dispatch()
        payload = build_trade_runtime_payload()
        self.assertFalse(payload['live_dispatch_armed'])

    def test_health_payload_reports_trade_check(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'symbol': 'EURUSD'}]})
        arm_trade_runtime()

        degraded = build_service_health_payload()
        self.assertIn('trade', degraded['checks'])
        self.assertFalse(degraded['checks']['trade']['ok'])

        state.bridge.ea_last_heartbeat_at = __import__('time').time()
        ready = build_service_health_payload()
        self.assertTrue(ready['checks']['trade']['ok'])

    def test_build_trade_runtime_payload_exposes_runtime_shape(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'label': 'One'}]})
        payload = build_trade_runtime_payload()
        self.assertIn('metrics', payload)
        self.assertIn('audit_events', payload)
        self.assertIn('latency_events', payload)
        self.assertEqual(payload['sleeves'][0]['label'], 'One')

    def test_build_trade_runtime_payload_sanitizes_non_finite_values(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'label': 'One'}]})
        state.trade.sleeve_states = {
            's1': {
                'last_price': float('nan'),
                'expected_exit_price': float('inf'),
            },
        }
        state.trade.metrics['last_latency_ms'] = float('nan')
        state.trade.order_intents = [{
            'id': 'oi_1',
            'latency_ms': float('inf'),
        }]
        state.trade.order_commands = [{
            'id': 'oc_1',
            'created_at': 1.0,
            'status': 'queued',
            'age_seconds': math.nan,
        }]

        payload = build_trade_runtime_payload()

        self.assertIsNone(payload['sleeve_states']['s1']['last_price'])
        self.assertIsNone(payload['sleeve_states']['s1']['expected_exit_price'])
        self.assertIsNone(payload['metrics']['last_latency_ms'])
        self.assertIsNone(payload['order_intents'][0]['latency_ms'])

    def test_bridge_heartbeat_can_promote_armed_runtime_to_live(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        note_trade_bridge_heartbeat({
            'status': 'idle',
            'message': 'heartbeat ok',
            'request_id': 'req-1',
            'account_position_mode': 'hedging',
            'account_hedge_allowed': '1',
        })
        payload = build_trade_runtime_payload()
        self.assertTrue(payload['live'])
        self.assertEqual(payload['status'], 'live')
        self.assertEqual(payload['broker_account_position_mode'], 'hedging')
        self.assertTrue(payload['broker_account_hedge_allowed'])
        self.assertEqual(payload['audit_events'][0]['kind'], 'bridge_heartbeat')

    def test_bridge_error_interrupts_armed_runtime(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        note_trade_bridge_event('request_error', {
            'level': 'error',
            'message': 'bridge failed',
            'status': 'error',
        })
        payload = build_trade_runtime_payload()
        self.assertFalse(payload['live'])
        self.assertEqual(payload['status'], 'interrupted')
        self.assertEqual(payload['last_error'], 'bridge failed')

    def test_history_request_error_does_not_interrupt_armed_runtime(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        note_trade_bridge_event('request_error', {
            'level': 'error',
            'message': 'HttpPostBytes HTTP error for http://127.0.0.1:8010/mt5/jobs/mreq_123/history status=1003',
            'status': 'error',
        })
        payload = build_trade_runtime_payload()
        self.assertTrue(payload['armed'])
        self.assertEqual(payload['status'], 'armed')
        self.assertIsNone(payload['last_error'])
        self.assertEqual(payload['audit_events'][0]['kind'], 'bridge_request_error')
        self.assertEqual(payload['audit_events'][0]['level'], 'warning')

    def test_next_job_request_error_does_not_interrupt_armed_runtime(self):
        configure_trade_runtime({'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        note_trade_bridge_event('request_error', {
            'level': 'error',
            'message': 'HttpPostFetchText HTTP error for http://127.0.0.1:8010/mt5/jobs/next status=1003',
            'status': 'error',
        })
        payload = build_trade_runtime_payload()
        self.assertTrue(payload['armed'])
        self.assertEqual(payload['status'], 'armed')
        self.assertIsNone(payload['last_error'])
        self.assertEqual(payload['audit_events'][0]['kind'], 'bridge_request_error')
        self.assertEqual(payload['audit_events'][0]['level'], 'warning')

    def test_idle_trade_command_poll_error_does_not_interrupt_armed_runtime_without_active_commands(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One'}],
        })
        arm_trade_runtime()
        note_trade_bridge_event('request_error', {
            'level': 'error',
            'message': 'HttpPostFetchText HTTP error for http://127.0.0.1:8010/mt5/trade/commands/next status=1003',
            'status': 'error',
        })
        payload = build_trade_runtime_payload()
        self.assertTrue(payload['armed'])
        self.assertEqual(payload['status'], 'armed')
        self.assertIsNone(payload['last_error'])
        self.assertEqual(payload['audit_events'][0]['kind'], 'bridge_request_error')
        self.assertEqual(payload['audit_events'][0]['level'], 'warning')

    def test_heartbeat_clears_stale_history_request_runtime_error(self):
        state.trade.last_error = 'HttpPostBytes HTTP error for http://127.0.0.1:8010/mt5/jobs/mreq_123/history status=1003'
        note_trade_bridge_heartbeat({
            'status': 'active',
            'online': True,
            'message': 'heartbeat ok',
        })
        self.assertIsNone(state.trade.last_error)

    def test_heartbeat_clears_stale_next_job_request_runtime_error(self):
        state.trade.last_error = 'HttpPostFetchText HTTP error for http://127.0.0.1:8010/mt5/jobs/next status=1003'
        note_trade_bridge_heartbeat({
            'status': 'active',
            'online': True,
            'message': 'heartbeat ok',
        })
        self.assertIsNone(state.trade.last_error)

    def test_heartbeat_clears_idle_trade_command_poll_runtime_error(self):
        state.trade.last_error = 'HttpPostFetchText HTTP error for http://127.0.0.1:8010/mt5/trade/commands/next status=1003'
        note_trade_bridge_heartbeat({
            'status': 'active',
            'online': True,
            'message': 'heartbeat ok',
        })
        self.assertIsNone(state.trade.last_error)

    def test_default_trade_bars_is_decoupled_from_large_chart_request(self):
        state.chart.request['bars'] = 100000
        self.assertEqual(_get_default_trade_bars(), 2000)

    def test_market_update_records_audit_and_latency(self):
        note_trade_market_update('history_loaded', symbol='EURUSD', timeframe='M1', candle_count=4000)
        payload = build_trade_runtime_payload()
        self.assertEqual(payload['audit_events'][0]['kind'], 'market_history_loaded')
        self.assertEqual(payload['latency_events'][0]['stage'], 'market_history_loaded')

    def test_resume_policy_queues_safety_close_for_orphan_broker_position(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        arm_trade_runtime()
        state.trade.broker_positions = [{
            'ticket': '1001',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('deep'),
            'side': 'long',
            'volume': 0.01,
        }]
        state.trade.market_history_ready = True
        state.trade.market_last_update_at = __import__('time').time()
        state.trade.market_latest_candle_time = int(__import__('time').time())
        note_trade_bridge_heartbeat({'status': 'idle', 'message': 'ok', 'online': True, 'positions': state.trade.broker_positions})

        payload = build_trade_runtime_payload()
        self.assertTrue(any(
            entry.get('trigger') == 'resume_policy' and entry.get('action') == 'close'
            for entry in payload['order_intents']
        ))

    def test_resume_policy_queues_close_for_each_orphan_multiple_position(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        arm_trade_runtime()
        state.trade.broker_positions = [
            {
                'ticket': '1101',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.01,
            },
            {
                'ticket': '1102',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.02,
            },
        ]
        state.trade.market_history_ready = True
        state.trade.market_last_update_at = __import__('time').time()
        state.trade.market_latest_candle_time = int(__import__('time').time())
        note_trade_bridge_heartbeat({'status': 'idle', 'message': 'ok', 'online': True, 'positions': state.trade.broker_positions})

        payload = build_trade_runtime_payload()
        resume_close_intents = [
            entry for entry in payload['order_intents']
            if entry.get('trigger') == 'resume_policy' and entry.get('action') == 'close'
        ]
        self.assertEqual(len(resume_close_intents), 2)
        self.assertEqual(sorted(str(entry.get('broker_ticket') or '') for entry in resume_close_intents), ['1101', '1102'])

    def test_build_order_command_prefers_broker_ticket_for_close(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        state.trade.broker_positions = [{
            'ticket': '2002',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('deep'),
            'side': 'long',
            'volume': 0.01,
        }]
        command = _build_order_command({
            'id': 'oi_test_close',
            'fingerprint': 'deep|close|long|1',
            'sleeve_id': 'deep',
            'sleeve_label': 'Deep',
            'source_strategy_id': 'Debug',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'long',
            'decision': 'stop_long_trail',
            'bar_time': 1,
        })
        self.assertEqual(command['broker_ticket'], '2002')
        self.assertEqual(command['broker_position_side'], 'long')

    def test_build_order_command_omits_open_stop_prices_in_live_mode(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        command = _build_order_command({
            'id': 'oi_test_open',
            'fingerprint': 'deep|open|long|1',
            'sleeve_id': 'deep',
            'sleeve_label': 'Deep',
            'source_strategy_id': 'Debug',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'cycle_id': 'deep-cycle-1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 1,
            'strategy_entry_price': 1.2000,
            'take_profit_price': 1.205,
            'stop_loss_price': 1.195,
        })
        self.assertIsNone(command['take_profit_price'])
        self.assertIsNone(command['stop_loss_price'])
        self.assertAlmostEqual(command['strategy_entry_price'], 1.2000)
        self.assertEqual(command['cycle_id'], 'deep-cycle-1')

    def test_build_order_command_carries_exit_reason_context(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        command = _build_order_command({
            'id': 'oi_test_close',
            'fingerprint': 'deep|close|long|2',
            'sleeve_id': 'deep',
            'sleeve_label': 'Deep',
            'source_strategy_id': 'Debug',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'cycle_id': 'deep-cycle-1',
            'action': 'close',
            'side': 'long',
            'decision': 'stop_long_trail',
            'exit_reason': 'trail',
            'expected_exit_price': 1.204,
            'bar_time': 2,
        })
        self.assertEqual(command['exit_reason'], 'trail')
        self.assertAlmostEqual(command['expected_exit_price'], 1.204)

    def test_build_order_intent_keeps_protective_exits_in_live_mode(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M15'}],
        })
        intent = _build_order_intent(
            'deep',
            {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M15'},
            {
                'decision': 'stop_short_loss',
                'symbol': 'EURUSD',
                'timeframe': 'M15',
                'last_bar_time': 123,
                'short_stop_loss_price': 1.2050,
            },
            'manual',
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent['action'], 'close')
        self.assertEqual(intent['side'], 'short')
        self.assertEqual(intent['exit_reason'], 'loss')

    def test_build_sleeve_reconciliation_state_reports_match_open(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = [{
            'ticket': '3001',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('deep'),
            'side': 'long',
            'volume': 0.01,
        }]
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 1})
        self.assertEqual(reconciliation['status'], 'match_open')
        self.assertEqual(reconciliation['actual_side'], 'long')
        self.assertFalse(reconciliation['should_queue_close'])

    def test_build_sleeve_reconciliation_state_reports_missing_broker_position(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = []
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 1})
        self.assertEqual(reconciliation['status'], 'missing_broker_position')
        self.assertEqual(reconciliation['actual_side'], 'flat')
        self.assertFalse(reconciliation['should_queue_close'])

    def test_build_sleeve_reconciliation_state_reports_orphan_broker_position(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = [{
            'ticket': '3002',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('deep'),
            'side': 'short',
            'volume': 0.01,
        }]
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 0})
        self.assertEqual(reconciliation['status'], 'orphan_broker_position')
        self.assertEqual(reconciliation['actual_side'], 'short')
        self.assertTrue(reconciliation['should_queue_close'])

    def test_build_sleeve_reconciliation_state_reports_conflicting_position(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = [{
            'ticket': '3003',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('deep'),
            'side': 'short',
            'volume': 0.01,
        }]
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 1})
        self.assertEqual(reconciliation['status'], 'conflicting_broker_position')
        self.assertEqual(reconciliation['desired_side'], 'long')
        self.assertEqual(reconciliation['actual_side'], 'short')
        self.assertTrue(reconciliation['should_queue_close'])

    def test_normalize_live_decision_rebases_prices_and_triggers_loss_from_fill_anchor(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        state.trade.market_snapshot_symbol = 'EURUSD'
        state.trade.market_snapshot_timeframe = 'M1'
        state.trade.market_snapshot_candles = [{
            'time': 11,
            'open': 0.95,
            'high': 0.99,
            'low': 0.86,
            'close': 0.90,
        }]
        state.trade.broker_positions = [{
            'ticket': '7001',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('s1'),
            'side': 'long',
            'volume': 0.01,
        }]

        decision = _normalize_live_decision_against_broker(
            {'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'},
            {
                'status': 'ready',
                'decision': 'hold',
                'position': 1,
                'strategy_position': 1,
                'pending_action': None,
                'order_type': None,
                'bar_time': 11,
                'long_open_price': 1.00,
                'long_take_profit_price': 1.10,
                'long_stop_loss_price': 0.90,
            },
            {
                'live_entry_fill_price': 0.97,
                'strategy_entry_price': 1.00,
                'live_entry_side': 'long',
                'live_entry_bar_time': 10,
                'broker_position_side': 'long',
                'actual_position_side': 'long',
            },
        )

        self.assertEqual(decision['decision'], 'stop_long_loss')
        self.assertAlmostEqual(decision['long_take_profit_price'], 1.07)
        self.assertAlmostEqual(decision['long_stop_loss_price'], 0.87)

    def test_normalize_live_decision_suppresses_theoretical_stop_when_rebased_level_not_hit(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        state.trade.market_snapshot_symbol = 'EURUSD'
        state.trade.market_snapshot_timeframe = 'M1'
        state.trade.market_snapshot_candles = [{
            'time': 11,
            'open': 0.95,
            'high': 0.96,
            'low': 0.89,
            'close': 0.91,
        }]
        state.trade.broker_positions = [{
            'ticket': '7001',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('s1'),
            'side': 'long',
            'volume': 0.01,
        }]

        decision = _normalize_live_decision_against_broker(
            {'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'},
            {
                'status': 'ready',
                'decision': 'stop_long_loss',
                'position': 0,
                'strategy_position': 0,
                'pending_action': None,
                'order_type': 'stop_long_loss',
                'bar_time': 11,
                'long_open_price': 1.00,
                'long_take_profit_price': 1.10,
                'long_stop_loss_price': 0.90,
            },
            {
                'live_entry_fill_price': 0.97,
                'strategy_entry_price': 1.00,
                'live_entry_side': 'long',
                'live_entry_bar_time': 10,
                'broker_position_side': 'long',
                'actual_position_side': 'long',
            },
        )

        self.assertEqual(decision['decision'], 'hold')
        self.assertEqual(decision['strategy_position'], 1)
        self.assertAlmostEqual(decision['long_stop_loss_price'], 0.87)

    def test_build_sleeve_reconciliation_state_reports_match_open_multiple(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = [
            {
                'ticket': '3010',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.01,
            },
            {
                'ticket': '3011',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.02,
            },
        ]
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 1})
        self.assertEqual(reconciliation['status'], 'match_open_multiple')
        self.assertEqual(reconciliation['actual_side'], 'long')
        self.assertEqual(reconciliation['broker_position_count'], 2)
        self.assertEqual(reconciliation['broker_tickets'], ['3010', '3011'])
        self.assertFalse(reconciliation['should_queue_close'])

    def test_build_sleeve_reconciliation_state_reports_orphan_multiple_positions(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = [
            {
                'ticket': '3020',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'short',
                'volume': 0.01,
            },
            {
                'ticket': '3021',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'short',
                'volume': 0.01,
            },
        ]
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 0})
        self.assertEqual(reconciliation['status'], 'orphan_multiple_positions')
        self.assertEqual(reconciliation['actual_side'], 'short')
        self.assertTrue(reconciliation['should_queue_close'])
        self.assertEqual(
            [str(item.get('ticket') or '').strip() for item in reconciliation['close_targets']],
            ['3020', '3021'],
        )

    def test_build_sleeve_reconciliation_state_reports_conflicting_multiple_positions(self):
        sleeve = {'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD'}
        state.trade.broker_positions = [
            {
                'ticket': '3030',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.01,
            },
            {
                'ticket': '3031',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'short',
                'volume': 0.01,
            },
        ]
        reconciliation = _build_sleeve_reconciliation_state(sleeve, {'position': 1})
        self.assertEqual(reconciliation['status'], 'conflicting_multiple_positions')
        self.assertEqual(reconciliation['actual_side'], 'multiple')
        self.assertTrue(reconciliation['should_queue_close'])
        self.assertEqual(
            [str(item.get('ticket') or '').strip() for item in reconciliation['close_targets']],
            ['3031'],
        )

    def test_build_order_command_selects_deterministic_ticket_when_multiple_broker_positions_match_side(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 'deep', 'label': 'Deep', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        state.trade.broker_positions = [
            {
                'ticket': '3040',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.01,
            },
            {
                'ticket': '3041',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('deep'),
                'side': 'long',
                'volume': 0.01,
            },
        ]
        command = _build_order_command({
            'id': 'oi_test_close_multi',
            'fingerprint': 'deep|close|long|2|3040',
            'sleeve_id': 'deep',
            'sleeve_label': 'Deep',
            'source_strategy_id': 'Debug',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'cycle_id': 'deep-cycle-2',
            'action': 'close',
            'side': 'long',
            'decision': 'stop_long_loss',
            'bar_time': 2,
        })
        self.assertEqual(command['broker_ticket'], '3040')
        self.assertEqual(command['broker_position_side'], 'long')

    def test_stale_market_feed_keeps_trade_runtime_armed(self):
        configure_trade_runtime({
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        arm_trade_runtime()
        thursday_now = 1780059600.0
        state.bridge.history_ready = True
        state.bridge.ea_last_heartbeat_at = thursday_now
        state.market.last_update_at = thursday_now - 180.0
        state.market.latest_candle_time = int(thursday_now - 180.0)

        with patch('backend.python.services.trade_runtime_service.time.time', return_value=thursday_now):
            payload = build_trade_runtime_payload()

        self.assertTrue(payload['armed'])
        self.assertEqual(payload['status'], 'market_feed_stale')
        self.assertEqual(payload['market_feed']['status'], 'stale')
        self.assertFalse(payload['market_feed']['auto_sanitized'])
        self.assertFalse(payload['live'])
        self.assertIsNone(payload['last_error'])

    def test_weekend_market_feed_pause_reports_closed_without_disarming(self):
        configure_trade_runtime({
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        arm_trade_runtime()
        saturday_now = 1780119157.0
        state.bridge.history_ready = True
        state.bridge.ea_last_heartbeat_at = saturday_now
        state.market.last_update_at = 1780099017.806475
        state.market.latest_candle_time = 1780099140

        with patch('backend.python.services.trade_runtime_service.time.time', return_value=saturday_now):
            payload = build_trade_runtime_payload()

        self.assertTrue(payload['armed'])
        self.assertEqual(payload['status'], 'market_feed_stale')
        self.assertEqual(payload['market_feed']['status'], 'closed')
        self.assertIn('weekly close', payload['market_feed']['detail'])
        self.assertFalse(payload['market_feed']['auto_sanitized'])
        self.assertIsNone(payload['last_error'])

    def test_stale_market_feed_holds_live_intents_until_feed_recovers(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = __import__('time').time()
        state.bridge.history_ready = True
        state.bridge.ea_last_heartbeat_at = now
        state.trade.market_history_ready = True
        state.market.last_update_at = now - 180.0
        state.market.latest_candle_time = int(now - 180.0)
        state.trade.sleeve_states['s1'].update({
            'decision': 'open_long',
            'last_bar_time': int(now - 60.0),
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'long_take_profit_price': 1.2000,
            'long_stop_loss_price': 0.9000,
            'long_open_price': 1.0000,
        })
        state.trade.order_intents = [
            _build_order_intent('s1', state.trade.sleeves[0], state.trade.sleeve_states['s1'], 'manual'),
        ]

        paused = process_trade_order_intents()
        self.assertTrue(paused['armed'])
        self.assertEqual(paused['status'], 'market_feed_stale')
        self.assertEqual(paused['order_intents'][0]['status'], 'queued')
        self.assertEqual(paused['order_commands'], [])

        state.market.last_update_at = now
        state.market.latest_candle_time = int(now)
        resumed = process_trade_order_intents()
        self.assertEqual(resumed['market_feed']['status'], 'healthy')
        self.assertEqual(resumed['order_intents'][0]['status'], 'broker_queued')
        self.assertEqual(len(resumed['order_commands']), 1)

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_evaluate_trade_runtime_marks_waiting_market_data(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': False,
            'request_status': 'queued',
            'error': None,
        }
        configure_trade_runtime({
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        payload = evaluate_trade_runtime()
        self.assertEqual(payload['sleeve_states']['s1']['status'], 'waiting_market_data')
        self.assertEqual(payload['sleeve_states']['s1']['decision'], 'waiting_market_data')

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_evaluate_trade_runtime_produces_ready_sleeve_state(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.2, 'low': 0.9, 'close': 1.1, 'volume': 10.0},
                {'time': 2, 'open': 1.1, 'high': 1.3, 'low': 1.0, 'close': 1.2, 'volume': 12.0},
                {'time': 3, 'open': 1.2, 'high': 1.4, 'low': 1.1, 'close': 1.3, 'volume': 11.0},
            ],
        }
        configure_trade_runtime({
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        payload = evaluate_trade_runtime()
        self.assertEqual(payload['sleeve_states']['s1']['status'], 'ready')
        self.assertIn(payload['sleeve_states']['s1']['decision'], {'hold', 'open_long', 'close_long', 'open_short', 'close_short', 'invert_to_long', 'invert_to_short'})
        self.assertIsNotNone(payload['sleeve_states']['s1']['last_evaluated_at'])

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_evaluate_trade_runtime_in_live_mode_does_not_optimistically_open_position(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.1, 'volume': 10.0},
                {'time': 3, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.15, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        payload = evaluate_trade_runtime()
        sleeve = payload['sleeve_states']['s1']
        self.assertEqual(sleeve['decision'], 'open_long')
        self.assertEqual(sleeve['strategy_position'], 1)
        self.assertEqual(sleeve['position'], 0)

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_evaluate_trade_runtime_in_live_mode_suppresses_close_decision_without_broker_position(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.1, 'volume': 10.0},
                {'time': 3, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.15, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'close[0] > open[0]',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })

        payload = evaluate_trade_runtime()
        sleeve = payload['sleeve_states']['s1']
        self.assertEqual(sleeve['decision'], 'hold')
        self.assertEqual(sleeve['position'], 0)

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_evaluate_trade_runtime_queues_order_intent_in_paper_mode(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.1, 'volume': 10.0},
                {'time': 3, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.15, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'paper',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        payload = evaluate_trade_runtime()
        self.assertEqual(len(payload['order_intents']), 1)
        self.assertEqual(payload['order_intents'][0]['status'], 'queued')
        self.assertEqual(payload['order_intents'][0]['action'], 'open')
        self.assertEqual(payload['order_intents'][0]['side'], 'long')
        self.assertEqual(payload['sleeve_states']['s1']['decision'], 'open_long')

        payload = evaluate_trade_runtime()
        self.assertEqual(len(payload['order_intents']), 1)

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_candle_update_does_not_queue_bar_open_intent_mid_candle(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.1, 'volume': 10.0},
                {'time': 3, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.15, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'paper',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        state.trade.market_latest_candle_time = 3
        note_trade_market_update('candle_update', symbol='EURUSD', timeframe='M1', latest_candle_time=3)
        payload = evaluate_trade_runtime(trigger='candle_update')
        self.assertEqual(len(payload['order_intents']), 0)

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_candle_update_queues_bar_open_intent_on_new_candle(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.1, 'volume': 10.0},
                {'time': 3, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.15, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'paper',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        state.trade.market_latest_candle_time = 2
        note_trade_market_update('candle_update', symbol='EURUSD', timeframe='M1', latest_candle_time=3)
        payload = evaluate_trade_runtime(trigger='candle_update')
        self.assertEqual(len(payload['order_intents']), 1)
        self.assertEqual(payload['order_intents'][0]['action'], 'open')

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_live_candle_update_does_not_open_from_current_unfinished_bar_signal(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 3, 'open': 1.0, 'high': 1.1, 'low': 1.0, 'close': 1.05, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'open[0]',
                        'closePrice': 'open[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'open[0]',
                        'closePrice': 'open[0]',
                        'openIf': 'close[0] < open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        state.trade.market_latest_candle_time = 2
        note_trade_market_update('candle_update', symbol='EURUSD', timeframe='M1', latest_candle_time=3)

        payload = evaluate_trade_runtime(trigger='candle_update')

        self.assertEqual(payload['sleeve_states']['s1']['decision'], 'hold')
        self.assertEqual(len(payload['order_intents']), 0)

    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_live_candle_update_still_executes_pending_action_from_previous_closed_bar(self, ensure_market_data_mock):
        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': [
                {'time': 1, 'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
                {'time': 2, 'open': 1.0, 'high': 1.1, 'low': 1.0, 'close': 1.1, 'volume': 10.0},
                {'time': 3, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.0, 'volume': 10.0},
            ],
        }
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {
                        'openPrice': 'open[0]',
                        'closePrice': 'open[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'open[0]',
                        'closePrice': 'open[0]',
                        'openIf': 'close[0] < open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })
        state.trade.market_latest_candle_time = 2
        note_trade_market_update('candle_update', symbol='EURUSD', timeframe='M1', latest_candle_time=3)

        payload = evaluate_trade_runtime(trigger='candle_update')

        self.assertEqual(payload['sleeve_states']['s1']['decision'], 'open_long')
        self.assertEqual(len(payload['order_intents']), 1)
        self.assertEqual(payload['order_intents'][0]['action'], 'open')
        self.assertEqual(payload['order_intents'][0]['side'], 'long')

    def test_process_trade_order_intents_expires_stale_bar_open_signal(self):
        configure_trade_runtime({
            'executionMode': 'paper',
            'signalValiditySeconds': 10,
            'sleeves': [{'id': 's1', 'label': 'One', 'timeframe': 'M1'}],
        })
        state.trade.market_latest_candle_time = 71
        state.trade.order_intents = [{
            'id': 'oi_expired_1',
            'fingerprint': 's1|open|long|60',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 60,
            'expires_at': 70,
            'trigger': 'candle_update',
            'created_at': 1.0,
        }]
        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'expired')
        self.assertEqual(payload['order_intents'][0]['rejection_message'], 'Signal expired before execution window.')

    def test_same_symbol_policy_single_active_blocks_second_open(self):
        configure_trade_runtime({
            'executionMode': 'paper',
            'sameSymbolExecutionPolicy': 'single_active_per_symbol',
            'sleeves': [{'id': 's1', 'label': 'One'}, {'id': 's2', 'label': 'Two'}],
        })
        state.trade.sleeve_states = {
            's1': {'sleeve_id': 's1', 'symbol': 'EURUSD', 'position': 1},
            's2': {'sleeve_id': 's2', 'symbol': 'EURUSD', 'position': 0},
        }
        state.trade.order_intents = []

        from backend.python.services.trade_runtime_service import _append_order_intent
        created = _append_order_intent({
            'id': 'oi_policy_1',
            'fingerprint': 's2|open|short|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's2',
            'sleeve_label': 'Two',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'short',
            'decision': 'open_short',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        self.assertIsNone(created)
        self.assertEqual(len(state.trade.order_intents), 0)

    def test_same_symbol_policy_block_conflicts_allows_same_side(self):
        configure_trade_runtime({
            'executionMode': 'paper',
            'sameSymbolExecutionPolicy': 'block_conflicts',
            'sleeves': [{'id': 's1', 'label': 'One'}, {'id': 's2', 'label': 'Two'}],
        })
        state.trade.sleeve_states = {
            's1': {'sleeve_id': 's1', 'symbol': 'EURUSD', 'position': 1},
            's2': {'sleeve_id': 's2', 'symbol': 'EURUSD', 'position': 0},
        }
        state.trade.order_intents = []

        from backend.python.services.trade_runtime_service import _append_order_intent
        created = _append_order_intent({
            'id': 'oi_policy_2',
            'fingerprint': 's2|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's2',
            'sleeve_label': 'Two',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        self.assertIsNotNone(created)
        self.assertEqual(len(state.trade.order_intents), 1)

    def test_shared_pipe_blocks_second_same_symbol_open_even_when_policy_is_independent(self):
        configure_trade_runtime({
            'mode': 'shared_pipe',
            'executionMode': 'paper',
            'sameSymbolExecutionPolicy': 'independent',
            'sleeves': [{'id': 's1', 'label': 'One'}, {'id': 's2', 'label': 'Two'}],
        })
        state.trade.sleeve_states = {
            's1': {'sleeve_id': 's1', 'symbol': 'EURUSD', 'position': 1},
            's2': {'sleeve_id': 's2', 'symbol': 'EURUSD', 'position': 0},
        }
        state.trade.order_intents = []

        from backend.python.services.trade_runtime_service import _append_order_intent
        created = _append_order_intent({
            'id': 'oi_shared_pipe_1',
            'fingerprint': 's2|open|short|3',
            'status': 'queued',
            'portfolio_mode': 'shared_pipe',
            'execution_mode': 'paper',
            'sleeve_id': 's2',
            'sleeve_label': 'Two',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'short',
            'decision': 'open_short',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        self.assertIsNone(created)
        self.assertEqual(len(state.trade.order_intents), 0)
        self.assertEqual(state.trade.audit_events[0]['kind'], 'order_intent_blocked_policy')
        self.assertIn('shared_pipe blocked open on EURUSD', state.trade.audit_events[0]['message'])

    def test_parallel_sleeves_independent_allows_same_symbol_opposite_side_open(self):
        configure_trade_runtime({
            'mode': 'parallel_sleeves',
            'executionMode': 'paper',
            'sameSymbolExecutionPolicy': 'independent',
            'sleeves': [{'id': 's1', 'label': 'One'}, {'id': 's2', 'label': 'Two'}],
        })
        state.trade.sleeve_states = {
            's1': {'sleeve_id': 's1', 'symbol': 'EURUSD', 'position': 1},
            's2': {'sleeve_id': 's2', 'symbol': 'EURUSD', 'position': 0},
        }
        state.trade.order_intents = []

        from backend.python.services.trade_runtime_service import _append_order_intent
        created = _append_order_intent({
            'id': 'oi_parallel_1',
            'fingerprint': 's2|open|short|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's2',
            'sleeve_label': 'Two',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'short',
            'decision': 'open_short',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        self.assertIsNotNone(created)
        self.assertEqual(len(state.trade.order_intents), 1)

    def test_shared_pipe_keeps_different_symbols_independent(self):
        configure_trade_runtime({
            'mode': 'shared_pipe',
            'executionMode': 'paper',
            'sameSymbolExecutionPolicy': 'independent',
            'sleeves': [{'id': 's1', 'label': 'One'}, {'id': 's2', 'label': 'Two'}],
        })
        state.trade.sleeve_states = {
            's1': {'sleeve_id': 's1', 'symbol': 'EURUSD', 'position': 1},
            's2': {'sleeve_id': 's2', 'symbol': 'GBPUSD', 'position': 0},
        }
        state.trade.order_intents = []

        from backend.python.services.trade_runtime_service import _append_order_intent
        created = _append_order_intent({
            'id': 'oi_shared_pipe_2',
            'fingerprint': 's2|open|short|3',
            'status': 'queued',
            'portfolio_mode': 'shared_pipe',
            'execution_mode': 'paper',
            'sleeve_id': 's2',
            'sleeve_label': 'Two',
            'symbol': 'GBPUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'short',
            'decision': 'open_short',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        self.assertIsNotNone(created)
        self.assertEqual(len(state.trade.order_intents), 1)

    def test_append_order_intent_deduplicates_same_cycle_open(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One'}],
        })
        from backend.python.services.trade_runtime_service import _append_order_intent

        first = _append_order_intent({
            'id': 'oi_cycle_open_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'cycle_id': 's1-cycle-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        second = _append_order_intent({
            'id': 'oi_cycle_open_2',
            'fingerprint': 's1|open|long|4',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'cycle_id': 's1-cycle-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 4,
            'trigger': 'manual',
            'created_at': 2.0,
        })

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(state.trade.order_intents), 1)

    def test_append_order_intent_deduplicates_same_cycle_close(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One'}],
        })
        from backend.python.services.trade_runtime_service import _append_order_intent

        first = _append_order_intent({
            'id': 'oi_cycle_close_1',
            'fingerprint': 's1|close|long|5',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'cycle_id': 's1-cycle-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'long',
            'decision': 'stop_long_trail',
            'bar_time': 5,
            'trigger': 'manual',
            'created_at': 1.0,
        })
        second = _append_order_intent({
            'id': 'oi_cycle_close_2',
            'fingerprint': 's1|close|long|6',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'cycle_id': 's1-cycle-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'long',
            'decision': 'stop_long_loss',
            'bar_time': 6,
            'trigger': 'manual',
            'created_at': 2.0,
        })

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(state.trade.order_intents), 1)

    @patch('backend.python.services.trade_runtime_service.apply_indicator_payload')
    @patch('backend.python.services.trade_runtime_service.ensure_market_data')
    def test_evaluate_trade_runtime_applies_sleeve_indicators(self, ensure_market_data_mock, apply_indicator_payload_mock):
        candles = []
        price = 1.0
        for index in range(30):
            next_price = price + 0.001
            candles.append({
                'time': index + 1,
                'open': price,
                'high': next_price + 0.0005,
                'low': price - 0.0005,
                'close': next_price,
                'volume': 10.0 + index,
            })
            price = next_price

        ensure_market_data_mock.return_value = {
            'ready': True,
            'candles': candles,
        }
        apply_indicator_payload_mock.return_value = ([], None)
        configure_trade_runtime({
            'executionMode': 'paper',
            'sleeves': [{
                'id': 's1',
                'label': 'Indicator sleeve',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'indicators': [
                    {'name': 'RSI', 'params': ['close', 14]},
                ],
                'strategy': {
                    'long': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'close[0] > open[0]',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'short': {
                        'openPrice': 'close[0]',
                        'closePrice': 'close[0]',
                        'openIf': 'False',
                        'closeIf': 'False',
                        'gainPrice': '',
                        'lossPrice': '',
                        'trailingPrice': '',
                    },
                    'other': {
                        'allowInversion': False,
                        'priority': 'Short',
                    },
                },
            }],
        })

        payload = evaluate_trade_runtime()
        apply_indicator_payload_mock.assert_called_once()
        self.assertEqual(payload['sleeve_states']['s1']['status'], 'ready')
        self.assertNotEqual(payload['sleeve_states']['s1']['decision'], 'error')

    def test_process_trade_order_intents_advances_paper_lifecycle(self):
        configure_trade_runtime({'executionMode': 'paper', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        state.trade.order_intents = [{
            'id': 'oi_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        }]

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'acknowledged')

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'filled')

    def test_auto_process_trade_order_intents_completes_paper_cycle_when_armed(self):
        configure_trade_runtime({'executionMode': 'paper', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        state.trade.order_intents = [{
            'id': 'oi_auto_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        }]

        payload = auto_process_trade_order_intents_if_needed()
        self.assertEqual(payload['order_intents'][0]['status'], 'filled')

    def test_auto_process_trade_order_intents_does_nothing_when_disarmed(self):
        configure_trade_runtime({'executionMode': 'paper', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        state.trade.order_intents = [{
            'id': 'oi_auto_2',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'paper',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 3,
            'trigger': 'manual',
            'created_at': 1.0,
        }]

        payload = auto_process_trade_order_intents_if_needed()
        self.assertEqual(payload['order_intents'][0]['status'], 'queued')

    def test_auto_process_trade_order_intents_promotes_live_command_when_armed(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.order_intents = [{
            'id': 'oi_auto_live_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]

        payload = auto_process_trade_order_intents_if_needed()
        self.assertEqual(payload['order_intents'][0]['status'], 'broker_queued')
        self.assertEqual(len(payload['order_commands']), 1)
        self.assertEqual(payload['order_commands'][0]['status'], 'queued')

    def test_process_trade_order_intents_in_live_mode_queues_order_command(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'volume': 0.07}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.order_intents = [{
            'id': 'oi_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'broker_queued')
        self.assertEqual(len(payload['order_commands']), 1)
        self.assertEqual(payload['order_commands'][0]['status'], 'queued')
        self.assertAlmostEqual(payload['order_commands'][0]['volume'], 0.07)

    def test_arm_live_dispatch_immediately_processes_queued_live_intents(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD'}]})
        state.trade.armed = True
        self._prime_healthy_market_feed()
        state.trade.order_intents = [{
            'id': 'oi_open_waiting_dispatch',
            'fingerprint': 's1|open|long|14',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': time.time() + 60,
            'trigger': 'manual',
            'created_at': time.time(),
        }]

        payload = arm_trade_live_dispatch()
        self.assertTrue(payload['live_dispatch_armed'])
        self.assertEqual(payload['order_intents'][0]['status'], 'broker_queued')
        self.assertEqual(len(payload['order_commands']), 1)

    def test_process_trade_order_intents_in_live_mode_suppresses_close_without_broker_position(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD'}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.broker_positions = []
        state.trade.order_intents = [{
            'id': 'oi_close_missing',
            'fingerprint': 's1|close|long|10',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'long',
            'decision': 'stop_long_trail',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'suppressed')
        self.assertIn('No broker position exists', payload['order_intents'][0]['rejection_message'])
        self.assertEqual(len(payload['order_commands']), 0)

    def test_process_trade_order_intents_in_live_mode_suppresses_duplicate_open_when_broker_already_open(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD'}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.broker_positions = [{
            'ticket': '5001',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('s1'),
            'side': 'long',
            'volume': 0.01,
        }]
        state.trade.order_intents = [{
            'id': 'oi_open_duplicate',
            'fingerprint': 's1|open|long|11',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'suppressed')
        self.assertIn('already open in the requested direction', payload['order_intents'][0]['rejection_message'])
        self.assertEqual(len(payload['order_commands']), 0)

    def test_process_trade_order_intents_in_live_mode_blocks_open_when_broker_conflicts(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD'}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.broker_positions = [{
            'ticket': '5002',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('s1'),
            'side': 'short',
            'volume': 0.01,
        }]
        state.trade.order_intents = [{
            'id': 'oi_open_conflict',
            'fingerprint': 's1|open|long|12',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'dispatch_blocked')
        self.assertIn('opposite direction', payload['order_intents'][0]['rejection_message'])
        self.assertEqual(len(payload['order_commands']), 0)

    def test_process_trade_order_intents_in_live_mode_blocks_invalid_broker_stops(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD'}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.market_snapshot_symbol = 'EURUSD'
        state.trade.market_snapshot_candles = [{'close': 1.10000}]
        state.trade.broker_symbol_rules = {
            'EURUSD': {
                'symbol': 'EURUSD',
                'digits': 5,
                'point': 0.00001,
                'stops_level_points': 20,
                'freeze_level_points': 0,
            }
        }
        state.trade.order_intents = [{
            'id': 'oi_open_invalid_stops',
            'fingerprint': 's1|open|long|13',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'take_profit_price': 1.10010,
            'stop_loss_price': 1.09990,
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]

        payload = process_trade_order_intents()
        self.assertEqual(payload['order_intents'][0]['status'], 'broker_queued')
        self.assertEqual(len(payload['order_commands']), 1)
        self.assertIsNone(payload['order_commands'][0]['take_profit_price'])
        self.assertIsNone(payload['order_commands'][0]['stop_loss_price'])

    def test_finalize_trade_order_command_persists_live_trade_history(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'Deep live',
                'sourceStrategyId': 'primary',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {'openPrice': 'open[0]', 'closePrice': 'open[0]', 'openIf': 'False', 'closeIf': 'False'},
                    'short': {'openPrice': 'open[0]', 'closePrice': 'open[0]', 'openIf': 'False', 'closeIf': 'False'},
                    'other': {'allowInversion': False, 'priority': 'Short'},
                },
            }],
        })
        state.trade.order_commands = [{
            'id': 'cmd_live_history_1',
            'source_intent_id': 'oi_live_history_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'Deep live',
            'source_strategy_id': 'primary',
            'cycle_id': 's1-cycle-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'long',
            'broker_ticket': '9001',
            'decision': 'stop_long_trail',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_live_history_1',
            'status': 'broker_acknowledged',
        }]

        finalize_trade_order_command('cmd_live_history_1', {
            'status': 'filled',
            'order_id': '101',
            'deal_id': '202',
            'price': 1.2345,
            'volume': 0.01,
            'profit': 12.5,
            'commission': -0.7,
            'swap': 0.1,
            'message': 'closed',
        })

        stored = get_workspace_live_trade_by_command_id('trade-test-user', 'trade-test-workspace', 'cmd_live_history_1')
        self.assertIsNotNone(stored)
        self.assertEqual(stored['status'], 'filled')
        self.assertEqual(stored['sleeve_label'], 'Deep live')
        self.assertAlmostEqual(stored['profit'], 12.5)
        self.assertAlmostEqual(stored['commission'], -0.7)
        self.assertAlmostEqual(stored['swap'], 0.1)
        self.assertEqual(stored['cycle_id'], 's1-cycle-1')
        self.assertEqual(stored['exit_reason'], 'trail')
        self.assertEqual(stored['broker_position_ticket'], '9001')

    @patch.dict('backend.python.services.trade_runtime_service.FEATURE_FLAGS', {'trader_portfolios_v2': True}, clear=False)
    def test_finalize_trade_order_command_persists_portfolio_and_pipeline_metadata(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'portfolioStructureVersion': 2,
            'portfolios': [{
                'id': 'growth',
                'label': 'Growth',
                'pipelines': [{
                    'id': 'lon',
                    'label': 'London',
                    'portfolioMode': 'shared_pipe',
                    'sleeves': [{
                        'id': 's1',
                        'label': 'Breakout',
                        'sourceStrategyId': 'alpha-breakout',
                        'symbol': 'EURUSD',
                        'timeframe': 'M15',
                    }],
                }],
            }],
        })
        state.trade.order_commands = [{
            'id': 'cmd_live_history_scope_1',
            'source_intent_id': 'oi_live_history_scope_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'shared_pipe',
            'portfolio_id': 'growth',
            'portfolio_label': 'Growth',
            'pipeline_id': 'lon',
            'pipeline_label': 'London',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'Breakout',
            'source_strategy_id': 'alpha-breakout',
            'cycle_id': 's1-cycle-portfolio-1',
            'symbol': 'EURUSD',
            'timeframe': 'M15',
            'action': 'close',
            'side': 'long',
            'decision': 'close_long',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_live_history_scope_1',
            'status': 'broker_acknowledged',
            'portfolio_id': 'growth',
            'portfolio_label': 'Growth',
            'pipeline_id': 'lon',
            'pipeline_label': 'London',
            'cycle_id': 's1-cycle-portfolio-1',
            'source_strategy_id': 'alpha-breakout',
        }]

        finalize_trade_order_command('cmd_live_history_scope_1', {
            'status': 'filled',
            'order_id': '7001',
            'deal_id': '7002',
            'price': 1.2201,
            'volume': 0.03,
            'profit': 4.5,
            'commission': -0.2,
            'swap': 0.0,
            'message': 'closed',
        })

        stored = get_workspace_live_trade_by_command_id('trade-test-user', 'trade-test-workspace', 'cmd_live_history_scope_1')
        self.assertIsNotNone(stored)
        self.assertEqual(stored['portfolio_id'], 'growth')
        self.assertEqual(stored['portfolio_label'], 'Growth')
        self.assertEqual(stored['pipeline_id'], 'lon')
        self.assertEqual(stored['pipeline_label'], 'London')
        self.assertEqual(stored['source_strategy_id'], 'alpha-breakout')

    def test_bridge_heartbeat_syncs_broker_position_ticket_into_live_trade_history(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'Deep live',
                'sourceStrategyId': 'primary',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {'openPrice': 'open[0]', 'closePrice': 'open[0]', 'openIf': 'False', 'closeIf': 'False'},
                    'short': {'openPrice': 'open[0]', 'closePrice': 'open[0]', 'openIf': 'False', 'closeIf': 'False'},
                    'other': {'allowInversion': False, 'priority': 'Short'},
                },
            }],
        })
        state.trade.armed = True
        state.trade.status = 'live'
        state.trade.order_commands = [{
            'id': 'cmd_live_history_open_sync_1',
            'source_intent_id': 'oi_live_history_open_sync_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'Deep live',
            'source_strategy_id': 'primary',
            'cycle_id': 's1-cycle-open-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_live_history_open_sync_1',
            'status': 'broker_acknowledged',
        }]

        finalize_trade_order_command('cmd_live_history_open_sync_1', {
            'status': 'filled',
            'order_id': '301',
            'deal_id': '401',
            'price': 1.1111,
            'volume': 0.01,
            'message': 'opened',
        })

        stored = get_workspace_live_trade_by_command_id('trade-test-user', 'trade-test-workspace', 'cmd_live_history_open_sync_1')
        self.assertIsNotNone(stored)
        self.assertEqual(stored['broker_position_ticket'], '')

        note_trade_bridge_heartbeat({
            'status': 'idle',
            'message': 'ok',
            'online': True,
            'positions': [{
                'ticket': '7777',
                'symbol': 'EURUSD',
                'magic': _build_sleeve_magic('s1'),
                'side': 'long',
                'volume': 0.01,
            }],
        })

        stored = get_workspace_live_trade_by_command_id('trade-test-user', 'trade-test-workspace', 'cmd_live_history_open_sync_1')
        self.assertIsNotNone(stored)
        self.assertEqual(stored['broker_position_ticket'], '7777')

    def test_bridge_heartbeat_persists_broker_managed_close_into_live_trade_history(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'Deep live',
                'sourceStrategyId': 'primary',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'strategy': {
                    'long': {'openPrice': 'open[0]', 'closePrice': 'open[0]', 'openIf': 'False', 'closeIf': 'False'},
                    'short': {'openPrice': 'open[0]', 'closePrice': 'open[0]', 'openIf': 'False', 'closeIf': 'False'},
                    'other': {'allowInversion': False, 'priority': 'Short'},
                },
            }],
        })
        state.trade.armed = True
        state.trade.status = 'live'
        state.trade.order_commands = [{
            'id': 'cmd_live_history_open_reconcile_1',
            'source_intent_id': 'oi_live_history_open_reconcile_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'Deep live',
            'source_strategy_id': 'primary',
            'cycle_id': 's1-cycle-open-2',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'short',
            'decision': 'open_short',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_live_history_open_reconcile_1',
            'status': 'broker_acknowledged',
            'cycle_id': 's1-cycle-open-2',
            'source_strategy_id': 'primary',
        }]

        finalize_trade_order_command('cmd_live_history_open_reconcile_1', {
            'status': 'filled',
            'order_id': '8111',
            'deal_id': '9001',
            'price': 1.2000,
            'volume': 0.01,
            'message': 'opened',
        })
        state.trade.market_snapshot_symbol = 'EURUSD'
        state.trade.market_snapshot_timeframe = 'M1'
        state.trade.market_snapshot_candles = [{
            'time': 124,
            'open': 1.2000,
            'high': 1.2056,
            'low': 1.1990,
            'close': 1.2052,
        }]
        state.trade.sleeve_states['s1'].update({
            'short_stop_loss_price': 1.2050,
            'short_take_profit_price': 1.1900,
            'broker_position_side': 'short',
            'broker_position_ticket': '8111',
            'broker_position_tickets': ['8111'],
            'broker_position_count': 1,
            'actual_position_side': 'short',
            'desired_position': -1,
            'desired_side': 'short',
            'strategy_position': -1,
            'position': -1,
            'current_cycle_id': 's1-cycle-open-2',
        })

        note_trade_bridge_heartbeat({
            'status': 'idle',
            'message': 'ok',
            'online': True,
            'positions': [],
        })

        stored = get_workspace_live_trade_by_command_id('trade-test-user', 'trade-test-workspace', 'reconciled_close::8111')
        self.assertIsNotNone(stored)
        self.assertEqual(stored['status'], 'filled')
        self.assertEqual(stored['action'], 'close')
        self.assertEqual(stored['side'], 'short')
        self.assertEqual(stored['cycle_id'], 's1-cycle-open-2')
        self.assertEqual(stored['exit_reason'], 'loss')
        sleeve_state = build_trade_runtime_payload()['sleeve_states']['s1']
        self.assertEqual(sleeve_state['position'], 0)
        self.assertIsNone(sleeve_state['current_cycle_id'])
        self.assertEqual(sleeve_state['broker_position_side'], 'flat')
        self.assertEqual(sleeve_state['reconciliation_status'], 'match_flat')

    def test_finalize_open_command_rebases_runtime_protective_prices_from_fill(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}],
        })
        state.trade.sleeve_states = {
            's1': {
                'sleeve_id': 's1',
                'label': 'One',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'long_take_profit_price': 1.10,
                'long_stop_loss_price': 0.90,
                'current_cycle_id': None,
                'pending_cycle_id': 's1-cycle-1',
            },
        }
        state.trade.order_commands = [{
            'id': 'cmd_open_rebase_1',
            'source_intent_id': 'oi_open_rebase_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'source_strategy_id': 'Debug',
            'cycle_id': 's1-cycle-1',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 10.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
            'strategy_entry_price': 1.00,
        }]
        state.trade.order_intents = [{
            'id': 'oi_open_rebase_1',
            'status': 'broker_acknowledged',
        }]

        finalize_trade_order_command('cmd_open_rebase_1', {
            'status': 'filled',
            'order_id': '8001',
            'deal_id': '8101',
            'price': 0.97,
            'volume': 0.01,
            'message': 'opened',
        })

        sleeve_state = build_trade_runtime_payload()['sleeve_states']['s1']
        self.assertAlmostEqual(sleeve_state['live_entry_fill_price'], 0.97)
        self.assertAlmostEqual(sleeve_state['strategy_entry_price'], 1.00)
        self.assertAlmostEqual(sleeve_state['protective_price_shift'], -0.03)
        self.assertAlmostEqual(sleeve_state['long_take_profit_price'], 1.07)
        self.assertAlmostEqual(sleeve_state['long_stop_loss_price'], 0.87)

    def test_persist_live_trade_command_recovers_cycle_id_from_intent(self):
        configure_trade_runtime({
            'executionMode': 'live_mt5',
            'sleeves': [{
                'id': 's1',
                'label': 'Deep live',
                'sourceStrategyId': 'Debug',
                'symbol': 'EURUSD',
                'timeframe': 'M1',
            }],
        })
        state.trade.sleeve_states = {
            's1': {
                'current_cycle_id': 's1-cycle-7',
                'pending_cycle_id': None,
            },
        }
        state.trade.order_intents = [{
            'id': 'oi_cycle_recover',
            'cycle_id': 's1-cycle-7',
            'source_strategy_id': 'Debug',
        }]
        state.trade.order_commands = [{
            'id': 'cmd_cycle_recover',
            'source_intent_id': 'oi_cycle_recover',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'Deep live',
            'source_strategy_id': '',
            'cycle_id': None,
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'long',
            'decision': 'close_long',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]

        finalize_trade_order_command('cmd_cycle_recover', {
            'status': 'filled',
            'order_id': '301',
            'deal_id': '401',
            'price': 1.1111,
            'volume': 0.01,
            'profit': 1.0,
            'commission': 0.0,
            'swap': 0.0,
            'message': 'closed',
        })

        stored = get_workspace_live_trade_by_command_id('trade-test-user', 'trade-test-workspace', 'cmd_cycle_recover')
        self.assertIsNotNone(stored)
        self.assertEqual(stored['cycle_id'], 's1-cycle-7')
        self.assertEqual(stored['source_strategy_id'], 'Debug')

    def test_live_command_claim_ack_and_fill_updates_runtime(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        arm_trade_live_dispatch()
        now = self._prime_healthy_market_feed()
        state.trade.order_intents = [{
            'id': 'oi_1',
            'fingerprint': 's1|open|long|3',
            'status': 'queued',
            'portfolio_mode': 'parallel_sleeves',
            'execution_mode': 'live_mt5',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': int(now),
            'trigger': 'manual',
            'created_at': now,
        }]
        process_trade_order_intents()

        command = claim_next_trade_order_command('sess-1')
        self.assertIsNotNone(command)
        self.assertEqual(command['status'], 'claimed')

        command = acknowledge_trade_order_command(command['id'], {
            'session_id': 'sess-1',
            'order_id': '10001',
            'message': 'accepted',
        })
        self.assertEqual(command['status'], 'acknowledged')

        command = finalize_trade_order_command(command['id'], {
            'status': 'filled',
            'order_id': '10001',
            'deal_id': '50001',
            'price': '1.2345',
            'volume': '0.01',
        })
        self.assertEqual(command['status'], 'filled')

        payload = build_trade_runtime_payload()
        self.assertEqual(payload['order_intents'][0]['status'], 'filled')
        self.assertEqual(payload['metrics']['command_ack_count'], 1)
        self.assertEqual(payload['metrics']['command_fill_count'], 1)

    def test_rejected_open_resets_sleeve_position_state(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}]})
        state.trade.sleeve_states = {
            's1': {
                'sleeve_id': 's1',
                'position': 1,
                'current_cycle_id': 's1-cycle-2',
                'pending_cycle_id': 's1-cycle-3',
            },
        }
        state.trade.order_commands = [{
            'id': 'cmd_reject_open_1',
            'source_intent_id': 'oi_reject_open_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'source_strategy_id': 'Debug',
            'cycle_id': 's1-cycle-3',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'open',
            'side': 'long',
            'decision': 'open_long',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_reject_open_1',
            'status': 'broker_acknowledged',
        }]

        finalize_trade_order_command('cmd_reject_open_1', {
            'status': 'rejected',
            'order_id': '10001',
            'message': 'rejected',
        })

        sleeve_state = build_trade_runtime_payload()['sleeve_states']['s1']
        self.assertEqual(sleeve_state['position'], 0)
        self.assertIsNone(sleeve_state['current_cycle_id'])
        self.assertIsNone(sleeve_state['pending_cycle_id'])

    def test_rejected_close_without_matching_position_is_treated_as_already_closed(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}]})
        state.trade.sleeve_states = {
            's1': {
                'sleeve_id': 's1',
                'position': -1,
                'current_cycle_id': 's1-cycle-9',
                'pending_cycle_id': None,
                'broker_position_side': 'short',
                'broker_position_ticket': '7001',
                'broker_position_tickets': ['7001'],
                'broker_position_count': 1,
            },
        }
        state.trade.order_commands = [{
            'id': 'cmd_reject_close_missing_1',
            'source_intent_id': 'oi_reject_close_missing_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'source_strategy_id': 'Debug',
            'cycle_id': 's1-cycle-9',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'short',
            'broker_ticket': '7001',
            'decision': 'resume_close',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_reject_close_missing_1',
            'status': 'broker_acknowledged',
        }]
        state.trade.broker_positions = []

        command = finalize_trade_order_command('cmd_reject_close_missing_1', {
            'status': 'rejected',
            'message': 'No matching position found',
        })

        self.assertEqual(command['status'], 'filled')
        self.assertTrue(command['normalized_from_rejection'])
        self.assertEqual(command['broker_result_status'], 'rejected')
        self.assertIn('treated as already closed', command['message'])
        payload = build_trade_runtime_payload()
        self.assertEqual(payload['order_intents'][0]['status'], 'filled')
        self.assertEqual(payload['metrics']['command_fill_count'], 1)
        self.assertEqual(payload['metrics']['command_reject_count'], 0)
        sleeve_state = payload['sleeve_states']['s1']
        self.assertEqual(sleeve_state['position'], 0)
        self.assertEqual(sleeve_state['broker_position_side'], 'flat')
        self.assertEqual(sleeve_state['reconciliation_status'], 'match_flat')

    def test_rejected_close_stays_rejected_when_same_side_position_still_exists(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One', 'symbol': 'EURUSD', 'timeframe': 'M1'}]})
        state.trade.order_commands = [{
            'id': 'cmd_reject_close_present_1',
            'source_intent_id': 'oi_reject_close_present_1',
            'execution_mode': 'live_mt5',
            'portfolio_mode': 'parallel_sleeves',
            'status': 'acknowledged',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'source_strategy_id': 'Debug',
            'cycle_id': 's1-cycle-10',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'action': 'close',
            'side': 'short',
            'broker_ticket': '7002',
            'decision': 'resume_close',
            'bar_time': 123.0,
            'created_at': 1.0,
            'acknowledged_at': 2.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_reject_close_present_1',
            'status': 'broker_acknowledged',
        }]
        state.trade.broker_positions = [{
            'ticket': '7002',
            'symbol': 'EURUSD',
            'magic': _build_sleeve_magic('s1'),
            'side': 'short',
            'volume': 0.01,
        }]

        command = finalize_trade_order_command('cmd_reject_close_present_1', {
            'status': 'rejected',
            'message': 'No matching position found',
        })

        self.assertEqual(command['status'], 'rejected')
        self.assertFalse(command.get('normalized_from_rejection'))
        payload = build_trade_runtime_payload()
        self.assertEqual(payload['order_intents'][0]['status'], 'rejected')
        self.assertEqual(payload['metrics']['command_fill_count'], 0)
        self.assertEqual(payload['metrics']['command_reject_count'], 1)

    def test_reconcile_marks_stale_live_command_and_interrupts_runtime(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        arm_trade_runtime()
        state.trade.order_commands = [{
            'id': 'cmd_1',
            'source_intent_id': 'oi_1',
            'status': 'claimed',
            'sleeve_id': 's1',
            'sleeve_label': 'One',
            'symbol': 'EURUSD',
            'created_at': 1.0,
            'claimed_at': 1.0,
        }]
        state.trade.order_intents = [{
            'id': 'oi_1',
            'status': 'broker_claimed',
        }]

        payload = reconcile_trade_runtime_commands(stale_after_seconds=1)
        self.assertEqual(payload['order_commands'][0]['status'], 'stale')
        self.assertEqual(payload['order_intents'][0]['status'], 'stale')
        self.assertEqual(payload['status'], 'interrupted')

    def test_reset_trade_runtime_commands_clears_queue(self):
        configure_trade_runtime({'executionMode': 'live_mt5', 'sleeves': [{'id': 's1', 'label': 'One'}]})
        state.trade.order_commands = [{'id': 'cmd_1', 'status': 'queued'}]
        state.trade.order_intents = [{'id': 'oi_1', 'status': 'broker_queued'}]

        payload = reset_trade_runtime_commands(clear_intents=False)
        self.assertEqual(payload['order_commands'], [])
        self.assertEqual(len(payload['order_intents']), 1)


if __name__ == '__main__':
    unittest.main()
