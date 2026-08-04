import tempfile
import unittest
from pathlib import Path

from backend.python.services import neural_store


class NeuralStoreTest(unittest.TestCase):
    def setUp(self):
        self.original_db_path = neural_store.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        neural_store.DB_PATH = Path(self.temp_dir.name) / 'neural-test.db'
        neural_store.ensure_neural_store()

    def tearDown(self):
        neural_store.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_neural_network_alias_persists_favorite_flag(self):
        payload = neural_store.set_neural_network_alias(
            user_id='user-1',
            network_id='micro_cost_edge_cnn_v1',
            alias='Micro cost watch',
            is_favorite=True,
        )

        self.assertEqual(payload['alias'], 'Micro cost watch')
        self.assertTrue(payload['is_favorite'])
        self.assertFalse(payload['is_deleted'])

        updated = neural_store.set_neural_network_alias(
            user_id='user-1',
            network_id='micro_cost_edge_cnn_v1',
            is_favorite=False,
        )
        self.assertEqual(updated['alias'], 'Micro cost watch')
        self.assertFalse(updated['is_favorite'])

    def test_delete_neural_network_user_state_clears_favorite(self):
        neural_store.set_neural_network_alias(
            user_id='user-1',
            network_id='micro_cost_edge_cnn_v1',
            alias='Micro cost watch',
            is_favorite=True,
        )

        neural_store.delete_neural_network_user_state(
            user_id='user-1',
            network_id='micro_cost_edge_cnn_v1',
        )
        payload = neural_store.get_neural_network_alias('user-1', 'micro_cost_edge_cnn_v1')

        self.assertEqual(payload['alias'], '')
        self.assertTrue(payload['is_deleted'])
        self.assertFalse(payload['is_favorite'])


if __name__ == '__main__':
    unittest.main()
