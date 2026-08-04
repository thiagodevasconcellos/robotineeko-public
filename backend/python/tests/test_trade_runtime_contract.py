import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend.python import bridge, trade_bridge
from backend.python.app_state import state
from backend.python.trade_runtime_contract import (
    TradeRuntimeConfigureRequest,
    TradeRuntimeSleevePayload,
)


class TradeRuntimeContractTest(unittest.TestCase):
    def setUp(self):
        trade_bridge._SCHEDULED_MARKET_EVALUATION_THREAD = None
        trade_bridge._SCHEDULED_MARKET_EVALUATION_TRIGGER = None
        state.trade.armed = False
        state.trade.live_dispatch_armed = False
        state.trade.live = False
        state.trade.status = 'idle'
        state.trade.portfolio_structure_version = 1
        state.trade.portfolios = []
        state.trade.sleeves = []
        state.trade.sleeve_states = {}
        state.trade.order_intents = []
        state.trade.order_commands = []
        state.trade.last_error = None
        state.trade.market_feed_status = 'idle'

    def test_bridge_and_trade_service_share_same_configure_contract(self):
        self.assertIs(bridge.TradeRuntimeConfigureRequest, TradeRuntimeConfigureRequest)
        self.assertIs(trade_bridge.TradeRuntimeConfigureRequest, TradeRuntimeConfigureRequest)

    def test_configure_contract_preserves_runtime_fields_and_future_extras(self):
        payload = TradeRuntimeConfigureRequest.model_validate({
            'mode': 'parallel_sleeves',
            'executionMode': 'live_mt5',
            'sameSymbolExecutionPolicy': 'block_conflicts',
            'signalValiditySeconds': 7,
            'latencyBudgetMs': 90,
            'liveDispatchArmed': True,
            'futureField': 'keep-me',
            'sleeves': [
                {
                    'id': 's1',
                    'label': 'Sleeve 1',
                    'symbol': 'EURUSD',
                    'timeframe': 'M1',
                    'volume': 0.07,
                    'sourceStrategyId': 'primary',
                    'strategy': {'long': {'openIf': 'True'}},
                    'indicators': [{'name': 'EMA', 'params': ['close', 20]}],
                    'futureSleeveField': 'keep-me-too',
                },
            ],
        })

        dumped = payload.model_dump()

        self.assertEqual(dumped['signalValiditySeconds'], 7)
        self.assertEqual(dumped['futureField'], 'keep-me')
        self.assertEqual(dumped['sleeves'][0]['volume'], 0.07)
        self.assertEqual(dumped['sleeves'][0]['indicators'][0]['name'], 'EMA')
        self.assertEqual(dumped['sleeves'][0]['futureSleeveField'], 'keep-me-too')

    def test_configure_contract_accepts_portfolio_runtime_structure(self):
        payload = TradeRuntimeConfigureRequest.model_validate({
            'mode': 'parallel_sleeves',
            'portfolioStructureVersion': 2,
            'portfolios': [
                {
                    'id': 'p1',
                    'label': 'Portfolio 1',
                    'pipelines': [
                        {
                            'id': 'london',
                            'label': 'London',
                            'portfolioMode': 'shared_pipe',
                            'sleeves': [
                                {
                                    'id': 's1',
                                    'label': 'Sleeve 1',
                                    'symbol': 'EURUSD',
                                    'timeframe': 'M15',
                                    'volumeMode': 'base_volume_compounding',
                                    'baseVolume': 0.02,
                                    'maxVolumeCap': 0.20,
                                    'strategy': {'long': {'openIf': 'True'}},
                                },
                            ],
                        },
                    ],
                },
            ],
        })

        dumped = payload.model_dump()

        self.assertEqual(dumped['portfolioStructureVersion'], 2)
        self.assertEqual(dumped['portfolios'][0]['id'], 'p1')
        self.assertEqual(dumped['portfolios'][0]['pipelines'][0]['portfolioMode'], 'shared_pipe')
        self.assertEqual(dumped['portfolios'][0]['pipelines'][0]['sleeves'][0]['volumeMode'], 'base_volume_compounding')
        self.assertEqual(dumped['portfolios'][0]['pipelines'][0]['sleeves'][0]['baseVolume'], 0.02)

    def test_sleeve_contract_preserves_snake_case_extras_used_by_internal_callers(self):
        sleeve = TradeRuntimeSleevePayload.model_validate({
            'id': 's1',
            'label': 'Sleeve 1',
            'source_strategy_id': 'auxiliary',
            'volume': 0.03,
        })

        dumped = sleeve.model_dump()

        self.assertEqual(dumped['source_strategy_id'], 'auxiliary')
        self.assertEqual(dumped['volume'], 0.03)

    @patch('backend.python.bridge.get_trade_runtime_via_service')
    @patch('backend.python.bridge.get_trade_service_health')
    def test_build_service_health_preserves_unreachable_trade_service_hint(self, get_trade_service_health_mock, get_trade_runtime_via_service_mock):
        state.chart.snapshot_error = None
        state.chart.snapshot_built_at = 1.0
        state.workspace.last_error = None
        state.strategy.request = None
        state.strategy.last_applied_at = None
        state.bridge.history_ready = True
        state.bridge.loading = False
        state.bridge.error = None
        state.bridge.revision = 1
        state.bridge.ea_last_heartbeat_at = None
        state.bridge.ea_timeout_seconds = 8.0

        get_trade_service_health_mock.return_value = {
            'status': 'degraded',
            'service': {
                'reachable': False,
                'stale': True,
            },
            'trade_runtime': {
                'status': 'armed',
                'mode': 'parallel_sleeves',
                'armed': True,
                'live_dispatch_armed': False,
                'live': False,
                'sleeves': [{'id': 's1'}],
                'active_symbols': ['EURUSD'],
                'market_feed': {'status': 'waiting'},
                'last_error': None,
            },
        }
        get_trade_runtime_via_service_mock.return_value = {
            'status': 'ok',
            'trade_runtime': {
                'status': 'armed',
                'mode': 'parallel_sleeves',
                'armed': True,
                'live_dispatch_armed': False,
                'live': False,
                'sleeves': [{'id': 's1'}],
                'active_symbols': ['EURUSD'],
                'market_feed': {'status': 'waiting'},
                'last_error': None,
            },
            'trade_service': {
                'reachable': False,
                'stale': True,
            },
        }

        payload = bridge.build_service_health_payload()

        self.assertFalse(payload['trade_service']['reachable'])
        self.assertTrue(payload['trade_service']['stale'])
        self.assertFalse(payload['checks']['trade']['ok'])

    @patch('backend.python.trade_bridge._schedule_runtime_market_evaluation')
    @patch('backend.python.trade_bridge.note_trade_market_update')
    @patch('backend.python.trade_bridge._require_internal')
    def test_internal_market_update_schedules_evaluation_when_runtime_is_armed(
        self,
        require_internal_mock,
        note_trade_market_update_mock,
        schedule_runtime_market_evaluation_mock,
    ):
        state.trade.armed = True
        state.trade.sleeves = [{'id': 's1'}]

        payload = trade_bridge.post_trade_market_update(
            {
                'stage': 'candle_update',
                'symbol': 'EURUSD',
                'timeframe': 'M15',
                'candle_count': 1000,
                'latest_candle_time': 123,
                'candles': [{'time': 123, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.05}],
            },
            SimpleNamespace(headers={}),
        )

        require_internal_mock.assert_called_once()
        note_trade_market_update_mock.assert_called_once()
        schedule_runtime_market_evaluation_mock.assert_called_once_with('candle_update')
        self.assertEqual(payload['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
