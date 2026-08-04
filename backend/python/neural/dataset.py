import pandas as pd

try:
    from ..lib.symbol import Symbol
except ImportError:
    from lib.symbol import Symbol


class NeuralDatasetBuilder:
    """Converts a Symbol dataframe into supervised learning datasets."""

    def __init__(self, symbol: Symbol):
        self.symbol = symbol

    def build_target(self, target_source='close', horizon=1, target_mode='direction', target_name='target'):
        safe_horizon = max(1, int(horizon))
        source_series = pd.to_numeric(self.symbol[target_source], errors='coerce')
        future_series = source_series.shift(-safe_horizon)

        if target_mode == 'value':
            target = future_series
        elif target_mode == 'return':
            target = (future_series / source_series) - 1.0
        elif target_mode == 'direction':
            target = (future_series > source_series).astype('Int64')
        else:
            raise ValueError(f'Unsupported target_mode: {target_mode}')

        frame = self.symbol.candles.copy()
        frame[target_name] = target
        return frame

    def build(
        self,
        feature_columns: list[str],
        target_source='close',
        horizon=1,
        target_mode='direction',
        target_name='target',
        dropna=True,
    ):
        frame = self.build_target(
            target_source=target_source,
            horizon=horizon,
            target_mode=target_mode,
            target_name=target_name,
        )

        dataset = frame[['time', *feature_columns, target_name]].copy()

        if dropna:
            dataset = dataset.dropna().reset_index(drop=True)

        return dataset
