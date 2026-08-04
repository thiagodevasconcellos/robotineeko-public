import unittest
import time
from unittest.mock import patch

from backend.python.app_state import state
from backend.python.bridge import get_mt5_next_job, sync_market_data_request
from backend.python.services.market_data_service import get_market_snapshot, wait_for_market_data


class MarketDataServiceTest(unittest.TestCase):
    def setUp(self):
        state.market_data.cache_by_key.clear()
        state.market_data.request_order.clear()
        state.market_data.requests_by_id.clear()
        state.market_data.pending_queue.clear()
        state.market_data.last_request_id = None
        state.market_data.last_cache_key = None
        state.market_data.last_error = None
        state.market_data.revision = 0
        state.bridge.active_request_id = None
        state.bridge.request['symbol'] = 'EURUSD'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 1000
        state.bridge.candles = []
        state.bridge.history_ready = False
        state.bridge.history_loading = False
        state.bridge.history_error = None
        state.bridge.history_request_started_at = None
        state.bridge.history_meta = {
            'symbol': None,
            'timeframe': None,
            'requested_bars': None,
            'loaded_candles': 0,
            'first_time': None,
            'last_time': None,
            'last_reset_reason': None,
        }
        state.bridge.ea_last_status = None
        state.bridge.ea_last_error = None
        state.bridge.ea_last_error_at = None
        state.bridge.ea_last_heartbeat_at = None

    def test_sync_market_data_request_does_not_reuse_completed_request_without_snapshot(self):
        stale_request_id = 'mreq_stale'
        state.market_data.requests_by_id[stale_request_id] = {
            'request_id': stale_request_id,
            'cache_key': 'USDJPY|M1|10000',
            'symbol': 'USDJPY',
            'timeframe': 'M1',
            'bars': 10000,
            'status': 'completed',
            'result_ready': False,
        }
        state.market_data.request_order.append(stale_request_id)

        payload = sync_market_data_request('USDJPY', 'M1', 10000, source='test')

        self.assertNotEqual(payload['request_id'], stale_request_id)
        self.assertEqual(payload['status'], 'loading')
        self.assertEqual(payload['cache_key'], 'USDJPY|M1|10000')
        self.assertEqual(state.bridge.active_request_id, payload['request_id'])

    def test_sync_market_data_request_reuses_completed_request_when_snapshot_is_ready(self):
        request_id = 'mreq_ready'
        state.market_data.requests_by_id[request_id] = {
            'request_id': request_id,
            'cache_key': 'USDJPY|M1|10000',
            'symbol': 'USDJPY',
            'timeframe': 'M1',
            'bars': 10000,
            'status': 'completed',
            'result_ready': True,
        }
        state.market_data.request_order.append(request_id)
        state.market_data.cache_by_key['USDJPY|M1|10000'] = {
            'snapshot': {
                'bars_requested': 10000,
                'bars_loaded': 10000,
                'candles': [{'time': index} for index in range(10000)],
            },
        }

        payload = sync_market_data_request('USDJPY', 'M1', 10000, source='test')

        self.assertEqual(payload['request_id'], request_id)
        self.assertEqual(state.bridge.active_request_id, None)

    def test_sync_market_data_request_discards_stale_active_request_for_other_market(self):
        stale_request_id = 'mreq_stale'
        state.market_data.requests_by_id[stale_request_id] = {
            'request_id': stale_request_id,
            'cache_key': 'EUR|M1|1000',
            'symbol': 'EUR',
            'timeframe': 'M1',
            'bars': 1000,
            'status': 'loading',
            'result_ready': False,
        }
        state.market_data.request_order.append(stale_request_id)
        state.bridge.active_request_id = stale_request_id

        payload = sync_market_data_request('EURUSD', 'M1', 1000, source='test')

        self.assertNotEqual(payload['request_id'], stale_request_id)
        self.assertEqual(payload['cache_key'], 'EURUSD|M1|1000')
        self.assertEqual(state.bridge.active_request_id, payload['request_id'])
        self.assertEqual(state.market_data.requests_by_id[stale_request_id]['status'], 'cancelled')

    def test_get_mt5_next_job_discards_stale_active_request_before_poll_response(self):
        stale_request_id = 'mreq_stale'
        state.market_data.requests_by_id[stale_request_id] = {
            'request_id': stale_request_id,
            'cache_key': 'EUR|M1|1000',
            'symbol': 'EUR',
            'timeframe': 'M1',
            'bars': 1000,
            'status': 'loading',
            'result_ready': False,
        }
        state.market_data.request_order.append(stale_request_id)
        state.bridge.active_request_id = stale_request_id
        state.bridge.request['symbol'] = 'EURUSD'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 1000

        response_text = get_mt5_next_job()

        self.assertIn(';EURUSD;M1;1000', response_text)
        self.assertEqual(state.market_data.requests_by_id[stale_request_id]['status'], 'cancelled')

    def test_sync_market_data_request_does_not_reuse_completed_request_with_partial_snapshot(self):
        request_id = 'mreq_partial'
        state.market_data.requests_by_id[request_id] = {
            'request_id': request_id,
            'cache_key': 'USDJPY|M1|10000',
            'symbol': 'USDJPY',
            'timeframe': 'M1',
            'bars': 10000,
            'status': 'completed',
            'result_ready': False,
        }
        state.market_data.request_order.append(request_id)
        state.market_data.cache_by_key['USDJPY|M1|10000'] = {
            'snapshot': {
                'bars_requested': 10000,
                'bars_loaded': 4763,
                'candles': [{'time': index} for index in range(4763)],
            },
        }

        payload = sync_market_data_request('USDJPY', 'M1', 10000, source='test')

        self.assertNotEqual(payload['request_id'], request_id)
        self.assertEqual(payload['cache_key'], 'USDJPY|M1|10000')

    def test_wait_for_market_data_requeues_completed_request_without_snapshot(self):
        missing_context = {
            'ready': False,
            'request_status': 'completed',
            'cache_key': 'USDJPY|M1|10000',
            'error': None,
        }
        ready_context = {
            'ready': True,
            'request_status': 'completed',
            'cache_key': 'USDJPY|M1|10000',
            'candles': [{'time': 1}],
        }

        with patch('backend.python.services.market_data_service.get_market_snapshot', side_effect=[missing_context, missing_context, ready_context]) as get_snapshot_mock:
            with patch('backend.python.services.market_data_service.has_ready_market_snapshot', side_effect=[False, False]):
                with patch('backend.python.services.market_data_service.request_market_data') as request_market_data_mock:
                    context = wait_for_market_data('USDJPY', 'M1', 10000, timeout_seconds=0.5, poll_interval=0.0, source='test')

        self.assertTrue(context['ready'])
        self.assertGreaterEqual(request_market_data_mock.call_count, 2)
        self.assertEqual(get_snapshot_mock.call_count, 3)

    def test_wait_for_market_data_returns_ready_on_final_reconciliation(self):
        loading_context = {
            'ready': False,
            'request_status': 'loading',
            'cache_key': 'USDJPY|M1|5000',
            'diagnostics': {
                'request_age_seconds': 55.0,
                'bridge_heartbeat_age_seconds': 1.0,
            },
        }
        ready_context = {
            'ready': True,
            'request_status': 'completed',
            'cache_key': 'USDJPY|M1|5000',
            'candles': [{'time': 1}],
        }

        with patch('backend.python.services.market_data_service.ensure_market_data', side_effect=[loading_context, ready_context]):
            with patch('backend.python.services.market_data_service.get_market_snapshot', return_value=loading_context):
                context = wait_for_market_data('USDJPY', 'M1', 5000, timeout_seconds=0.0, poll_interval=0.0, source='test')

        self.assertTrue(context['ready'])

    def test_wait_for_market_data_uses_loading_grace_window(self):
        loading_context = {
            'ready': False,
            'request_status': 'loading',
            'cache_key': 'USDJPY|M1|5000',
            'diagnostics': {
                'request_age_seconds': 30.0,
                'bridge_heartbeat_age_seconds': 1.0,
            },
        }
        ready_context = {
            'ready': True,
            'request_status': 'completed',
            'cache_key': 'USDJPY|M1|5000',
            'candles': [{'time': 1}],
        }

        with patch('backend.python.services.market_data_service.ensure_market_data', return_value=loading_context):
            with patch('backend.python.services.market_data_service.get_market_snapshot', side_effect=[loading_context, ready_context]):
                context = wait_for_market_data('USDJPY', 'M1', 5000, timeout_seconds=0.01, poll_interval=0.0, source='test')

        self.assertTrue(context['ready'])

    def test_wait_for_market_data_can_fallback_to_partial_exact_snapshot(self):
        state.market_data.cache_by_key['EURUSD|M1|1000000'] = {
            'cache_key': 'EURUSD|M1|1000000',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 1000000,
            'revision': 31,
            'ready': False,
            'status': 'partial',
            'error': 'Bridge cached only 4,763 of 1,000,000 requested candles for EURUSD M1.',
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 1000000,
                'bars_loaded': 4763,
                'candles': [{'time': index} for index in range(4763)],
            },
        }
        state.market_data.requests_by_id['mreq_partial'] = {
            'request_id': 'mreq_partial',
            'cache_key': 'EURUSD|M1|1000000',
            'status': 'completed',
            'bars': 1000000,
            'result_ready': False,
        }
        state.market_data.request_order.append('mreq_partial')

        context = wait_for_market_data(
            'EURUSD',
            'M1',
            1000000,
            timeout_seconds=0.0,
            poll_interval=0.0,
            source='test',
            allow_truncated_fallback=True,
        )

        self.assertTrue(context['ready'])
        self.assertTrue(context['truncated'])
        self.assertEqual(context['bars_requested'], 4763)
        self.assertEqual(context['requested_bars_original'], 1000000)
        self.assertIn('Using the maximum available history instead', context['notice'])

    def test_wait_for_market_data_can_fallback_to_smaller_ready_cache(self):
        state.market_data.cache_by_key['CCM$|M15|1000'] = {
            'cache_key': 'CCM$|M15|1000',
            'symbol': 'CCM$',
            'timeframe': 'M15',
            'bars': 1000,
            'revision': 14,
            'snapshot': {
                'symbol': 'CCM$',
                'timeframe': 'M15',
                'bars_requested': 1000,
                'bars_loaded': 1000,
                'first_time': 1,
                'last_time': 1000,
                'candles': [{'time': index} for index in range(1, 1001)],
            },
        }

        context = wait_for_market_data(
            'CCM$',
            'M15',
            100000,
            timeout_seconds=0.0,
            poll_interval=0.0,
            source='test',
            allow_truncated_fallback=True,
        )

        self.assertTrue(context['ready'])
        self.assertTrue(context['truncated'])
        self.assertEqual(context['source'], 'cache_subset_fallback')
        self.assertEqual(context['bars_requested'], 1000)
        self.assertEqual(context['requested_bars_original'], 100000)
        self.assertEqual(len(context['candles']), 1000)

    def test_sync_market_data_request_preserves_ready_bridge_history_for_larger_fallback(self):
        state.bridge.history_ready = True
        state.bridge.history_loading = False
        state.bridge.history_meta = {
            'symbol': 'CCM$',
            'timeframe': 'M15',
            'requested_bars': 1000,
            'loaded_candles': 1000,
            'first_time': 1,
            'last_time': 1000,
            'last_reset_reason': None,
        }
        state.bridge.candles = [{'time': index} for index in range(1, 1001)]

        payload = sync_market_data_request('CCM$', 'M15', 100000, source='test')

        self.assertEqual(payload['cache_key'], 'CCM$|M15|100000')
        preserved_cache = state.market_data.cache_by_key.get('CCM$|M15|1000') or {}
        preserved_snapshot = preserved_cache.get('snapshot') or {}
        self.assertEqual(preserved_cache.get('status'), 'ready')
        self.assertEqual(preserved_snapshot.get('bars_loaded'), 1000)
        self.assertEqual(len(preserved_snapshot.get('candles') or []), 1000)

        context = wait_for_market_data(
            'CCM$',
            'M15',
            100000,
            timeout_seconds=0.0,
            poll_interval=0.0,
            source='test',
            allow_truncated_fallback=True,
        )

        self.assertTrue(context['ready'])
        self.assertTrue(context['truncated'])
        self.assertEqual(context['source'], 'cache_subset_fallback')
        self.assertEqual(context['bars_requested'], 1000)
        self.assertEqual(context['requested_bars_original'], 100000)

    def test_get_market_snapshot_exposes_request_and_bridge_diagnostics(self):
        request_id = 'mreq_diag'
        state.market_data.requests_by_id[request_id] = {
            'request_id': request_id,
            'cache_key': 'USDJPY|M1|10000',
            'symbol': 'USDJPY',
            'timeframe': 'M1',
            'bars': 10000,
            'status': 'loading',
            'created_at': 100.0,
            'started_at': 105.0,
            'error': None,
        }
        state.market_data.request_order.append(request_id)
        state.bridge.ea_last_status = 'loading'
        state.bridge.ea_last_error = 'bridge timeout'
        state.bridge.ea_last_heartbeat_at = None

        context = get_market_snapshot('USDJPY', 'M1', 10000)

        self.assertFalse(context['ready'])
        self.assertEqual(context['request_status'], 'loading')
        self.assertEqual(context['diagnostics']['request_id'], request_id)
        self.assertEqual(context['diagnostics']['bridge_last_status'], 'loading')
        self.assertEqual(context['diagnostics']['bridge_last_error'], 'bridge timeout')

    def test_get_market_snapshot_marks_active_request_as_error_after_history_timeout(self):
        request_id = 'mreq_timeout'
        state.market_data.requests_by_id[request_id] = {
            'request_id': request_id,
            'cache_key': 'USDJPY|M1|10000',
            'symbol': 'USDJPY',
            'timeframe': 'M1',
            'bars': 10000,
            'status': 'loading',
            'created_at': 100.0,
            'started_at': 105.0,
            'error': None,
            'result_ready': False,
        }
        state.market_data.request_order.append(request_id)
        state.bridge.active_request_id = request_id
        state.bridge.request['symbol'] = 'USDJPY'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 10000
        state.bridge.history_loading = True
        state.bridge.history_request_started_at = time.time() - 120.0

        context = get_market_snapshot('USDJPY', 'M1', 10000)

        self.assertFalse(context['ready'])
        self.assertEqual(context['request_status'], 'error')
        self.assertIn('History load timeout', context['error'])
        self.assertEqual(state.market_data.requests_by_id[request_id]['status'], 'error')
        self.assertIsNone(state.bridge.active_request_id)

    def test_get_market_snapshot_reconciles_loading_request_after_bridge_failure(self):
        request_id = 'mreq_failed'
        state.market_data.requests_by_id[request_id] = {
            'request_id': request_id,
            'cache_key': 'USDJPY|M1|10000',
            'symbol': 'USDJPY',
            'timeframe': 'M1',
            'bars': 10000,
            'status': 'loading',
            'created_at': 100.0,
            'started_at': 105.0,
            'error': None,
            'result_ready': False,
        }
        state.market_data.request_order.append(request_id)
        state.bridge.active_request_id = request_id
        state.bridge.request['symbol'] = 'USDJPY'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 10000
        state.bridge.history_loading = False
        state.bridge.history_error = 'History load timeout after 180.0s'

        context = get_market_snapshot('USDJPY', 'M1', 10000)

        self.assertFalse(context['ready'])
        self.assertEqual(context['request_status'], 'error')
        self.assertEqual(context['error'], 'History load timeout after 180.0s')
        self.assertEqual(state.market_data.requests_by_id[request_id]['status'], 'error')
        self.assertIsNone(state.bridge.active_request_id)

    def test_get_market_snapshot_reuses_larger_cached_snapshot(self):
        state.market_data.cache_by_key['EURUSD|M1|50000'] = {
            'cache_key': 'EURUSD|M1|50000',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 50000,
            'revision': 12,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 50000,
                'bars_loaded': 50000,
                'first_time': 1,
                'last_time': 50000,
                'candles': [{'time': index} for index in range(1, 50001)],
            },
        }

        context = get_market_snapshot('EURUSD', 'M1', 5000)

        self.assertTrue(context['ready'])
        self.assertEqual(context['source'], 'cache_superset')
        self.assertEqual(len(context['candles']), 5000)
        self.assertEqual(context['candles'][0]['time'], 45001)
        self.assertEqual(context['candles'][-1]['time'], 50000)

    def test_get_market_snapshot_prefers_fresher_superset_over_stale_exact_cache(self):
        state.market_data.cache_by_key['EURUSD|M1|1000'] = {
            'cache_key': 'EURUSD|M1|1000',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 1000,
            'revision': 11,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 1000,
                'bars_loaded': 1000,
                'first_time': 1,
                'last_time': 1000,
                'candles': [{'time': index} for index in range(1, 1001)],
            },
        }
        state.market_data.cache_by_key['EURUSD|M1|2000'] = {
            'cache_key': 'EURUSD|M1|2000',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 2000,
            'revision': 12,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 2000,
                'bars_loaded': 2000,
                'first_time': 1,
                'last_time': 2000,
                'candles': [{'time': index} for index in range(1, 2001)],
            },
        }

        context = get_market_snapshot('EURUSD', 'M1', 1000)

        self.assertTrue(context['ready'])
        self.assertEqual(context['source'], 'cache_superset')
        self.assertEqual(context['candles'][0]['time'], 1001)
        self.assertEqual(context['candles'][-1]['time'], 2000)

    def test_get_market_snapshot_reuses_larger_bridge_snapshot(self):
        state.bridge.history_ready = True
        state.bridge.history_meta = {
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'requested_bars': 5000,
            'first_time': 1,
            'last_time': 5000,
        }
        state.bridge.request['symbol'] = 'EURUSD'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 5000
        state.bridge.candles = [{'time': index} for index in range(1, 5001)]

        context = get_market_snapshot('EURUSD', 'M1', 1000)

        self.assertTrue(context['ready'])
        self.assertEqual(context['source'], 'bridge_superset')
        self.assertEqual(len(context['candles']), 1000)
        self.assertEqual(context['candles'][0]['time'], 4001)
        self.assertEqual(context['candles'][-1]['time'], 5000)

    def test_get_market_snapshot_prefers_fresher_bridge_over_stale_exact_cache(self):
        state.market_data.cache_by_key['EURUSD|M1|1000'] = {
            'cache_key': 'EURUSD|M1|1000',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 1000,
            'revision': 11,
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 1000,
                'bars_loaded': 1000,
                'first_time': 1,
                'last_time': 1000,
                'candles': [{'time': index} for index in range(1, 1001)],
            },
        }
        state.bridge.history_ready = True
        state.bridge.history_meta = {
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'requested_bars': 2000,
            'first_time': 1,
            'last_time': 2000,
        }
        state.bridge.request['symbol'] = 'EURUSD'
        state.bridge.request['timeframe'] = 'M1'
        state.bridge.request['bars'] = 2000
        state.bridge.candles = [{'time': index} for index in range(1, 2001)]

        context = get_market_snapshot('EURUSD', 'M1', 1000)

        self.assertTrue(context['ready'])
        self.assertEqual(context['source'], 'bridge_superset')
        self.assertEqual(context['candles'][0]['time'], 1001)
        self.assertEqual(context['candles'][-1]['time'], 2000)

    def test_get_market_snapshot_marks_exact_partial_cache_as_not_ready(self):
        state.market_data.cache_by_key['EURUSD|M1|1000000'] = {
            'cache_key': 'EURUSD|M1|1000000',
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 1000000,
            'revision': 31,
            'ready': False,
            'status': 'partial',
            'error': 'Bridge cached only 4,763 of 1,000,000 requested candles for EURUSD M1.',
            'snapshot': {
                'symbol': 'EURUSD',
                'timeframe': 'M1',
                'bars_requested': 1000000,
                'bars_loaded': 4763,
                'candles': [{'time': index} for index in range(4763)],
            },
        }
        state.market_data.requests_by_id['mreq_partial'] = {
            'request_id': 'mreq_partial',
            'cache_key': 'EURUSD|M1|1000000',
            'status': 'completed',
            'bars': 1000000,
            'result_ready': False,
        }
        state.market_data.request_order.append('mreq_partial')

        context = get_market_snapshot('EURUSD', 'M1', 1000000)

        self.assertFalse(context['ready'])
        self.assertEqual(context['source'], 'cache_partial')
        self.assertEqual(context['bars_loaded'], 4763)
        self.assertEqual(context['request_status'], 'completed')
        self.assertIn('4,763', context['error'])


if __name__ == '__main__':
    unittest.main()
