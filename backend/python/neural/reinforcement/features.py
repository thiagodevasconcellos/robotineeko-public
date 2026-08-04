import numpy as np
import pandas as pd

try:
    from ..feature_builder import NeuralFeatureBuilder
    from ...indicator_registry import build_indicator_feature_name
    from ...lib.symbol import Symbol
    from .config import RLFeatureConfig
except ImportError:
    from neural.feature_builder import NeuralFeatureBuilder
    from indicator_registry import build_indicator_feature_name
    from lib.symbol import Symbol
    from neural.reinforcement.config import RLFeatureConfig


class VasconcellosRLFeaturePipeline:
    """Builds RL-ready observations from OHLC, volume and Vasconcellos lines."""

    def __init__(self, symbol: Symbol, config: RLFeatureConfig):
        self.symbol = symbol
        self.config = config
        self._builder = NeuralFeatureBuilder.from_symbol(symbol)
        self._applied = False
        self._requested_observation_columns: list[str] = []
        self._observation_columns: list[str] = []

    @classmethod
    def from_candles(cls, config: RLFeatureConfig, candles):
        symbol = Symbol(
            name=config.symbol_name,
            timeframe=config.timeframe,
            bars=config.bars,
            candles=list(candles or []),
        )
        return cls(symbol, config)

    @classmethod
    def from_bridge(cls, config: RLFeatureConfig):
        symbol = Symbol(
            name=config.symbol_name,
            timeframe=config.timeframe,
            bars=config.bars,
        )
        return cls(symbol, config)

    @property
    def observation_columns(self):
        if not self._applied:
            self.apply()
        return list(self._observation_columns)

    @property
    def requested_observation_columns(self):
        if not self._applied:
            self.apply()
        return list(self._requested_observation_columns)

    def _resolve_volume_series(self):
        for candidate in self.config.volume_column_candidates:
            if candidate in self.symbol.candles.columns:
                return pd.to_numeric(self.symbol.candles[candidate], errors='coerce').fillna(0.0)

        return pd.Series(np.zeros(len(self.symbol.candles), dtype=float), index=self.symbol.candles.index)

    def apply(self):
        if self._applied:
            return self

        candles = self.symbol.candles

        for column in ('open', 'high', 'low', 'close'):
            candles[column] = pd.to_numeric(candles[column], errors='coerce')

        if self.config.include_volume:
            candles['volume'] = self._resolve_volume_series()

        vasconcellos_params = self.config.build_vasconcellos_params()
        envelope_base = build_indicator_feature_name('VasconcellosEnvelope', vasconcellos_params)
        self._requested_observation_columns = [
            'open',
            'high',
            'low',
            'close',
            'volume',
            f'{envelope_base}_resistance',
            f'{envelope_base}_support',
            f'{envelope_base}_last_relevant_resistance',
            f'{envelope_base}_last_relevant_support',
        ]
        self._builder.ensure_feature_columns(self._requested_observation_columns)
        self._observation_columns = self._builder.resolve_existing_feature_columns(self._requested_observation_columns)
        self._applied = True
        return self

    def build_training_frame(self, dropna=True, normalize_volume=False):
        if not self._applied:
            self.apply()

        frame = self.symbol.candles[['time', *self._observation_columns]].copy()

        if normalize_volume and 'volume' in frame.columns:
            volume = pd.to_numeric(frame['volume'], errors='coerce')
            volume_std = float(volume.std(ddof=0) or 0.0)
            if volume_std > 0:
                frame['volume'] = (volume - volume.mean()) / volume_std
            else:
                frame['volume'] = 0.0

        if dropna:
            frame = frame.dropna().reset_index(drop=True)

        return frame


class MarketRegimeRLFeaturePipeline:
    """Builds RL-ready observations from OHLCV and MarketRegime state columns."""

    def __init__(self, symbol: Symbol, config: RLFeatureConfig):
        self.symbol = symbol
        self.config = config
        self._builder = NeuralFeatureBuilder.from_symbol(symbol)
        self._applied = False
        self._requested_observation_columns: list[str] = []
        self._observation_columns: list[str] = []

    @classmethod
    def from_candles(cls, config: RLFeatureConfig, candles):
        symbol = Symbol(
            name=config.symbol_name,
            timeframe=config.timeframe,
            bars=config.bars,
            candles=list(candles or []),
        )
        return cls(symbol, config)

    @classmethod
    def from_bridge(cls, config: RLFeatureConfig):
        symbol = Symbol(
            name=config.symbol_name,
            timeframe=config.timeframe,
            bars=config.bars,
        )
        return cls(symbol, config)

    @property
    def observation_columns(self):
        if not self._applied:
            self.apply()
        return list(self._observation_columns)

    @property
    def requested_observation_columns(self):
        if not self._applied:
            self.apply()
        return list(self._requested_observation_columns)

    def _resolve_volume_series(self):
        for candidate in self.config.volume_column_candidates:
            if candidate in self.symbol.candles.columns:
                return pd.to_numeric(self.symbol.candles[candidate], errors='coerce').fillna(0.0)

        return pd.Series(np.zeros(len(self.symbol.candles), dtype=float), index=self.symbol.candles.index)

    def apply(self):
        if self._applied:
            return self

        candles = self.symbol.candles
        for column in ('open', 'high', 'low', 'close'):
            candles[column] = pd.to_numeric(candles[column], errors='coerce')

        if self.config.include_volume:
            candles['volume'] = self._resolve_volume_series()

        market_regime_params = self.config.build_market_regime_params()
        regime_base = build_indicator_feature_name('MarketRegime', market_regime_params)
        self._requested_observation_columns = [
            'open',
            'high',
            'low',
            'close',
            'volume',
            f'{regime_base}_trend_score',
            f'{regime_base}_volatility_score',
            f'{regime_base}_compression_score',
            f'{regime_base}_direction_score',
            f'{regime_base}_stability_score',
            f'{regime_base}_regime_age',
            f'{regime_base}_regime_code',
        ]
        self._builder.ensure_feature_columns(self._requested_observation_columns)
        self._observation_columns = self._builder.resolve_existing_feature_columns(self._requested_observation_columns)
        self._applied = True
        return self

    def build_training_frame(self, dropna=True, normalize_volume=False):
        if not self._applied:
            self.apply()

        frame = self.symbol.candles[['time', *self._observation_columns]].copy()

        if normalize_volume and 'volume' in frame.columns:
            volume = pd.to_numeric(frame['volume'], errors='coerce')
            volume_std = float(volume.std(ddof=0) or 0.0)
            if volume_std > 0:
                frame['volume'] = (volume - volume.mean()) / volume_std
            else:
                frame['volume'] = 0.0

        if dropna:
            frame = frame.dropna().reset_index(drop=True)

        return frame
