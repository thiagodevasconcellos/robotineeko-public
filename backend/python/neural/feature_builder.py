import numpy as np
import pandas as pd
import ast

try:
    from ..indicator_registry import get_indicator_class, normalize_indicator_feature_name, split_indicator_feature_name
    from ..lib.symbol import Symbol
except ImportError:
    from indicator_registry import get_indicator_class, normalize_indicator_feature_name, split_indicator_feature_name
    from lib.symbol import Symbol


class NeuralFeatureBuilder:
    """Builds feature columns on top of the existing Symbol/indicator stack."""

    BASE_PRICE_COLUMNS = ('time', 'open', 'high', 'low', 'close')

    def __init__(self, symbol: Symbol):
        self.symbol = symbol

    @classmethod
    def from_candles(cls, symbol_name: str, timeframe: str, candles, bars: int | None = None):
        safe_candles = list(candles or [])
        symbol = Symbol(
            name=symbol_name,
            timeframe=timeframe,
            bars=bars or len(safe_candles),
            candles=safe_candles,
        )
        return cls(symbol)

    @classmethod
    def from_symbol(cls, symbol: Symbol):
        return cls(symbol)

    def apply_indicator(self, name: str, params=None):
        indicator_class = get_indicator_class(name)
        if indicator_class is None:
            raise ValueError(f'Unknown indicator: {name}')

        safe_params = list(params or [])
        before_columns = list(self.symbol.candles.columns)
        indicator_class(self.symbol, *safe_params)
        after_columns = list(self.symbol.candles.columns)
        return [column for column in after_columns if column not in before_columns]

    def apply_indicators(self, indicators_payload: list[dict]):
        created_columns = []

        for indicator in indicators_payload or []:
            created_columns.extend(
                self.apply_indicator(
                    indicator.get('name', ''),
                    indicator.get('params', []),
                )
            )

        return created_columns

    def ensure_feature_columns(self, feature_columns: list[str]):
        for feature_name in feature_columns or []:
            if feature_name in self.symbol.features:
                continue

            normalized_feature_name = normalize_indicator_feature_name(feature_name)
            if normalized_feature_name and normalized_feature_name in self._build_normalized_feature_map():
                continue

            indicator_name, raw_params = split_indicator_feature_name(feature_name)
            if indicator_name is None:
                continue

            indicator_class = get_indicator_class(indicator_name)
            if indicator_class is None:
                continue

            parsed_params = [self._parse_indicator_param(param) for param in (raw_params or [])]
            indicator_class(self.symbol, *parsed_params)

        return self

    def resolve_existing_feature_columns(self, feature_columns: list[str]):
        normalized_feature_map = self._build_normalized_feature_map()
        resolved_columns = []

        for feature_name in feature_columns or []:
            if feature_name in self.symbol.features:
                resolved_columns.append(feature_name)
                continue

            normalized_feature_name = normalize_indicator_feature_name(feature_name)
            resolved_columns.append(
                normalized_feature_map.get(normalized_feature_name, feature_name)
            )

        return resolved_columns

    def _build_normalized_feature_map(self):
        mapping = {}
        for column_name in self.symbol.features:
            normalized_name = normalize_indicator_feature_name(column_name)
            if normalized_name and normalized_name not in mapping:
                mapping[normalized_name] = column_name
        return mapping

    def _parse_indicator_param(self, value):
        if not isinstance(value, str):
            return value

        lower = value.lower()
        if lower == 'true':
            return True
        if lower == 'false':
            return False
        if lower == 'none':
            return None

        try:
            return ast.literal_eval(value)
        except Exception:
            return value

    def add_price_returns(self, periods=(1, 2, 5, 10), source='close', prefix='return'):
        source_series = self.symbol[source]

        for period in periods:
            safe_period = int(period)
            column_name = f'{prefix}_{source}_{safe_period}'
            self.symbol.add_feature(column_name, source_series.pct_change(safe_period))

        return self

    def add_log_returns(self, periods=(1, 2, 5, 10), source='close', prefix='log_return'):
        source_series = pd.to_numeric(self.symbol[source], errors='coerce')

        for period in periods:
            safe_period = int(period)
            column_name = f'{prefix}_{source}_{safe_period}'
            self.symbol.add_feature(column_name, np.log(source_series / source_series.shift(safe_period)))

        return self

    def add_candle_geometry_features(self, prefix='candle'):
        candles = self.symbol.candles
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        close_series = pd.to_numeric(candles['close'], errors='coerce')

        body = close_series - open_series
        candle_range = high_series - low_series
        upper_wick = high_series - pd.concat([open_series, close_series], axis=1).max(axis=1)
        lower_wick = pd.concat([open_series, close_series], axis=1).min(axis=1) - low_series

        safe_range = candle_range.replace(0, pd.NA)

        self.symbol.add_feature(f'{prefix}_body', body)
        self.symbol.add_feature(f'{prefix}_range', candle_range)
        self.symbol.add_feature(f'{prefix}_upper_wick', upper_wick)
        self.symbol.add_feature(f'{prefix}_lower_wick', lower_wick)
        self.symbol.add_feature(f'{prefix}_body_ratio', body / safe_range)
        self.symbol.add_feature(f'{prefix}_upper_wick_ratio', upper_wick / safe_range)
        self.symbol.add_feature(f'{prefix}_lower_wick_ratio', lower_wick / safe_range)

        return self

    def add_price_distance_features(self, windows=(5, 10, 20), source='close', prefix='distance'):
        source_series = pd.to_numeric(self.symbol[source], errors='coerce')

        for window in windows:
            safe_window = int(window)
            rolling_mean = source_series.rolling(safe_window).mean()
            column_name = f'{prefix}_{source}_sma_{safe_window}'
            self.symbol.add_feature(column_name, source_series - rolling_mean)

        return self

    def get_feature_columns(self, include_price_columns=False):
        if include_price_columns:
            return list(self.symbol.features)

        return [
            column
            for column in self.symbol.features
            if column not in self.BASE_PRICE_COLUMNS
        ]

    def to_frame(self, feature_columns=None, include_price_columns=False, dropna=False):
        selected_columns = feature_columns or self.get_feature_columns(include_price_columns=include_price_columns)
        frame = self.symbol.candles[['time', *selected_columns]].copy()

        if dropna:
            frame = frame.dropna().reset_index(drop=True)

        return frame
