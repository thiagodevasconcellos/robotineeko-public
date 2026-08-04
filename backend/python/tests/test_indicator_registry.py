import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.python import indicator_registry


class IndicatorRegistryRefreshTest(unittest.TestCase):
    def test_registry_reloads_when_manifest_file_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'indicatorManifest.json'
            manifest_path.write_text(
                json.dumps([
                    {
                        'name': 'TempOne',
                        'pythonImport': 'backend.python.lib.indicator.trend.ema.EMA',
                    },
                ]),
                encoding='utf-8',
            )

            with patch.object(indicator_registry, 'MANIFEST_PATH', manifest_path):
                indicator_registry.clear_indicator_registry_cache()

                first_class = indicator_registry.get_indicator_class('TempOne')
                self.assertIsNotNone(first_class)
                self.assertEqual(first_class.__name__, 'EMA')
                self.assertIsNone(indicator_registry.get_indicator_class('TempTwo'))

                manifest_path.write_text(
                    json.dumps([
                        {
                            'name': 'TempTwo',
                            'pythonImport': 'backend.python.lib.indicator.features.candlestick_patterns.CandlestickPatterns',
                        },
                    ]),
                    encoding='utf-8',
                )

                second_class = indicator_registry.get_indicator_class('TempTwo')
                self.assertIsNotNone(second_class)
                self.assertEqual(second_class.__name__, 'CandlestickPatterns')
                self.assertIsNone(indicator_registry.get_indicator_class('TempOne'))

                indicator_registry.clear_indicator_registry_cache()


if __name__ == '__main__':
    unittest.main()
