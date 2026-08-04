import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def load_docs_backend():
    import backend.python.docs_backend as docs_backend

    return importlib.reload(docs_backend)


def load_neural_backend():
    import backend.python.neural_backend as neural_backend

    return importlib.reload(neural_backend)


def load_auth_service():
    import backend.python.services.auth_service as auth_service

    return importlib.reload(auth_service)


class GuestDocsHardeningTest(unittest.TestCase):
    def test_guest_docs_catalog_is_curated_and_hides_repo_paths(self):
        docs_backend = load_docs_backend()

        guest_user = {
            'is_guest': True,
            'workspace_user_id': 'auth-user:guest',
            'email': '',
        }

        with patch.object(docs_backend, 'require_request_auth', return_value=guest_user):
            payload = docs_backend.get_project_docs(SimpleNamespace())

        self.assertTrue(payload['guest_curated'])
        self.assertEqual(
            [document['id'] for document in payload['documents']],
            [
                'robotineeko-overview',
                'operator-quickstart',
                'public-surfaces-and-access-modes',
                'broker-and-cost-models',
                'research-to-trader-workflow',
            ],
        )
        self.assertTrue(all(document['path'] == '' for document in payload['documents']))


class GuestNeuralHardeningTest(unittest.TestCase):
    def test_guest_neural_scrub_removes_paths_and_owner_metadata(self):
        neural_backend = load_neural_backend()

        scrubbed = neural_backend._scrub_guest_neural_network(
            {
                'active_job': {'id': 'job-1'},
                'best_model': {
                    'model_path': '/private/path/backend/python/data/neural/model.npz',
                    'score': 0.91,
                    'metadata_path': '/private/path/backend/python/data/neural/model.metadata.json',
                },
                'runs': [
                    {
                        'id': 'run-1',
                        'status': 'completed',
                        'error': 'should be cleared',
                        'artifact_path': '/private/path/backend/python/data/neural/run-1/model.npz',
                        'artifact': {
                            'path': '/private/path/backend/python/data/neural/run-1/model.npz',
                            'metadata_path': '/private/path/backend/python/data/neural/run-1/model.metadata.json',
                        },
                        'user_id': 'auth-user:demo-owner',
                        'source_run_id': 'owner-run-1',
                        'runtime_host': 'http://127.0.0.1:8010',
                    },
                ],
            }
        )

        self.assertIsNone(scrubbed['active_job'])
        self.assertEqual(scrubbed['best_model']['model_path'], '')
        self.assertNotIn('metadata_path', scrubbed['best_model'])
        self.assertEqual(len(scrubbed['runs']), 1)
        self.assertEqual(scrubbed['runs'][0]['error'], '')
        self.assertEqual(scrubbed['runs'][0]['runtime_host'], '')
        self.assertNotIn('artifact_path', scrubbed['runs'][0])
        self.assertNotIn('artifact', scrubbed['runs'][0])
        self.assertNotIn('user_id', scrubbed['runs'][0])
        self.assertNotIn('source_run_id', scrubbed['runs'][0])


class GuestAuthPayloadHardeningTest(unittest.TestCase):
    def test_guest_public_payload_hides_internal_email(self):
        auth_service = load_auth_service()

        payload = auth_service.build_public_user_payload(
            {
                'id': 7,
                'email': auth_service.GUEST_EMAIL,
                'workspace_user_id': 'auth-user:7',
                'created_at': 1.0,
                'last_login_at': 2.0,
            }
        )

        self.assertTrue(payload['is_guest'])
        self.assertEqual(payload['display_name'], auth_service.GUEST_DISPLAY_NAME)
        self.assertEqual(payload['email'], '')


if __name__ == '__main__':
    unittest.main()
