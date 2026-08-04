import unittest
from unittest.mock import patch

from backend.python.app_state import state
from backend.python.bridge import note_bridge_heartbeat
from backend.python.chart_backend import build_chart_market_tails_payload
from backend.python.services.chart_service import build_chart_symbol_catalog_payload


class ChartServiceSymbolCatalogTest(unittest.TestCase):
    def setUp(self):
        if not isinstance(getattr(state.chart, 'request', None), dict):
            state.chart.request = {}
        state.chart.request['symbol'] = 'EURUSD'
        state.chart.request['timeframe'] = 'M1'
        state.chart.request['bars'] = 1000
        state.bridge.request['symbol'] = 'EURUSD'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 1000
        state.bridge.history_meta = {
            'symbol': None,
            'timeframe': None,
            'requested_bars': None,
            'loaded_candles': 0,
            'first_time': None,
            'last_time': None,
            'last_reset_reason': None,
        }
        state.bridge.ea_market_watch_symbols = []
        state.bridge.ea_market_watch_exhaustive = False
        state.trade.active_symbols = []
        state.trade.broker_symbol_rules = {}
        state.trade.broker_positions = []
        state.market_data.cache_by_key.clear()
        state.market_data.requests_by_id.clear()

    def test_note_bridge_heartbeat_captures_market_watch_symbol_catalog(self):
        note_bridge_heartbeat({
            'market_watch_symbols': 'EURUSD;GBPUSD;USDJPY',
            'market_watch_exhaustive': '1',
        })

        self.assertEqual(state.bridge.ea_market_watch_symbols, ['EURUSD', 'GBPUSD', 'USDJPY'])
        self.assertTrue(state.bridge.ea_market_watch_exhaustive)

    def test_build_chart_symbol_catalog_is_exhaustive_when_market_watch_is_present(self):
        state.bridge.ea_market_watch_symbols = ['GBPUSD', 'EURUSD']
        state.bridge.ea_market_watch_exhaustive = True
        state.trade.active_symbols = ['USDJPY']

        payload = build_chart_symbol_catalog_payload()

        self.assertTrue(payload['exhaustive'])
        self.assertEqual(payload['source'], 'mt5_market_watch')
        self.assertEqual(payload['symbols'], ['EURUSD', 'GBPUSD'])
        rows_by_symbol = {row['symbol']: row for row in payload['rows']}
        self.assertIn('mt5_market_watch', rows_by_symbol['EURUSD']['sources'])
        self.assertNotIn('USDJPY', rows_by_symbol)

    def test_build_chart_symbol_catalog_exhaustive_mode_ignores_stale_runtime_and_cache_symbols(self):
        state.bridge.ea_market_watch_symbols = ['EURUSD', 'GBPUSD']
        state.bridge.ea_market_watch_exhaustive = True
        state.chart.request['symbol'] = 'WIN$'
        state.bridge.request['symbol'] = 'WIN$'
        state.trade.active_symbols = ['CCM$']
        state.trade.broker_symbol_rules = {
            'WIN$': {'digits': 5},
            'CCM$': {'digits': 5},
        }
        state.market_data.cache_by_key['WIN$|M1|3000'] = {
            'symbol': 'WIN$',
            'snapshot': {'symbol': 'WIN$'},
        }
        state.market_data.requests_by_id['req-1'] = {'symbol': 'CCM$'}

        payload = build_chart_symbol_catalog_payload()

        self.assertTrue(payload['exhaustive'])
        self.assertEqual(payload['symbols'], ['EURUSD', 'GBPUSD'])
        rows_by_symbol = {row['symbol']: row for row in payload['rows']}
        self.assertNotIn('WIN$', rows_by_symbol)
        self.assertNotIn('CCM$', rows_by_symbol)

    def test_build_chart_symbol_catalog_falls_back_to_known_subset_without_market_watch(self):
        state.trade.active_symbols = ['GBPUSD']
        state.trade.broker_symbol_rules = {'USDCHF': {'digits': 5}}

        payload = build_chart_symbol_catalog_payload()

        self.assertFalse(payload['exhaustive'])
        self.assertEqual(payload['source'], 'mt5_known_symbols_subset')
        self.assertEqual(payload['symbols'], ['EURUSD', 'GBPUSD', 'USDCHF'])

    @patch('backend.python.chart_backend.ensure_market_data')
    def test_build_chart_market_tails_payload_returns_latest_close_per_market(self, ensure_market_data_mock):
        def fake_ensure_market_data(symbol, timeframe, bars, source='api'):
            return {
                'ready': True,
                'request_status': 'completed',
                'source': source,
                'snapshot': {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'candles': [
                        {'time': 100, 'close': 1.10},
                        {'time': 101, 'close': 1.11},
                        {'time': 102, 'close': 1.12},
                    ],
                },
            }

        ensure_market_data_mock.side_effect = fake_ensure_market_data

        payload = build_chart_market_tails_payload([
            {'symbol': 'eurusd', 'timeframe': 'm15', 'bars': 2},
            {'symbol': 'EURUSD', 'timeframe': 'M15', 'bars': 2},
            {'symbol': 'GBPUSD', 'timeframe': 'M5', 'bars': 1},
        ])

        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['market_count'], 2)
        self.assertEqual(ensure_market_data_mock.call_count, 2)
        rows_by_key = {row['key']: row for row in payload['markets']}
        self.assertEqual(rows_by_key['EURUSD::M15']['last_close'], 1.12)
        self.assertEqual(rows_by_key['EURUSD::M15']['bars_loaded'], 2)
        self.assertEqual(rows_by_key['GBPUSD::M5']['last_time'], 102)


if __name__ == '__main__':
    unittest.main()
