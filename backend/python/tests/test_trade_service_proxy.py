import unittest
from unittest.mock import patch

from backend.python.services import trade_service_proxy


class TradeServiceProxyTest(unittest.TestCase):
    def setUp(self):
        trade_service_proxy._LAST_TRADE_RUNTIME = None
        trade_service_proxy._LAST_TRADE_SERVICE_HEALTH = None

    @patch('backend.python.services.trade_service_proxy._request_json')
    def test_get_trade_runtime_via_service_uses_internal_runtime_endpoint(self, request_json_mock):
        request_json_mock.return_value = {
            'status': 'ok',
            'trade_runtime': {
                'status': 'idle',
                'armed': False,
            },
        }

        with patch.dict(trade_service_proxy.TRADE_SERVICE_CONFIG, {'internal_token': 'secret-token'}, clear=False):
            payload = trade_service_proxy.get_trade_runtime_via_service(fallback_local=False)

        self.assertEqual(payload['status'], 'ok')
        request_json_mock.assert_called_once()
        args, kwargs = request_json_mock.call_args
        self.assertEqual(args[0], 'GET')
        self.assertEqual(args[1], '/internal/trade/runtime')
        self.assertEqual(kwargs['headers']['x-robotineeko-trade-internal-token'], 'secret-token')


if __name__ == '__main__':
    unittest.main()
