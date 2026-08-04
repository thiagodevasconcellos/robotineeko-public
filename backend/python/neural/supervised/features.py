import numpy as np
import pandas as pd

try:
    from ..dataset import NeuralDatasetBuilder
    from ..feature_builder import NeuralFeatureBuilder
    from ...indicator_registry import build_indicator_feature_name
    from ...lib.symbol import Symbol
    from .config import SupervisedFeatureConfig
except ImportError:
    from neural.dataset import NeuralDatasetBuilder
    from neural.feature_builder import NeuralFeatureBuilder
    from indicator_registry import build_indicator_feature_name
    from lib.symbol import Symbol
    from neural.supervised.config import SupervisedFeatureConfig


REGIME_CLASS_CODES = [-3, -2, 0, 1, 2, 3]
REGIME_CLASS_LABELS = {
    -3: 'volatile_down',
    -2: 'trend_down',
    0: 'range',
    1: 'compression',
    2: 'trend_up',
    3: 'volatile_up',
}
REVERSAL_CLASS_CODES = [-1, 0, 1]
REVERSAL_CLASS_LABELS = {
    -1: 'bearish_reversal',
    0: 'no_reversal',
    1: 'bullish_reversal',
}
SETUP_QUALITY_CLASS_CODES = [-1, 0, 1]
SETUP_QUALITY_CLASS_LABELS = {
    -1: 'bad_setup',
    0: 'weak_setup',
    1: 'good_setup',
}
SETUP_QUALITY_BINARY_CLASS_CODES = [-1, 1]
SETUP_QUALITY_BINARY_CLASS_LABELS = {
    -1: 'bad_setup',
    1: 'good_setup',
}
SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES = [0, 1]
SETUP_QUALITY_GOOD_VS_REST_CLASS_LABELS = {
    0: 'not_good_setup',
    1: 'good_setup',
}
SETUP_QUALITY_FIRST_TOUCH_AMBIGUOUS_CODE = 2
MICRO_COST_EDGE_CLASS_CODES = [-1, 0, 1]
MICRO_COST_EDGE_CLASS_LABELS = {
    -1: 'short_edge',
    0: 'no_edge',
    1: 'long_edge',
}
MICRO_COST_EDGE_SIDE_CLASS_CODES = [0, 1]
MICRO_COST_EDGE_SIDE_CLASS_LABELS = {
    0: 'not_edge_for_side',
    1: 'edge_for_side',
}
MICRO_COST_EDGE_FIRST_TOUCH_AMBIGUOUS_CODE = 2
CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE = 2


def _count_target_codes(series: pd.Series):
    numeric = pd.to_numeric(series, errors='coerce')
    counts = {}
    for code in REVERSAL_CLASS_CODES:
        counts[int(code)] = int((numeric == code).sum())
    return counts


def _filter_candle_reversal_target_frame(
    frame: pd.DataFrame,
    *,
    pretrend_threshold: float,
    reversal_threshold: float,
    positive_pretrend_floor: float = 0.0,
    positive_excursion_floor: float = 0.0,
    neutral_pretrend_ceiling: float = 0.0,
    neutral_excursion_ceiling: float = 0.0,
):
    safe_frame = frame.copy()
    applied = any(
        float(value or 0.0) > 0.0
        for value in (
            positive_pretrend_floor,
            positive_excursion_floor,
            neutral_pretrend_ceiling,
            neutral_excursion_ceiling,
        )
    )
    counts_before = _count_target_codes(safe_frame.get('target_reversal_code', pd.Series([], dtype=float)))
    summary = {
        'applied': bool(applied),
        'rows_before': int(len(safe_frame)),
        'rows_after': int(len(safe_frame)),
        'class_counts_before': counts_before,
        'class_counts_after': dict(counts_before),
        'positive_pretrend_floor': float(positive_pretrend_floor or 0.0),
        'positive_excursion_floor': float(positive_excursion_floor or 0.0),
        'neutral_pretrend_ceiling': float(neutral_pretrend_ceiling or 0.0),
        'neutral_excursion_ceiling': float(neutral_excursion_ceiling or 0.0),
    }
    if not applied or safe_frame.empty:
        return safe_frame.reset_index(drop=True), summary

    target_codes = pd.to_numeric(safe_frame['target_reversal_code'], errors='coerce')
    prev_move_atr = pd.to_numeric(safe_frame['target_prev_move_atr'], errors='coerce').abs()
    future_upside_atr = pd.to_numeric(safe_frame['target_future_upside_atr'], errors='coerce')
    future_downside_atr = pd.to_numeric(safe_frame['target_future_downside_atr'], errors='coerce')
    future_excursion_atr = pd.concat([future_upside_atr, future_downside_atr], axis=1).max(axis=1)

    positive_mask = target_codes != 0
    neutral_mask = target_codes == 0

    clean_positive_mask = positive_mask.copy()
    if float(positive_pretrend_floor or 0.0) > 0.0:
        clean_positive_mask &= prev_move_atr >= (float(pretrend_threshold) * float(positive_pretrend_floor))
    if float(positive_excursion_floor or 0.0) > 0.0:
        clean_positive_mask &= future_excursion_atr >= (float(reversal_threshold) * float(positive_excursion_floor))

    clean_neutral_mask = neutral_mask.copy()
    if float(neutral_pretrend_ceiling or 0.0) > 0.0:
        clean_neutral_mask &= prev_move_atr <= (float(pretrend_threshold) * float(neutral_pretrend_ceiling))
    if float(neutral_excursion_ceiling or 0.0) > 0.0:
        clean_neutral_mask &= future_excursion_atr <= (float(reversal_threshold) * float(neutral_excursion_ceiling))

    keep_mask = (clean_positive_mask | clean_neutral_mask).fillna(False)
    filtered = safe_frame.loc[keep_mask].reset_index(drop=True)
    summary['rows_after'] = int(len(filtered))
    summary['class_counts_after'] = _count_target_codes(filtered.get('target_reversal_code', pd.Series([], dtype=float)))
    summary['positive_rows_before'] = int(positive_mask.sum())
    summary['positive_rows_after'] = int((pd.to_numeric(filtered['target_reversal_code'], errors='coerce') != 0).sum()) if not filtered.empty else 0
    summary['neutral_rows_before'] = int(neutral_mask.sum())
    summary['neutral_rows_after'] = int((pd.to_numeric(filtered['target_reversal_code'], errors='coerce') == 0).sum()) if not filtered.empty else 0
    return filtered, summary


class BasicFeedForwardFeaturePipeline:
    def __init__(self, symbol: Symbol, config: SupervisedFeatureConfig):
        self.symbol = symbol
        self.config = config
        self._builder = NeuralFeatureBuilder.from_symbol(symbol)
        self._requested_feature_columns = []
        self._feature_columns = []
        self._applied = False
        self._last_target_filter_summary = None
        self._last_setup_candidate_summary = None
        self._last_candle_reversal_setup_candidate_summary = None

    @classmethod
    def from_candles(cls, config: SupervisedFeatureConfig, candles):
        symbol = Symbol(
            name=config.symbol_name,
            timeframe=config.timeframe,
            bars=config.bars,
            candles=list(candles or []),
        )
        return cls(symbol, config)

    @classmethod
    def from_bridge(cls, config: SupervisedFeatureConfig):
        symbol = Symbol(
            name=config.symbol_name,
            timeframe=config.timeframe,
            bars=config.bars,
        )
        return cls(symbol, config)

    @property
    def feature_columns(self):
        if not self._applied:
            self.apply()
        return list(self._feature_columns)

    @property
    def requested_feature_columns(self):
        if not self._applied:
            self.apply()
        return list(self._requested_feature_columns)

    def _resolve_volume_series(self):
        candles = self.symbol.candles
        for candidate in ('volume', 'tick_volume', 'real_volume'):
            if candidate in candles.columns:
                return pd.to_numeric(candles[candidate], errors='coerce').fillna(0.0)

        return pd.Series(np.zeros(len(candles), dtype=float), index=candles.index)

    @staticmethod
    def _zscore_series(series: pd.Series):
        numeric = pd.to_numeric(series, errors='coerce')
        std = float(numeric.std(ddof=0) or 0.0)
        if std <= 0:
            return pd.Series(np.zeros(len(numeric), dtype=float), index=numeric.index)
        return (numeric - numeric.mean()) / std

    @staticmethod
    def _rolling_zscore(series: pd.Series, window: int):
        numeric = pd.to_numeric(series, errors='coerce')
        rolling_mean = numeric.rolling(window=window, min_periods=window).mean()
        rolling_std = numeric.rolling(window=window, min_periods=window).std(ddof=0).replace(0, np.nan)
        return (numeric - rolling_mean) / rolling_std

    def _resolve_indicator_columns(self, indicator_specs: list[tuple[str, list, str]]):
        requested = [
            build_indicator_feature_name(indicator_name, params, suffix)
            for indicator_name, params, suffix in indicator_specs
        ]
        self._builder.ensure_feature_columns(requested)
        return self._builder.resolve_existing_feature_columns(requested)

    def _apply_indicator_fusion_profile(self, candles: pd.DataFrame):
        indicator_columns = self._resolve_indicator_columns([
            ('EMA', ['close', 9], ''),
            ('EMA', ['close', 21], ''),
            ('RSI', ['close', 7], ''),
            ('RSI', ['close', 14], ''),
            ('ATR', [14], ''),
            ('ADX', [14], ''),
            ('ADX', [14], 'plus_di'),
            ('ADX', [14], 'minus_di'),
            ('MACD', ['close', 12, 26, 9], 'line'),
            ('MACD', ['close', 12, 26, 9], 'signal'),
            ('MACD', ['close', 12, 26, 9], 'histogram'),
            ('BollingerBands', ['close', 20, 2], 'upper'),
            ('BollingerBands', ['close', 20, 2], 'lower'),
            ('BollingerBands', ['close', 20, 2], 'width'),
            ('Stochastic', [14, 3, 3], 'k'),
            ('Stochastic', [14, 3, 3], 'd'),
            ('ROC', ['close', 10], ''),
        ])
        (
            ema_9_column,
            ema_21_column,
            rsi_7_column,
            rsi_14_column,
            atr_14_column,
            adx_14_column,
            plus_di_14_column,
            minus_di_14_column,
            macd_line_column,
            macd_signal_column,
            macd_histogram_column,
            boll_upper_column,
            boll_lower_column,
            boll_width_column,
            stoch_k_column,
            stoch_d_column,
            roc_10_column,
        ) = indicator_columns

        close_series = pd.to_numeric(candles['close'], errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        safe_close = close_series.replace(0, np.nan)
        candle_range = (high_series - low_series).replace(0, np.nan)
        upper_body = pd.concat([open_series, close_series], axis=1).max(axis=1)
        lower_body = pd.concat([open_series, close_series], axis=1).min(axis=1)

        ema_9_series = pd.to_numeric(candles[ema_9_column], errors='coerce')
        ema_21_series = pd.to_numeric(candles[ema_21_column], errors='coerce')
        rsi_7_series = pd.to_numeric(candles[rsi_7_column], errors='coerce')
        rsi_14_series = pd.to_numeric(candles[rsi_14_column], errors='coerce')
        atr_14_series = pd.to_numeric(candles[atr_14_column], errors='coerce')
        adx_14_series = pd.to_numeric(candles[adx_14_column], errors='coerce')
        plus_di_14_series = pd.to_numeric(candles[plus_di_14_column], errors='coerce')
        minus_di_14_series = pd.to_numeric(candles[minus_di_14_column], errors='coerce')
        macd_line_series = pd.to_numeric(candles[macd_line_column], errors='coerce')
        macd_signal_series = pd.to_numeric(candles[macd_signal_column], errors='coerce')
        macd_histogram_series = pd.to_numeric(candles[macd_histogram_column], errors='coerce')
        boll_upper_series = pd.to_numeric(candles[boll_upper_column], errors='coerce')
        boll_lower_series = pd.to_numeric(candles[boll_lower_column], errors='coerce')
        boll_width_series = pd.to_numeric(candles[boll_width_column], errors='coerce')
        stoch_k_series = pd.to_numeric(candles[stoch_k_column], errors='coerce')
        stoch_d_series = pd.to_numeric(candles[stoch_d_column], errors='coerce')
        roc_10_series = pd.to_numeric(candles[roc_10_column], errors='coerce')

        candles['ffx_return_1'] = close_series.pct_change(1)
        candles['ffx_return_3'] = close_series.pct_change(3)
        candles['ffx_return_8'] = close_series.pct_change(8)
        candles['ffx_range_ratio'] = (high_series - low_series) / safe_close
        candles['ffx_body_ratio'] = (close_series - open_series) / candle_range
        candles['ffx_upper_wick_ratio'] = (high_series - upper_body) / candle_range
        candles['ffx_lower_wick_ratio'] = (lower_body - low_series) / candle_range
        candles['ffx_volume_zscore_20'] = self._rolling_zscore(candles['ff_volume'], 20)
        candles['ffx_ema_gap_9_21_ratio'] = (ema_9_series - ema_21_series) / safe_close
        candles['ffx_close_to_ema_9_ratio'] = (close_series - ema_9_series) / safe_close
        candles['ffx_close_to_ema_21_ratio'] = (close_series - ema_21_series) / safe_close
        candles['ffx_atr_14_ratio'] = atr_14_series / safe_close
        candles['ffx_rsi_7'] = rsi_7_series
        candles['ffx_rsi_14'] = rsi_14_series
        candles['ffx_adx_14'] = adx_14_series
        candles['ffx_di_spread_14'] = (plus_di_14_series - minus_di_14_series) / 100.0
        candles['ffx_macd_line'] = macd_line_series / safe_close
        candles['ffx_macd_signal'] = macd_signal_series / safe_close
        candles['ffx_macd_histogram'] = macd_histogram_series / safe_close
        candles['ffx_bb_width_ratio'] = boll_width_series / safe_close
        candles['ffx_bb_position'] = (close_series - boll_lower_series) / (boll_upper_series - boll_lower_series).replace(0, np.nan)
        candles['ffx_stoch_k'] = stoch_k_series
        candles['ffx_stoch_d'] = stoch_d_series
        candles['ffx_roc_10'] = roc_10_series

        self._requested_feature_columns = [
            'ffx_return_1',
            'ffx_return_3',
            'ffx_return_8',
            'ffx_range_ratio',
            'ffx_body_ratio',
            'ffx_upper_wick_ratio',
            'ffx_lower_wick_ratio',
            'ffx_volume_zscore_20',
            'ffx_ema_gap_9_21_ratio',
            'ffx_close_to_ema_9_ratio',
            'ffx_close_to_ema_21_ratio',
            'ffx_atr_14_ratio',
            'ffx_rsi_7',
            'ffx_rsi_14',
            'ffx_adx_14',
            'ffx_di_spread_14',
            'ffx_macd_line',
            'ffx_macd_signal',
            'ffx_macd_histogram',
            'ffx_bb_width_ratio',
            'ffx_bb_position',
            'ffx_stoch_k',
            'ffx_stoch_d',
            'ffx_roc_10',
        ]

    def _apply_market_regime_fusion_profile(self, candles: pd.DataFrame):
        self._apply_indicator_fusion_profile(candles)
        indicator_columns = self._resolve_indicator_columns([
            ('ChoppinessIndex', [14], ''),
            ('ChoppinessIndex', [14], 'trendiness'),
            ('DonchianChannels', [20], 'width'),
            ('Supertrend', [10, 3], 'direction'),
            ('VWAP', ['hlc3'], 'distance_ratio'),
        ])
        (
            choppiness_column,
            trendiness_column,
            donchian_width_column,
            supertrend_direction_column,
            vwap_distance_ratio_column,
        ) = indicator_columns

        close_series = pd.to_numeric(candles['close'], errors='coerce')
        safe_close = close_series.replace(0, np.nan)

        candles['nmr_choppiness_14'] = pd.to_numeric(candles[choppiness_column], errors='coerce') / 100.0
        candles['nmr_trendiness_14'] = pd.to_numeric(candles[trendiness_column], errors='coerce') / 100.0
        candles['nmr_donchian_width_20_ratio'] = pd.to_numeric(candles[donchian_width_column], errors='coerce') / safe_close
        candles['nmr_supertrend_direction'] = pd.to_numeric(candles[supertrend_direction_column], errors='coerce')
        candles['nmr_vwap_distance_ratio'] = pd.to_numeric(candles[vwap_distance_ratio_column], errors='coerce')

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'nmr_choppiness_14',
            'nmr_trendiness_14',
            'nmr_donchian_width_20_ratio',
            'nmr_supertrend_direction',
            'nmr_vwap_distance_ratio',
        ]

    def _apply_candle_reversal_profile(self, candles: pd.DataFrame):
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')

        safe_close = close_series.replace(0, np.nan)
        candle_range = (high_series - low_series).replace(0, np.nan)
        upper_body = pd.concat([open_series, close_series], axis=1).max(axis=1)
        lower_body = pd.concat([open_series, close_series], axis=1).min(axis=1)
        body = close_series - open_series
        body_abs = body.abs()
        upper_wick = (high_series - upper_body).clip(lower=0.0)
        lower_wick = (lower_body - low_series).clip(lower=0.0)
        prev_close = close_series.shift(1)

        atr_ratio = self._build_atr_ratio(close_series, high_series, low_series, period=14)
        true_range_ratio = (
            pd.concat(
                [
                    high_series - low_series,
                    (high_series - prev_close).abs(),
                    (low_series - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            / safe_close
        )

        candles['crx_return_1'] = close_series.pct_change(1)
        candles['crx_return_2'] = close_series.pct_change(2)
        candles['crx_return_3'] = close_series.pct_change(3)
        candles['crx_range_ratio'] = (high_series - low_series) / safe_close
        candles['crx_signed_body_ratio'] = body / candle_range
        candles['crx_upper_wick_ratio'] = upper_wick / candle_range
        candles['crx_lower_wick_ratio'] = lower_wick / candle_range
        candles['crx_close_location'] = (close_series - low_series) / candle_range
        candles['crx_gap_ratio'] = (open_series - prev_close) / safe_close
        candles['crx_true_range_atr_ratio'] = true_range_ratio / atr_ratio.replace(0, np.nan)
        candles['crx_body_to_range_ratio'] = body_abs / candle_range
        candles['crx_wick_imbalance_ratio'] = (lower_wick - upper_wick) / candle_range

        self._requested_feature_columns = [
            'crx_return_1',
            'crx_return_2',
            'crx_return_3',
            'crx_range_ratio',
            'crx_signed_body_ratio',
            'crx_upper_wick_ratio',
            'crx_lower_wick_ratio',
            'crx_close_location',
            'crx_gap_ratio',
            'crx_true_range_atr_ratio',
            'crx_body_to_range_ratio',
            'crx_wick_imbalance_ratio',
        ]

    def _apply_candle_reversal_context_profile(self, candles: pd.DataFrame):
        self._apply_candle_reversal_profile(candles)
        indicator_columns = self._resolve_indicator_columns([
            ('EMA', ['close', 9], ''),
            ('EMA', ['close', 21], ''),
            ('ATR', [14], ''),
            ('ADX', [14], ''),
            ('ADX', [14], 'plus_di'),
            ('ADX', [14], 'minus_di'),
            ('BollingerBands', ['close', 20, 2], 'upper'),
            ('BollingerBands', ['close', 20, 2], 'lower'),
            ('BollingerBands', ['close', 20, 2], 'width'),
        ])
        (
            ema_9_column,
            ema_21_column,
            atr_14_column,
            adx_14_column,
            plus_di_14_column,
            minus_di_14_column,
            boll_upper_column,
            boll_lower_column,
            boll_width_column,
        ) = indicator_columns

        close_series = pd.to_numeric(candles['close'], errors='coerce')
        safe_close = close_series.replace(0, np.nan)

        ema_9_series = pd.to_numeric(candles[ema_9_column], errors='coerce')
        ema_21_series = pd.to_numeric(candles[ema_21_column], errors='coerce')
        atr_14_series = pd.to_numeric(candles[atr_14_column], errors='coerce')
        adx_14_series = pd.to_numeric(candles[adx_14_column], errors='coerce')
        plus_di_14_series = pd.to_numeric(candles[plus_di_14_column], errors='coerce')
        minus_di_14_series = pd.to_numeric(candles[minus_di_14_column], errors='coerce')
        boll_upper_series = pd.to_numeric(candles[boll_upper_column], errors='coerce')
        boll_lower_series = pd.to_numeric(candles[boll_lower_column], errors='coerce')
        boll_width_series = pd.to_numeric(candles[boll_width_column], errors='coerce')

        candles['crx_return_6'] = close_series.pct_change(6)
        candles['crx_volume_zscore_20'] = self._rolling_zscore(candles['ff_volume'], 20)
        candles['crx_ema_gap_9_21_ratio'] = (ema_9_series - ema_21_series) / safe_close
        candles['crx_close_to_ema_9_ratio'] = (close_series - ema_9_series) / safe_close
        candles['crx_close_to_ema_21_ratio'] = (close_series - ema_21_series) / safe_close
        candles['crx_atr_14_ratio'] = atr_14_series / safe_close
        candles['crx_adx_14'] = adx_14_series
        candles['crx_di_spread_14'] = (plus_di_14_series - minus_di_14_series) / 100.0
        candles['crx_bb_width_ratio'] = boll_width_series / safe_close
        candles['crx_bb_position'] = (
            (close_series - boll_lower_series)
            / (boll_upper_series - boll_lower_series).replace(0, np.nan)
        )

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'crx_return_6',
            'crx_volume_zscore_20',
            'crx_ema_gap_9_21_ratio',
            'crx_close_to_ema_9_ratio',
            'crx_close_to_ema_21_ratio',
            'crx_atr_14_ratio',
            'crx_adx_14',
            'crx_di_spread_14',
            'crx_bb_width_ratio',
            'crx_bb_position',
        ]

    def _apply_candle_reversal_pattern_score_context_profile(self, candles: pd.DataFrame):
        self._apply_candle_reversal_context_profile(candles)
        indicator_columns = self._resolve_indicator_columns([
            ('CandlestickPatterns', [5, 14], 'bullish_reversal_score'),
            ('CandlestickPatterns', [5, 14], 'bearish_reversal_score'),
            ('CandlestickPatterns', [5, 14], 'bullish_continuation_score'),
            ('CandlestickPatterns', [5, 14], 'bearish_continuation_score'),
        ])
        (
            bullish_reversal_score_column,
            bearish_reversal_score_column,
            bullish_continuation_score_column,
            bearish_continuation_score_column,
        ) = indicator_columns

        candles['crxp_bullish_reversal_score'] = pd.to_numeric(candles[bullish_reversal_score_column], errors='coerce')
        candles['crxp_bearish_reversal_score'] = pd.to_numeric(candles[bearish_reversal_score_column], errors='coerce')
        candles['crxp_bullish_continuation_score'] = pd.to_numeric(candles[bullish_continuation_score_column], errors='coerce')
        candles['crxp_bearish_continuation_score'] = pd.to_numeric(candles[bearish_continuation_score_column], errors='coerce')

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'crxp_bullish_reversal_score',
            'crxp_bearish_reversal_score',
            'crxp_bullish_continuation_score',
            'crxp_bearish_continuation_score',
        ]

    def _apply_candle_reversal_pattern_context_profile(self, candles: pd.DataFrame):
        self._apply_candle_reversal_pattern_score_context_profile(candles)
        indicator_columns = self._resolve_indicator_columns([
            ('CandlestickPatterns', [5, 14], 'hammer'),
            ('CandlestickPatterns', [5, 14], 'shooting_star'),
            ('CandlestickPatterns', [5, 14], 'bullish_engulfing'),
            ('CandlestickPatterns', [5, 14], 'bearish_engulfing'),
            ('CandlestickPatterns', [5, 14], 'bullish_harami'),
            ('CandlestickPatterns', [5, 14], 'bearish_harami'),
            ('CandlestickPatterns', [5, 14], 'morning_star'),
            ('CandlestickPatterns', [5, 14], 'evening_star'),
            ('CandlestickPatterns', [5, 14], 'rising_three_methods'),
            ('CandlestickPatterns', [5, 14], 'falling_three_methods'),
        ])
        (
            hammer_column,
            shooting_star_column,
            bullish_engulfing_column,
            bearish_engulfing_column,
            bullish_harami_column,
            bearish_harami_column,
            morning_star_column,
            evening_star_column,
            rising_three_methods_column,
            falling_three_methods_column,
        ) = indicator_columns

        candles['crxp_hammer'] = pd.to_numeric(candles[hammer_column], errors='coerce')
        candles['crxp_shooting_star'] = pd.to_numeric(candles[shooting_star_column], errors='coerce')
        candles['crxp_bullish_engulfing'] = pd.to_numeric(candles[bullish_engulfing_column], errors='coerce')
        candles['crxp_bearish_engulfing'] = pd.to_numeric(candles[bearish_engulfing_column], errors='coerce')
        candles['crxp_bullish_harami'] = pd.to_numeric(candles[bullish_harami_column], errors='coerce')
        candles['crxp_bearish_harami'] = pd.to_numeric(candles[bearish_harami_column], errors='coerce')
        candles['crxp_morning_star'] = pd.to_numeric(candles[morning_star_column], errors='coerce')
        candles['crxp_evening_star'] = pd.to_numeric(candles[evening_star_column], errors='coerce')
        candles['crxp_rising_three_methods'] = pd.to_numeric(candles[rising_three_methods_column], errors='coerce')
        candles['crxp_falling_three_methods'] = pd.to_numeric(candles[falling_three_methods_column], errors='coerce')

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'crxp_hammer',
            'crxp_shooting_star',
            'crxp_bullish_engulfing',
            'crxp_bearish_engulfing',
            'crxp_bullish_harami',
            'crxp_bearish_harami',
            'crxp_morning_star',
            'crxp_evening_star',
            'crxp_rising_three_methods',
            'crxp_falling_three_methods',
        ]

    def _apply_ema_low_adx_setup_quality_pattern_score_context_profile(self, candles: pd.DataFrame):
        self._apply_ema_low_adx_setup_quality_profile(candles)
        indicator_columns = self._resolve_indicator_columns([
            ('CandlestickPatterns', [5, 14], 'bullish_reversal_score'),
            ('CandlestickPatterns', [5, 14], 'bearish_reversal_score'),
            ('CandlestickPatterns', [5, 14], 'bullish_continuation_score'),
            ('CandlestickPatterns', [5, 14], 'bearish_continuation_score'),
        ])
        (
            bullish_reversal_score_column,
            bearish_reversal_score_column,
            bullish_continuation_score_column,
            bearish_continuation_score_column,
        ) = indicator_columns

        candles['slqp_bullish_reversal_score'] = pd.to_numeric(candles[bullish_reversal_score_column], errors='coerce')
        candles['slqp_bearish_reversal_score'] = pd.to_numeric(candles[bearish_reversal_score_column], errors='coerce')
        candles['slqp_bullish_continuation_score'] = pd.to_numeric(candles[bullish_continuation_score_column], errors='coerce')
        candles['slqp_bearish_continuation_score'] = pd.to_numeric(candles[bearish_continuation_score_column], errors='coerce')

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'slqp_bullish_reversal_score',
            'slqp_bearish_reversal_score',
            'slqp_bullish_continuation_score',
            'slqp_bearish_continuation_score',
        ]

    @staticmethod
    def _build_previous_candidate_distance_ratio(candidate_mask: pd.Series, cap_bars: int):
        safe_cap = max(1, int(cap_bars or 1))
        safe_mask = candidate_mask.fillna(False).astype(bool)
        candidate_indexes = np.where(safe_mask.to_numpy(dtype=bool), np.arange(len(safe_mask.index), dtype=float), np.nan)
        previous_candidate_index = pd.Series(candidate_indexes, index=safe_mask.index, dtype=float).shift(1).ffill()
        current_index = pd.Series(np.arange(len(safe_mask.index), dtype=float), index=safe_mask.index, dtype=float)
        distance = current_index - previous_candidate_index
        normalized = distance.clip(lower=0.0, upper=float(safe_cap)) / float(safe_cap)
        return normalized.fillna(1.0)

    @staticmethod
    def _build_previous_candidate_value(candidate_mask: pd.Series, value_series: pd.Series):
        safe_mask = candidate_mask.fillna(False).astype(bool)
        safe_value = pd.to_numeric(value_series, errors='coerce')
        return safe_value.where(safe_mask).shift(1).ffill().fillna(0.0)

    @staticmethod
    def _build_recent_candidate_aggregate(
        candidate_mask: pd.Series,
        value_series: pd.Series,
        *,
        window_bars: int,
        reducer: str,
    ):
        safe_window = max(1, int(window_bars or 1))
        safe_mask = candidate_mask.fillna(False).astype(bool)
        masked = pd.to_numeric(value_series, errors='coerce').where(safe_mask)
        rolling = masked.rolling(window=safe_window, min_periods=1)
        if reducer == 'max':
            aggregated = rolling.max()
        elif reducer == 'mean':
            aggregated = rolling.mean()
        else:
            raise ValueError(f'Unsupported candidate aggregate reducer: {reducer}')
        return aggregated.shift(1).fillna(0.0)

    def _apply_ema_low_adx_setup_quality_pattern_cluster_context_profile(self, candles: pd.DataFrame):
        self._apply_ema_low_adx_setup_quality_pattern_score_context_profile(candles)

        close_series = pd.to_numeric(candles['close'], errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        indicator_columns = self._resolve_indicator_columns([
            ('EMA', ['close', 9], ''),
            ('ATR', [14], ''),
            ('ADX', [14], ''),
            ('RSI', ['close', 14], ''),
            ('BollingerBands', ['close', 20, 2], 'middle'),
            ('BollingerBands', ['close', 20, 2], 'lower'),
        ])
        (
            ema_9_column,
            atr_14_column,
            adx_14_column,
            rsi_14_column,
            boll_middle_column,
            boll_lower_column,
        ) = indicator_columns

        ema_9_series = pd.to_numeric(candles[ema_9_column], errors='coerce')
        atr_14_series = pd.to_numeric(candles[atr_14_column], errors='coerce').replace(0, np.nan)
        adx_14_series = pd.to_numeric(candles[adx_14_column], errors='coerce')
        rsi_14_series = pd.to_numeric(candles[rsi_14_column], errors='coerce')
        boll_middle_series = pd.to_numeric(candles[boll_middle_column], errors='coerce')
        boll_lower_series = pd.to_numeric(candles[boll_lower_column], errors='coerce')

        prev_close = close_series.shift(1)
        prev_rsi = rsi_14_series.shift(1)
        prev_boll_lower = boll_lower_series.shift(1)
        prev_atr = atr_14_series.shift(1)
        reclaim_base = (boll_middle_series - ema_9_series).clip(lower=0.0)

        setup_adx_ceiling = float(getattr(self.config, 'setup_adx_ceiling', 28.0) or 28.0)
        setup_prev_rsi_ceiling = float(getattr(self.config, 'setup_prev_rsi_ceiling', 38.0) or 38.0)
        setup_current_rsi_floor = float(getattr(self.config, 'setup_current_rsi_floor', 38.0) or 38.0)
        setup_current_rsi_ceiling = float(getattr(self.config, 'setup_current_rsi_ceiling', 50.0) or 50.0)
        setup_touch_slack_atr = float(getattr(self.config, 'setup_touch_slack_atr', 0.06) or 0.0)
        setup_prev_band_slack_atr = float(getattr(self.config, 'setup_prev_band_slack_atr', 0.08) or 0.0)
        setup_bounce_fraction = float(getattr(self.config, 'setup_bounce_fraction', 0.02) or 0.0)

        base_candidate_mask = (
            (low_series <= (boll_lower_series + (atr_14_series * setup_touch_slack_atr)))
            & (prev_close <= (prev_boll_lower + (prev_atr * setup_prev_band_slack_atr)))
            & (close_series >= (ema_9_series + (reclaim_base * setup_bounce_fraction)))
            & (close_series > open_series)
            & (prev_rsi <= setup_prev_rsi_ceiling)
            & (rsi_14_series >= setup_current_rsi_floor)
            & (rsi_14_series <= setup_current_rsi_ceiling)
            & (adx_14_series <= setup_adx_ceiling)
        ).fillna(False)

        di_spread_series = pd.to_numeric(candles['slq_di_spread_14'], errors='coerce').fillna(0.0)
        reclaim_strength_series = pd.to_numeric(candles['slq_reclaim_strength'], errors='coerce').fillna(0.0)
        base_candidate_float = base_candidate_mask.astype(float)
        recent_candidate_count_12 = base_candidate_float.rolling(window=12, min_periods=1).sum().shift(1).fillna(0.0)
        recent_candidate_count_24 = base_candidate_float.rolling(window=24, min_periods=1).sum().shift(1).fillna(0.0)
        recent_candidate_max_di_12 = self._build_recent_candidate_aggregate(
            base_candidate_mask,
            di_spread_series,
            window_bars=12,
            reducer='max',
        )
        recent_candidate_mean_di_12 = self._build_recent_candidate_aggregate(
            base_candidate_mask,
            di_spread_series,
            window_bars=12,
            reducer='mean',
        )
        recent_candidate_max_reclaim_12 = self._build_recent_candidate_aggregate(
            base_candidate_mask,
            reclaim_strength_series,
            window_bars=12,
            reducer='max',
        )
        previous_candidate_di = self._build_previous_candidate_value(base_candidate_mask, di_spread_series)

        candles['slqc_base_candidate_flag'] = base_candidate_float
        candles['slqc_prev_candidate_gap_24'] = self._build_previous_candidate_distance_ratio(base_candidate_mask, 24)
        candles['slqc_recent_candidate_density_12'] = recent_candidate_count_12 / 12.0
        candles['slqc_recent_candidate_density_24'] = recent_candidate_count_24 / 24.0
        candles['slqc_last_candidate_di_spread'] = previous_candidate_di
        candles['slqc_di_vs_last_candidate'] = di_spread_series - previous_candidate_di
        candles['slqc_di_vs_recent_candidate_max_12'] = di_spread_series - recent_candidate_max_di_12
        candles['slqc_di_vs_recent_candidate_mean_12'] = di_spread_series - recent_candidate_mean_di_12
        candles['slqc_reclaim_vs_recent_candidate_max_12'] = reclaim_strength_series - recent_candidate_max_reclaim_12

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'slqc_base_candidate_flag',
            'slqc_prev_candidate_gap_24',
            'slqc_recent_candidate_density_12',
            'slqc_recent_candidate_density_24',
            'slqc_last_candidate_di_spread',
            'slqc_di_vs_last_candidate',
            'slqc_di_vs_recent_candidate_max_12',
            'slqc_di_vs_recent_candidate_mean_12',
            'slqc_reclaim_vs_recent_candidate_max_12',
        ]

    def _apply_ema_low_adx_setup_quality_profile(self, candles: pd.DataFrame):
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        safe_close = close_series.replace(0, np.nan)
        candle_range = (high_series - low_series).replace(0, np.nan)
        upper_body = pd.concat([open_series, close_series], axis=1).max(axis=1)
        lower_body = pd.concat([open_series, close_series], axis=1).min(axis=1)
        body = close_series - open_series
        upper_wick = (high_series - upper_body).clip(lower=0.0)
        lower_wick = (lower_body - low_series).clip(lower=0.0)

        indicator_columns = self._resolve_indicator_columns([
            ('EMA', ['close', 9], ''),
            ('EMA', ['close', 21], ''),
            ('ATR', [14], ''),
            ('ADX', [14], ''),
            ('ADX', [14], 'plus_di'),
            ('ADX', [14], 'minus_di'),
            ('RSI', ['close', 14], ''),
            ('BollingerBands', ['close', 20, 2], 'upper'),
            ('BollingerBands', ['close', 20, 2], 'middle'),
            ('BollingerBands', ['close', 20, 2], 'lower'),
            ('BollingerBands', ['close', 20, 2], 'width'),
        ])
        (
            ema_9_column,
            ema_21_column,
            atr_14_column,
            adx_14_column,
            plus_di_14_column,
            minus_di_14_column,
            rsi_14_column,
            boll_upper_column,
            boll_middle_column,
            boll_lower_column,
            boll_width_column,
        ) = indicator_columns

        ema_9_series = pd.to_numeric(candles[ema_9_column], errors='coerce')
        ema_21_series = pd.to_numeric(candles[ema_21_column], errors='coerce')
        atr_14_series = pd.to_numeric(candles[atr_14_column], errors='coerce').replace(0, np.nan)
        adx_14_series = pd.to_numeric(candles[adx_14_column], errors='coerce')
        plus_di_14_series = pd.to_numeric(candles[plus_di_14_column], errors='coerce')
        minus_di_14_series = pd.to_numeric(candles[minus_di_14_column], errors='coerce')
        rsi_14_series = pd.to_numeric(candles[rsi_14_column], errors='coerce')
        boll_upper_series = pd.to_numeric(candles[boll_upper_column], errors='coerce')
        boll_middle_series = pd.to_numeric(candles[boll_middle_column], errors='coerce')
        boll_lower_series = pd.to_numeric(candles[boll_lower_column], errors='coerce')
        boll_width_series = pd.to_numeric(candles[boll_width_column], errors='coerce')

        reclaim_base = (boll_middle_series - ema_9_series).clip(lower=0.0)
        reclaim_denominator = reclaim_base.replace(0, np.nan)

        candles['slq_return_1'] = close_series.pct_change(1)
        candles['slq_return_3'] = close_series.pct_change(3)
        candles['slq_return_6'] = close_series.pct_change(6)
        candles['slq_body_ratio'] = body / candle_range
        candles['slq_upper_wick_ratio'] = upper_wick / candle_range
        candles['slq_lower_wick_ratio'] = lower_wick / candle_range
        candles['slq_close_location'] = (close_series - low_series) / candle_range
        candles['slq_volume_zscore_20'] = self._rolling_zscore(candles['ff_volume'], 20)
        candles['slq_close_to_ema_9_ratio'] = (close_series - ema_9_series) / safe_close
        candles['slq_close_to_ema_21_ratio'] = (close_series - ema_21_series) / safe_close
        candles['slq_ema_gap_9_21_ratio'] = (ema_9_series - ema_21_series) / safe_close
        candles['slq_atr_14_ratio'] = atr_14_series / safe_close
        candles['slq_rsi_14'] = rsi_14_series
        candles['slq_rsi_delta_1'] = rsi_14_series - rsi_14_series.shift(1)
        candles['slq_rsi_delta_3'] = rsi_14_series - rsi_14_series.shift(3)
        candles['slq_adx_14'] = adx_14_series
        candles['slq_di_spread_14'] = (plus_di_14_series - minus_di_14_series) / 100.0
        candles['slq_bb_width_ratio'] = boll_width_series / safe_close
        candles['slq_bb_position'] = (close_series - boll_lower_series) / (boll_upper_series - boll_lower_series).replace(0, np.nan)
        candles['slq_low_to_bb_lower_atr'] = (low_series - boll_lower_series) / atr_14_series
        candles['slq_close_to_bb_lower_atr'] = (close_series - boll_lower_series) / atr_14_series
        candles['slq_close_to_bb_middle_ratio'] = (close_series - boll_middle_series) / safe_close
        candles['slq_reclaim_strength'] = (close_series - ema_9_series) / reclaim_denominator

        self._requested_feature_columns = [
            'slq_return_1',
            'slq_return_3',
            'slq_return_6',
            'slq_body_ratio',
            'slq_upper_wick_ratio',
            'slq_lower_wick_ratio',
            'slq_close_location',
            'slq_volume_zscore_20',
            'slq_close_to_ema_9_ratio',
            'slq_close_to_ema_21_ratio',
            'slq_ema_gap_9_21_ratio',
            'slq_atr_14_ratio',
            'slq_rsi_14',
            'slq_rsi_delta_1',
            'slq_rsi_delta_3',
            'slq_adx_14',
            'slq_di_spread_14',
            'slq_bb_width_ratio',
            'slq_bb_position',
            'slq_low_to_bb_lower_atr',
            'slq_close_to_bb_lower_atr',
            'slq_close_to_bb_middle_ratio',
            'slq_reclaim_strength',
        ]

    def _apply_micro_cost_edge_profile(self, candles: pd.DataFrame):
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        safe_close = close_series.replace(0, np.nan)
        candle_range = (high_series - low_series).replace(0, np.nan)
        upper_body = pd.concat([open_series, close_series], axis=1).max(axis=1)
        lower_body = pd.concat([open_series, close_series], axis=1).min(axis=1)
        body = close_series - open_series
        upper_wick = (high_series - upper_body).clip(lower=0.0)
        lower_wick = (lower_body - low_series).clip(lower=0.0)

        indicator_columns = self._resolve_indicator_columns([
            ('EMA', ['close', 9], ''),
            ('EMA', ['close', 21], ''),
            ('ATR', [14], ''),
            ('ADX', [14], ''),
            ('ADX', [14], 'plus_di'),
            ('ADX', [14], 'minus_di'),
            ('RSI', ['close', 7], ''),
            ('RSI', ['close', 14], ''),
            ('BollingerBands', ['close', 20, 2], 'upper'),
            ('BollingerBands', ['close', 20, 2], 'lower'),
            ('BollingerBands', ['close', 20, 2], 'width'),
            ('ChoppinessIndex', [14], ''),
            ('ChoppinessIndex', [14], 'trendiness'),
            ('VWAP', ['hlc3'], 'distance_ratio'),
        ])
        (
            ema_9_column,
            ema_21_column,
            atr_14_column,
            adx_14_column,
            plus_di_14_column,
            minus_di_14_column,
            rsi_7_column,
            rsi_14_column,
            boll_upper_column,
            boll_lower_column,
            boll_width_column,
            choppiness_column,
            trendiness_column,
            vwap_distance_ratio_column,
        ) = indicator_columns

        ema_9_series = pd.to_numeric(candles[ema_9_column], errors='coerce')
        ema_21_series = pd.to_numeric(candles[ema_21_column], errors='coerce')
        atr_14_series = pd.to_numeric(candles[atr_14_column], errors='coerce').replace(0, np.nan)
        adx_14_series = pd.to_numeric(candles[adx_14_column], errors='coerce')
        plus_di_14_series = pd.to_numeric(candles[plus_di_14_column], errors='coerce')
        minus_di_14_series = pd.to_numeric(candles[minus_di_14_column], errors='coerce')
        rsi_7_series = pd.to_numeric(candles[rsi_7_column], errors='coerce')
        rsi_14_series = pd.to_numeric(candles[rsi_14_column], errors='coerce')
        boll_upper_series = pd.to_numeric(candles[boll_upper_column], errors='coerce')
        boll_lower_series = pd.to_numeric(candles[boll_lower_column], errors='coerce')
        boll_width_series = pd.to_numeric(candles[boll_width_column], errors='coerce')
        choppiness_series = pd.to_numeric(candles[choppiness_column], errors='coerce')
        trendiness_series = pd.to_numeric(candles[trendiness_column], errors='coerce')
        vwap_distance_ratio_series = pd.to_numeric(candles[vwap_distance_ratio_column], errors='coerce')

        pip_size = max(1e-8, float(getattr(self.config, 'pip_size', 0.0001) or 0.0001))
        round_trip_cost_pips = max(0.0, float(getattr(self.config, 'round_trip_cost_pips', 1.6) or 0.0))
        round_trip_cost_price = pip_size * round_trip_cost_pips
        recent_range_5 = (
            high_series.rolling(window=5, min_periods=5).max()
            - low_series.rolling(window=5, min_periods=5).min()
        )

        candles['mce_return_1'] = close_series.pct_change(1)
        candles['mce_return_2'] = close_series.pct_change(2)
        candles['mce_return_3'] = close_series.pct_change(3)
        candles['mce_range_ratio'] = candle_range / safe_close
        candles['mce_body_ratio'] = body / candle_range
        candles['mce_upper_wick_ratio'] = upper_wick / candle_range
        candles['mce_lower_wick_ratio'] = lower_wick / candle_range
        candles['mce_close_location'] = (close_series - low_series) / candle_range
        candles['mce_volume_zscore_20'] = self._rolling_zscore(candles['ff_volume'], 20)
        candles['mce_ema_gap_9_21_ratio'] = (ema_9_series - ema_21_series) / safe_close
        candles['mce_close_to_ema_9_ratio'] = (close_series - ema_9_series) / safe_close
        candles['mce_close_to_ema_21_ratio'] = (close_series - ema_21_series) / safe_close
        candles['mce_atr_14_ratio'] = atr_14_series / safe_close
        candles['mce_atr_slope_3'] = (atr_14_series / atr_14_series.shift(3)) - 1.0
        candles['mce_rsi_7'] = rsi_7_series
        candles['mce_rsi_14'] = rsi_14_series
        candles['mce_rsi_delta_1'] = rsi_14_series - rsi_14_series.shift(1)
        candles['mce_adx_14'] = adx_14_series
        candles['mce_di_spread_14'] = (plus_di_14_series - minus_di_14_series) / 100.0
        candles['mce_bb_width_ratio'] = boll_width_series / safe_close
        candles['mce_bb_position'] = (
            (close_series - boll_lower_series)
            / (boll_upper_series - boll_lower_series).replace(0, np.nan)
        )
        candles['mce_choppiness_14'] = choppiness_series / 100.0
        candles['mce_trendiness_14'] = trendiness_series / 100.0
        candles['mce_vwap_distance_ratio'] = vwap_distance_ratio_series
        candles['mce_recent_range_atr_5'] = recent_range_5 / atr_14_series
        candles['mce_recent_move_atr_3'] = (close_series - close_series.shift(3)) / atr_14_series
        candles['mce_cost_to_atr_14'] = round_trip_cost_price / atr_14_series
        candles['mce_cost_to_range'] = round_trip_cost_price / candle_range

        self._requested_feature_columns = [
            'mce_return_1',
            'mce_return_2',
            'mce_return_3',
            'mce_range_ratio',
            'mce_body_ratio',
            'mce_upper_wick_ratio',
            'mce_lower_wick_ratio',
            'mce_close_location',
            'mce_volume_zscore_20',
            'mce_ema_gap_9_21_ratio',
            'mce_close_to_ema_9_ratio',
            'mce_close_to_ema_21_ratio',
            'mce_atr_14_ratio',
            'mce_atr_slope_3',
            'mce_rsi_7',
            'mce_rsi_14',
            'mce_rsi_delta_1',
            'mce_adx_14',
            'mce_di_spread_14',
            'mce_bb_width_ratio',
            'mce_bb_position',
            'mce_choppiness_14',
            'mce_trendiness_14',
            'mce_vwap_distance_ratio',
            'mce_recent_range_atr_5',
            'mce_recent_move_atr_3',
            'mce_cost_to_atr_14',
            'mce_cost_to_range',
        ]

    def _apply_micro_cost_edge_pattern_score_context_profile(self, candles: pd.DataFrame):
        self._apply_micro_cost_edge_profile(candles)
        indicator_columns = self._resolve_indicator_columns([
            ('CandlestickPatterns', [5, 14], 'bullish_reversal_score'),
            ('CandlestickPatterns', [5, 14], 'bearish_reversal_score'),
            ('CandlestickPatterns', [5, 14], 'bullish_continuation_score'),
            ('CandlestickPatterns', [5, 14], 'bearish_continuation_score'),
        ])
        (
            bullish_reversal_score_column,
            bearish_reversal_score_column,
            bullish_continuation_score_column,
            bearish_continuation_score_column,
        ) = indicator_columns

        candles['mcep_bullish_reversal_score'] = pd.to_numeric(candles[bullish_reversal_score_column], errors='coerce')
        candles['mcep_bearish_reversal_score'] = pd.to_numeric(candles[bearish_reversal_score_column], errors='coerce')
        candles['mcep_bullish_continuation_score'] = pd.to_numeric(candles[bullish_continuation_score_column], errors='coerce')
        candles['mcep_bearish_continuation_score'] = pd.to_numeric(candles[bearish_continuation_score_column], errors='coerce')

        self._requested_feature_columns = list(self._requested_feature_columns) + [
            'mcep_bullish_reversal_score',
            'mcep_bearish_reversal_score',
            'mcep_bullish_continuation_score',
            'mcep_bearish_continuation_score',
        ]

    @staticmethod
    def _build_future_extrema(high_series: pd.Series, low_series: pd.Series, horizon: int):
        future_high = pd.Series(np.nan, index=high_series.index, dtype=float)
        future_low = pd.Series(np.nan, index=low_series.index, dtype=float)
        for offset in range(1, horizon + 1):
            shifted_high = high_series.shift(-offset)
            shifted_low = low_series.shift(-offset)
            future_high = shifted_high if offset == 1 else np.maximum(future_high, shifted_high)
            future_low = shifted_low if offset == 1 else np.minimum(future_low, shifted_low)
        return future_high, future_low

    @staticmethod
    def _build_future_first_touch_codes(
        close_series: pd.Series,
        high_series: pd.Series,
        low_series: pd.Series,
        atr_series: pd.Series,
        horizon: int,
        good_excursion_threshold: float,
        bad_excursion_threshold: float,
    ):
        safe_close = pd.to_numeric(close_series, errors='coerce').to_numpy(dtype=float)
        safe_high = pd.to_numeric(high_series, errors='coerce').to_numpy(dtype=float)
        safe_low = pd.to_numeric(low_series, errors='coerce').to_numpy(dtype=float)
        safe_atr = pd.to_numeric(atr_series, errors='coerce').to_numpy(dtype=float)
        total_rows = len(safe_close)

        quality_codes = np.zeros(total_rows, dtype=int)
        resolution_codes = np.zeros(total_rows, dtype=int)
        max_horizon = max(1, int(horizon))
        good_threshold = max(0.0, float(good_excursion_threshold))
        bad_threshold = max(0.0, float(bad_excursion_threshold))

        for index in range(total_rows):
            entry_close = safe_close[index]
            atr_value = safe_atr[index]
            if not np.isfinite(entry_close) or not np.isfinite(atr_value) or atr_value <= 0.0:
                continue

            up_target = entry_close + (atr_value * good_threshold)
            down_target = entry_close - (atr_value * bad_threshold)

            for offset in range(1, max_horizon + 1):
                future_index = index + offset
                if future_index >= total_rows:
                    break

                bar_high = safe_high[future_index]
                bar_low = safe_low[future_index]
                if not np.isfinite(bar_high) or not np.isfinite(bar_low):
                    break

                hit_good = bar_high >= up_target
                hit_bad = bar_low <= down_target

                if hit_good and hit_bad:
                    resolution_codes[index] = SETUP_QUALITY_FIRST_TOUCH_AMBIGUOUS_CODE
                    break
                if hit_good:
                    quality_codes[index] = 1
                    resolution_codes[index] = 1
                    break
                if hit_bad:
                    quality_codes[index] = -1
                    resolution_codes[index] = -1
                    break

        return (
            pd.Series(quality_codes, index=close_series.index, dtype=int),
            pd.Series(resolution_codes, index=close_series.index, dtype=int),
        )

    @staticmethod
    def _dedupe_candidate_mask_by_priority(
        candidate_mask: pd.Series,
        priority_series: pd.Series,
        *,
        min_gap_bars: int,
    ):
        safe_gap = max(0, int(min_gap_bars or 0))
        safe_mask = candidate_mask.fillna(False).astype(bool)
        if safe_gap <= 0 or not bool(safe_mask.any()):
            return safe_mask

        safe_priority = (
            pd.to_numeric(priority_series, errors='coerce')
            .reindex(safe_mask.index)
            .fillna(float('-inf'))
        )
        candidate_positions = np.flatnonzero(safe_mask.to_numpy(dtype=bool))
        ranked_positions = sorted(
            (int(position) for position in candidate_positions),
            key=lambda position: (-float(safe_priority.iloc[position]), int(position)),
        )

        kept_positions: list[int] = []
        for position in ranked_positions:
            if any(abs(position - previous_position) < safe_gap for previous_position in kept_positions):
                continue
            kept_positions.append(position)

        deduped_mask = np.zeros(len(safe_mask.index), dtype=bool)
        if kept_positions:
            deduped_mask[kept_positions] = True
        return pd.Series(deduped_mask, index=safe_mask.index, dtype=bool)

    @staticmethod
    def _build_future_directional_tp_sl_codes(
        close_series: pd.Series,
        high_series: pd.Series,
        low_series: pd.Series,
        atr_series: pd.Series,
        horizon: int,
        take_profit_threshold: float,
        stop_loss_threshold: float,
        *,
        direction: str,
    ):
        safe_close = pd.to_numeric(close_series, errors='coerce').to_numpy(dtype=float)
        safe_high = pd.to_numeric(high_series, errors='coerce').to_numpy(dtype=float)
        safe_low = pd.to_numeric(low_series, errors='coerce').to_numpy(dtype=float)
        safe_atr = pd.to_numeric(atr_series, errors='coerce').to_numpy(dtype=float)
        total_rows = len(safe_close)

        outcome_codes = np.zeros(total_rows, dtype=int)
        resolution_codes = np.zeros(total_rows, dtype=int)
        max_horizon = max(1, int(horizon))
        take_profit_multiple = max(0.0, float(take_profit_threshold))
        stop_loss_multiple = max(0.0, float(stop_loss_threshold))
        side = str(direction or 'bullish').strip().lower()
        is_bullish = side != 'bearish'

        for index in range(total_rows):
            entry_close = safe_close[index]
            atr_value = safe_atr[index]
            if not np.isfinite(entry_close) or not np.isfinite(atr_value) or atr_value <= 0.0:
                continue

            if is_bullish:
                take_profit_level = entry_close + (atr_value * take_profit_multiple)
                stop_loss_level = entry_close - (atr_value * stop_loss_multiple)
            else:
                take_profit_level = entry_close - (atr_value * take_profit_multiple)
                stop_loss_level = entry_close + (atr_value * stop_loss_multiple)

            for offset in range(1, max_horizon + 1):
                future_index = index + offset
                if future_index >= total_rows:
                    break

                bar_high = safe_high[future_index]
                bar_low = safe_low[future_index]
                if not np.isfinite(bar_high) or not np.isfinite(bar_low):
                    break

                if is_bullish:
                    hit_take_profit = bar_high >= take_profit_level
                    hit_stop_loss = bar_low <= stop_loss_level
                else:
                    hit_take_profit = bar_low <= take_profit_level
                    hit_stop_loss = bar_high >= stop_loss_level

                if hit_take_profit and hit_stop_loss:
                    resolution_codes[index] = CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE
                    break
                if hit_take_profit:
                    outcome_codes[index] = 1
                    resolution_codes[index] = 1
                    break
                if hit_stop_loss:
                    outcome_codes[index] = -1
                    resolution_codes[index] = -1
                    break

        return (
            pd.Series(outcome_codes, index=close_series.index, dtype=int),
            pd.Series(resolution_codes, index=close_series.index, dtype=int),
        )

    @staticmethod
    def _build_future_cost_edge_codes(
        entry_series: pd.Series,
        high_series: pd.Series,
        low_series: pd.Series,
        *,
        horizon: int,
        edge_hurdle_price: float,
    ):
        safe_entry = pd.to_numeric(entry_series, errors='coerce').to_numpy(dtype=float)
        safe_high = pd.to_numeric(high_series, errors='coerce').to_numpy(dtype=float)
        safe_low = pd.to_numeric(low_series, errors='coerce').to_numpy(dtype=float)
        total_rows = len(safe_entry)

        class_codes = np.zeros(total_rows, dtype=int)
        resolution_codes = np.zeros(total_rows, dtype=int)
        max_horizon = max(1, int(horizon))
        safe_hurdle = max(0.0, float(edge_hurdle_price))

        for index in range(total_rows):
            entry_ref = safe_entry[index]
            if not np.isfinite(entry_ref):
                continue

            up_target = entry_ref + safe_hurdle
            down_target = entry_ref - safe_hurdle

            for offset in range(1, max_horizon + 1):
                future_index = index + offset
                if future_index >= total_rows:
                    break

                bar_high = safe_high[future_index]
                bar_low = safe_low[future_index]
                if not np.isfinite(bar_high) or not np.isfinite(bar_low):
                    break

                hit_long = bar_high >= up_target
                hit_short = bar_low <= down_target

                if hit_long and hit_short:
                    resolution_codes[index] = MICRO_COST_EDGE_FIRST_TOUCH_AMBIGUOUS_CODE
                    break
                if hit_long:
                    class_codes[index] = 1
                    resolution_codes[index] = 1
                    break
                if hit_short:
                    class_codes[index] = -1
                    resolution_codes[index] = -1
                    break

        return (
            pd.Series(class_codes, index=entry_series.index, dtype=int),
            pd.Series(resolution_codes, index=entry_series.index, dtype=int),
        )

    @staticmethod
    def _build_atr_ratio(close_series: pd.Series, high_series: pd.Series, low_series: pd.Series, period: int = 14):
        prev_close = close_series.shift(1)
        true_range = pd.concat(
            [
                high_series - low_series,
                (high_series - prev_close).abs(),
                (low_series - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.ewm(alpha=1 / max(1, int(period)), adjust=False).mean()
        return atr / close_series.replace(0, np.nan)

    def apply(self):
        if self._applied:
            return self

        candles = self.symbol.candles
        for column in ('open', 'high', 'low', 'close'):
            candles[column] = pd.to_numeric(candles[column], errors='coerce')

        candles['volume'] = self._resolve_volume_series()

        volume_series = pd.to_numeric(candles['volume'], errors='coerce').fillna(0.0)

        candles['ff_volume'] = volume_series
        feature_profile = str(getattr(self.config, 'feature_profile', 'indicator_fusion') or 'indicator_fusion').strip().lower()
        if feature_profile == 'market_regime_fusion':
            self._apply_market_regime_fusion_profile(candles)
        elif feature_profile == 'micro_cost_edge_pattern_score_context':
            self._apply_micro_cost_edge_pattern_score_context_profile(candles)
        elif feature_profile == 'micro_cost_edge':
            self._apply_micro_cost_edge_profile(candles)
        elif feature_profile == 'ema_low_adx_setup_quality_pattern_score_cluster_context':
            self._apply_ema_low_adx_setup_quality_pattern_cluster_context_profile(candles)
        elif feature_profile == 'ema_low_adx_setup_quality_pattern_score_context':
            self._apply_ema_low_adx_setup_quality_pattern_score_context_profile(candles)
        elif feature_profile == 'ema_low_adx_setup_quality':
            self._apply_ema_low_adx_setup_quality_profile(candles)
        elif feature_profile == 'candle_reversal_pattern_score_context':
            self._apply_candle_reversal_pattern_score_context_profile(candles)
        elif feature_profile == 'candle_reversal_pattern_context':
            self._apply_candle_reversal_pattern_context_profile(candles)
        elif feature_profile == 'candle_reversal_context':
            self._apply_candle_reversal_context_profile(candles)
        elif feature_profile == 'candle_reversal':
            self._apply_candle_reversal_profile(candles)
        else:
            self._apply_indicator_fusion_profile(candles)

        normalization_columns = [
            str(column_name).strip()
            for column_name in (getattr(self.config, 'normalization_columns', None) or [])
            if str(column_name).strip() in self._requested_feature_columns
        ]
        for column_name in normalization_columns:
            candles[column_name] = self._zscore_series(candles[column_name])

        self._feature_columns = list(self._requested_feature_columns)
        self._applied = True
        return self

    def _build_base_target_frame(self):
        if not self._applied:
            self.apply()

        candles = self.symbol.candles.copy()
        feature_frame = candles[self._feature_columns].apply(pd.to_numeric, errors='coerce')
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        horizon = max(1, int(self.config.target_horizon))

        future_high, future_low = self._build_future_extrema(high_series, low_series, horizon)
        future_close = close_series.shift(-horizon)
        safe_close = close_series.replace(0, np.nan)

        frame = feature_frame.copy()
        frame['target_upside_ratio'] = ((future_high - close_series) / safe_close).clip(lower=0.0)
        frame['target_downside_ratio'] = ((close_series - future_low) / safe_close).clip(lower=0.0)
        frame['target_future_return_ratio'] = (future_close - close_series) / safe_close
        frame['target_future_range_ratio'] = ((future_high - future_low) / safe_close).clip(lower=0.0)
        frame['target_future_close_ratio'] = future_close / safe_close
        return frame, candles

    def build_dataset(self):
        frame, _candles = self._build_base_target_frame()
        target_mode = str(getattr(self.config, 'target_mode', 'excursion_signal') or 'excursion_signal').strip().lower()

        if target_mode == 'std_threshold_signal':
            std_window = max(2, int(getattr(self.config, 'target_std_window', 20) or 20))
            std_threshold = max(0.0, float(getattr(self.config, 'target_std_threshold', 1.0) or 0.0))
            upside_std = frame['target_upside_ratio'].rolling(window=std_window, min_periods=std_window).std(ddof=0)
            downside_std = frame['target_downside_ratio'].rolling(window=std_window, min_periods=std_window).std(ddof=0)

            long_mask = frame['target_upside_ratio'] >= (frame['target_downside_ratio'] + (std_threshold * downside_std))
            short_mask = frame['target_downside_ratio'] >= (frame['target_upside_ratio'] + (std_threshold * upside_std))

            frame['target_signal_score'] = 0.0
            frame.loc[long_mask, 'target_signal_score'] = 1.0
            frame.loc[short_mask, 'target_signal_score'] = -1.0
            frame['target_upside_std'] = upside_std
            frame['target_downside_std'] = downside_std
        else:
            total_excursion = (frame['target_upside_ratio'] + frame['target_downside_ratio']).replace(0, np.nan)
            frame['target_signal_score'] = (
                (frame['target_upside_ratio'] - frame['target_downside_ratio']) / total_excursion
            ).clip(lower=-1.0, upper=1.0)

        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        return frame

    def build_regime_classification_dataset(self):
        frame, candles = self._build_base_target_frame()
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')

        atr_ratio = self._build_atr_ratio(close_series, high_series, low_series, period=14)
        safe_atr_ratio = atr_ratio.replace(0, np.nan)
        future_range_ratio = frame['target_future_range_ratio']
        future_return_ratio = frame['target_future_return_ratio']
        future_upside_ratio = frame['target_upside_ratio']
        future_downside_ratio = frame['target_downside_ratio']
        safe_future_range = future_range_ratio.replace(0, np.nan)

        frame['target_atr_ratio'] = atr_ratio
        frame['target_future_volatility_multiple'] = future_range_ratio / safe_atr_ratio
        frame['target_directional_efficiency'] = future_return_ratio.abs() / safe_future_range
        frame['target_directional_move_multiple'] = future_return_ratio.abs() / safe_atr_ratio
        frame['target_directional_dominance'] = (future_upside_ratio - future_downside_ratio).abs() / safe_future_range

        compression_threshold = max(0.0, float(getattr(self.config, 'target_regime_compression_threshold', 0.9) or 0.0))
        volatility_threshold = max(0.0, float(getattr(self.config, 'target_regime_volatility_threshold', 2.2) or 0.0))
        trend_efficiency_threshold = max(0.0, float(getattr(self.config, 'target_regime_trend_efficiency_threshold', 0.55) or 0.0))
        directional_move_threshold = max(0.0, float(getattr(self.config, 'target_regime_directional_move_threshold', 0.35) or 0.0))
        dominance_threshold = max(0.0, float(getattr(self.config, 'target_regime_directional_dominance_threshold', 0.6) or 0.0))

        compression_mask = (frame['target_future_volatility_multiple'] <= compression_threshold).fillna(False)
        trend_mask = (
            (frame['target_directional_efficiency'] >= trend_efficiency_threshold)
            & (frame['target_directional_move_multiple'] >= directional_move_threshold)
            & (frame['target_directional_dominance'] >= dominance_threshold)
        ).fillna(False)
        trend_up_mask = trend_mask & (future_return_ratio > 0)
        trend_down_mask = trend_mask & (future_return_ratio < 0)
        volatile_mask = (
            (~compression_mask)
            & (~trend_mask)
            & (frame['target_future_volatility_multiple'] >= volatility_threshold)
        ).fillna(False)
        volatile_up_mask = volatile_mask & (
            (future_return_ratio > 0)
            | ((future_return_ratio == 0) & (future_upside_ratio >= future_downside_ratio))
        )
        volatile_down_mask = volatile_mask & (
            (future_return_ratio < 0)
            | ((future_return_ratio == 0) & (future_downside_ratio > future_upside_ratio))
        )

        regime_code = pd.Series(0.0, index=frame.index, dtype=float)
        regime_code.loc[compression_mask] = 1.0
        regime_code.loc[trend_up_mask] = 2.0
        regime_code.loc[trend_down_mask] = -2.0
        regime_code.loc[volatile_up_mask] = 3.0
        regime_code.loc[volatile_down_mask] = -3.0

        code_to_class_index = {code: index for index, code in enumerate(REGIME_CLASS_CODES)}
        frame['target_regime_code'] = regime_code
        frame['target_class_index'] = frame['target_regime_code'].map(code_to_class_index)

        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        frame['target_regime_code'] = frame['target_regime_code'].astype(int)
        frame['target_class_index'] = frame['target_class_index'].astype(int)
        return frame

    def build_candle_reversal_classification_dataset(self):
        self._last_target_filter_summary = None
        frame, candles = self._build_base_target_frame()
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        target_mode = str(getattr(self.config, 'target_mode', 'future_candle_reversal_classification') or 'future_candle_reversal_classification').strip().lower()

        pretrend_lookback = max(2, int(getattr(self.config, 'target_pretrend_lookback', 6) or 6))
        pretrend_threshold = max(0.0, float(getattr(self.config, 'target_pretrend_threshold', 1.2) or 0.0))
        reversal_threshold = max(0.0, float(getattr(self.config, 'target_reversal_threshold', 1.0) or 0.0))
        dominance_ratio = max(1.0, float(getattr(self.config, 'target_dominance_ratio', 1.35) or 1.0))

        atr_ratio = self._build_atr_ratio(close_series, high_series, low_series, period=14)
        safe_atr_ratio = atr_ratio.replace(0, np.nan)
        safe_close = close_series.replace(0, np.nan)
        atr_price = (safe_atr_ratio * safe_close).replace(0, np.nan)

        previous_close = close_series.shift(pretrend_lookback)
        previous_move_ratio = (close_series - previous_close) / safe_close
        frame['target_prev_move_atr'] = previous_move_ratio / safe_atr_ratio
        frame['target_future_upside_atr'] = frame['target_upside_ratio'] / safe_atr_ratio
        frame['target_future_downside_atr'] = frame['target_downside_ratio'] / safe_atr_ratio

        reversal_threshold_for_filter = reversal_threshold
        if target_mode == 'future_candle_reversal_tp_sl_classification':
            take_profit_atr = max(0.05, float(getattr(self.config, 'target_reversal_take_profit_atr', 0.75) or 0.05))
            stop_loss_atr = max(0.05, float(getattr(self.config, 'target_reversal_stop_loss_atr', 1.0) or 0.05))
            bullish_outcome_code, bullish_resolution_code = self._build_future_directional_tp_sl_codes(
                close_series,
                high_series,
                low_series,
                atr_price,
                horizon=max(1, int(getattr(self.config, 'target_horizon', 1) or 1)),
                take_profit_threshold=take_profit_atr,
                stop_loss_threshold=stop_loss_atr,
                direction='bullish',
            )
            bearish_outcome_code, bearish_resolution_code = self._build_future_directional_tp_sl_codes(
                close_series,
                high_series,
                low_series,
                atr_price,
                horizon=max(1, int(getattr(self.config, 'target_horizon', 1) or 1)),
                take_profit_threshold=take_profit_atr,
                stop_loss_threshold=stop_loss_atr,
                direction='bearish',
            )
            frame['target_bullish_tp_sl_code'] = bullish_outcome_code
            frame['target_bullish_resolution_code'] = bullish_resolution_code
            frame['target_bearish_tp_sl_code'] = bearish_outcome_code
            frame['target_bearish_resolution_code'] = bearish_resolution_code

            bullish_mask = (
                (frame['target_prev_move_atr'] <= -pretrend_threshold)
                & (frame['target_bullish_tp_sl_code'] == 1)
            ).fillna(False)
            bearish_mask = (
                (frame['target_prev_move_atr'] >= pretrend_threshold)
                & (frame['target_bearish_tp_sl_code'] == 1)
            ).fillna(False)
            reversal_threshold_for_filter = take_profit_atr
        else:
            bullish_mask = (
                (frame['target_prev_move_atr'] <= -pretrend_threshold)
                & (frame['target_future_upside_atr'] >= reversal_threshold)
                & (frame['target_future_upside_atr'] >= (dominance_ratio * frame['target_future_downside_atr']))
            ).fillna(False)
            bearish_mask = (
                (frame['target_prev_move_atr'] >= pretrend_threshold)
                & (frame['target_future_downside_atr'] >= reversal_threshold)
                & (frame['target_future_downside_atr'] >= (dominance_ratio * frame['target_future_upside_atr']))
            ).fillna(False)

        conflict_mask = (bullish_mask & bearish_mask).fillna(False)
        bullish_mask = bullish_mask & ~conflict_mask
        bearish_mask = bearish_mask & ~conflict_mask

        reversal_code = pd.Series(0.0, index=frame.index, dtype=float)
        reversal_code.loc[bullish_mask] = 1.0
        reversal_code.loc[bearish_mask] = -1.0

        code_to_class_index = {code: index for index, code in enumerate(REVERSAL_CLASS_CODES)}
        frame['target_reversal_code'] = reversal_code
        frame['target_class_index'] = frame['target_reversal_code'].map(code_to_class_index)

        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        frame, target_filter_summary = _filter_candle_reversal_target_frame(
            frame,
            pretrend_threshold=pretrend_threshold,
            reversal_threshold=reversal_threshold_for_filter,
            positive_pretrend_floor=float(getattr(self.config, 'target_clean_positive_pretrend_floor', 0.0) or 0.0),
            positive_excursion_floor=float(getattr(self.config, 'target_clean_positive_excursion_floor', 0.0) or 0.0),
            neutral_pretrend_ceiling=float(getattr(self.config, 'target_clean_neutral_pretrend_ceiling', 0.0) or 0.0),
            neutral_excursion_ceiling=float(getattr(self.config, 'target_clean_neutral_excursion_ceiling', 0.0) or 0.0),
        )
        self._last_target_filter_summary = target_filter_summary
        frame['target_reversal_code'] = frame['target_reversal_code'].astype(int)
        frame['target_class_index'] = frame['target_class_index'].astype(int)
        return frame

    def build_candle_reversal_setup_quality_classification_dataset(self):
        self._last_candle_reversal_setup_candidate_summary = None
        frame, candles = self._build_base_target_frame()
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')

        pretrend_lookback = max(2, int(getattr(self.config, 'target_pretrend_lookback', 6) or 6))
        pretrend_threshold = max(0.0, float(getattr(self.config, 'target_pretrend_threshold', 1.2) or 0.0))
        take_profit_atr = max(0.05, float(getattr(self.config, 'target_reversal_take_profit_atr', 0.75) or 0.05))
        stop_loss_atr = max(0.05, float(getattr(self.config, 'target_reversal_stop_loss_atr', 1.0) or 0.05))
        target_horizon = max(1, int(getattr(self.config, 'target_horizon', 1) or 1))

        atr_ratio = self._build_atr_ratio(close_series, high_series, low_series, period=14)
        safe_atr_ratio = atr_ratio.replace(0, np.nan)
        safe_close = close_series.replace(0, np.nan)
        atr_price = (safe_atr_ratio * safe_close).replace(0, np.nan)

        previous_close = close_series.shift(pretrend_lookback)
        previous_move_ratio = (close_series - previous_close) / safe_close
        frame['source_bar_index'] = candles.index.to_numpy(dtype=int)
        frame['time'] = pd.to_numeric(candles['time'], errors='coerce')
        frame['close_price'] = close_series
        frame['atr_price'] = atr_price
        frame['target_prev_move_atr'] = previous_move_ratio / safe_atr_ratio
        frame['target_future_upside_atr'] = frame['target_upside_ratio'] / safe_atr_ratio
        frame['target_future_downside_atr'] = frame['target_downside_ratio'] / safe_atr_ratio

        bullish_outcome_code, bullish_resolution_code = self._build_future_directional_tp_sl_codes(
            close_series,
            high_series,
            low_series,
            atr_price,
            horizon=target_horizon,
            take_profit_threshold=take_profit_atr,
            stop_loss_threshold=stop_loss_atr,
            direction='bullish',
        )
        bearish_outcome_code, bearish_resolution_code = self._build_future_directional_tp_sl_codes(
            close_series,
            high_series,
            low_series,
            atr_price,
            horizon=target_horizon,
            take_profit_threshold=take_profit_atr,
            stop_loss_threshold=stop_loss_atr,
            direction='bearish',
        )
        frame['target_bullish_tp_sl_code'] = bullish_outcome_code
        frame['target_bullish_resolution_code'] = bullish_resolution_code
        frame['target_bearish_tp_sl_code'] = bearish_outcome_code
        frame['target_bearish_resolution_code'] = bearish_resolution_code

        bullish_candidate_mask = (frame['target_prev_move_atr'] <= -pretrend_threshold).fillna(False)
        bearish_candidate_mask = (frame['target_prev_move_atr'] >= pretrend_threshold).fillna(False)
        candidate_mask = (bullish_candidate_mask | bearish_candidate_mask).fillna(False)
        good_mask = (
            (bullish_candidate_mask & (frame['target_bullish_tp_sl_code'] == 1))
            | (bearish_candidate_mask & (frame['target_bearish_tp_sl_code'] == 1))
        ).fillna(False)
        bad_mask = (
            (bullish_candidate_mask & (frame['target_bullish_tp_sl_code'] == -1))
            | (bearish_candidate_mask & (frame['target_bearish_tp_sl_code'] == -1))
        ).fillna(False)
        ambiguous_mask = (
            (bullish_candidate_mask & (frame['target_bullish_resolution_code'] == CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE))
            | (bearish_candidate_mask & (frame['target_bearish_resolution_code'] == CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE))
        ).fillna(False)
        timeout_mask = (
            candidate_mask
            & ~good_mask
            & ~bad_mask
            & ~ambiguous_mask
        ).fillna(False)

        setup_side_code = pd.Series(0.0, index=frame.index, dtype=float)
        setup_side_code.loc[bullish_candidate_mask] = 1.0
        setup_side_code.loc[bearish_candidate_mask] = -1.0
        setup_quality_code = pd.Series(0.0, index=frame.index, dtype=float)
        setup_quality_code.loc[good_mask] = 1.0
        setup_resolution_code = pd.Series(0.0, index=frame.index, dtype=float)
        setup_resolution_code.loc[good_mask] = 1.0
        setup_resolution_code.loc[bad_mask] = -1.0
        setup_resolution_code.loc[ambiguous_mask] = float(CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE)

        code_to_class_index = {
            code: index
            for index, code in enumerate(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES)
        }
        frame['target_setup_candidate'] = candidate_mask.astype(int)
        frame['target_setup_side_code'] = setup_side_code
        frame['target_setup_quality_code'] = setup_quality_code
        frame['target_setup_resolution_code'] = setup_resolution_code
        frame['target_class_index'] = frame['target_setup_quality_code'].map(code_to_class_index)

        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        frame['target_setup_candidate'] = frame['target_setup_candidate'].astype(int)
        frame['target_setup_side_code'] = frame['target_setup_side_code'].astype(int)
        frame['target_setup_quality_code'] = frame['target_setup_quality_code'].astype(int)
        frame['target_setup_resolution_code'] = frame['target_setup_resolution_code'].astype(int)
        frame['target_class_index'] = frame['target_class_index'].astype(int)

        candidate_rows = frame[frame['target_setup_candidate'] == 1]
        self._last_candle_reversal_setup_candidate_summary = {
            'rows_after_cleaning': int(len(frame)),
            'candidate_rows': int(len(candidate_rows)),
            'bullish_candidate_rows': int((candidate_rows['target_setup_side_code'] == 1).sum()) if not candidate_rows.empty else 0,
            'bearish_candidate_rows': int((candidate_rows['target_setup_side_code'] == -1).sum()) if not candidate_rows.empty else 0,
            'good_rows': int((candidate_rows['target_setup_quality_code'] == 1).sum()) if not candidate_rows.empty else 0,
            'rest_rows': int((candidate_rows['target_setup_quality_code'] == 0).sum()) if not candidate_rows.empty else 0,
            'bad_rows': int((candidate_rows['target_setup_resolution_code'] == -1).sum()) if not candidate_rows.empty else 0,
            'timeout_rows': int((candidate_rows['target_setup_resolution_code'] == 0).sum()) if not candidate_rows.empty else 0,
            'ambiguous_rows': int((candidate_rows['target_setup_resolution_code'] == CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE).sum()) if not candidate_rows.empty else 0,
            'pretrend_lookback': int(pretrend_lookback),
            'pretrend_threshold': float(pretrend_threshold),
            'take_profit_atr': float(take_profit_atr),
            'stop_loss_atr': float(stop_loss_atr),
            'target_horizon': int(target_horizon),
        }
        return frame

    def build_candle_reversal_setup_quality_v1_sequence_dataset(self):
        frame = self.build_candle_reversal_setup_quality_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        candidate_mask = frame['target_setup_candidate'].to_numpy(dtype=int) == 1
        feature_values = frame[feature_columns].to_numpy(dtype=float)
        target_classes = frame['target_class_index'].to_numpy(dtype=int)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Candle-reversal setup-quality dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        sequence_end_indexes = [
            int(end_index)
            for end_index in range(observation_window - 1, total_rows)
            if bool(candidate_mask[end_index])
        ]
        if not sequence_end_indexes:
            raise ValueError('No candle-reversal setup-quality candidate rows were available after feature generation.')

        X_sequences = []
        y_classes = []
        event_rows = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(target_classes[end_index])
            row = frame.iloc[end_index]
            event_rows.append({
                'event_row_index': int(len(event_rows)),
                'time': int(row['time']),
                'source_bar_index': int(row['source_bar_index']),
                'entry_bar_index': int(row['source_bar_index']) + 1,
                'close_price': float(row['close_price']),
                'atr_price': float(row['atr_price']),
                'setup_side': 'long' if int(row['target_setup_side_code']) == 1 else 'short',
                'setup_side_code': int(row['target_setup_side_code']),
                'actual_class_index': int(target_classes[end_index]),
                'actual_setup_quality_code': int(row['target_setup_quality_code']),
                'target_setup_resolution_code': int(row['target_setup_resolution_code']),
                'target_prev_move_atr': float(row['target_prev_move_atr']),
                'target_future_upside_atr': float(row['target_future_upside_atr']),
                'target_future_downside_atr': float(row['target_future_downside_atr']),
            })

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES),
            'class_labels': dict(SETUP_QUALITY_GOOD_VS_REST_CLASS_LABELS),
            'candidate_summary': self._last_candle_reversal_setup_candidate_summary or {},
            'event_rows': event_rows,
        }

    def build_ema_low_adx_setup_quality_classification_dataset(self):
        self._last_setup_candidate_summary = None
        if not self._applied:
            self.apply()

        candles = self.symbol.candles.copy()
        feature_frame = candles[self._feature_columns].apply(pd.to_numeric, errors='coerce')
        close_series = pd.to_numeric(candles['close'], errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        horizon = max(1, int(getattr(self.config, 'target_horizon', 8) or 8))

        indicator_columns = self._resolve_indicator_columns([
            ('EMA', ['close', 9], ''),
            ('ATR', [14], ''),
            ('ADX', [14], ''),
            ('RSI', ['close', 14], ''),
            ('BollingerBands', ['close', 20, 2], 'middle'),
            ('BollingerBands', ['close', 20, 2], 'lower'),
        ])
        (
            ema_9_column,
            atr_14_column,
            adx_14_column,
            rsi_14_column,
            boll_middle_column,
            boll_lower_column,
        ) = indicator_columns

        ema_9_series = pd.to_numeric(candles[ema_9_column], errors='coerce')
        atr_14_series = pd.to_numeric(candles[atr_14_column], errors='coerce').replace(0, np.nan)
        adx_14_series = pd.to_numeric(candles[adx_14_column], errors='coerce')
        rsi_14_series = pd.to_numeric(candles[rsi_14_column], errors='coerce')
        boll_middle_series = pd.to_numeric(candles[boll_middle_column], errors='coerce')
        boll_lower_series = pd.to_numeric(candles[boll_lower_column], errors='coerce')

        prev_close = close_series.shift(1)
        prev_rsi = rsi_14_series.shift(1)
        prev_boll_lower = boll_lower_series.shift(1)
        prev_atr = atr_14_series.shift(1)
        reclaim_base = (boll_middle_series - ema_9_series).clip(lower=0.0)
        di_spread_series = (
            pd.to_numeric(candles['slq_di_spread_14'], errors='coerce')
            if 'slq_di_spread_14' in candles.columns
            else pd.Series(np.nan, index=feature_frame.index, dtype=float)
        )

        future_high, future_low = self._build_future_extrema(high_series, low_series, horizon)

        setup_adx_ceiling = float(getattr(self.config, 'setup_adx_ceiling', 28.0) or 28.0)
        setup_prev_rsi_ceiling = float(getattr(self.config, 'setup_prev_rsi_ceiling', 38.0) or 38.0)
        setup_current_rsi_floor = float(getattr(self.config, 'setup_current_rsi_floor', 38.0) or 38.0)
        setup_current_rsi_ceiling = float(getattr(self.config, 'setup_current_rsi_ceiling', 50.0) or 50.0)
        setup_touch_slack_atr = float(getattr(self.config, 'setup_touch_slack_atr', 0.06) or 0.0)
        setup_prev_band_slack_atr = float(getattr(self.config, 'setup_prev_band_slack_atr', 0.08) or 0.0)
        setup_bounce_fraction = float(getattr(self.config, 'setup_bounce_fraction', 0.02) or 0.0)
        setup_di_spread_floor = max(0.0, float(getattr(self.config, 'setup_di_spread_floor', 0.0) or 0.0))
        setup_candidate_min_gap_bars = max(0, int(getattr(self.config, 'setup_candidate_min_gap_bars', 0) or 0))
        good_excursion_threshold = float(getattr(self.config, 'target_quality_good_excursion_threshold', 0.82) or 0.82)
        bad_excursion_threshold = float(getattr(self.config, 'target_quality_bad_excursion_threshold', 0.52) or 0.52)
        good_dominance_ratio = max(1.0, float(getattr(self.config, 'target_quality_good_dominance_ratio', 1.1) or 1.0))
        bad_dominance_ratio = max(1.0, float(getattr(self.config, 'target_quality_bad_dominance_ratio', 1.1) or 1.0))
        take_profit_atr = max(0.05, float(getattr(self.config, 'target_reversal_take_profit_atr', 1.0) or 0.05))
        stop_loss_atr = max(0.05, float(getattr(self.config, 'target_reversal_stop_loss_atr', 1.0) or 0.05))

        base_candidate_mask = (
            (low_series <= (boll_lower_series + (atr_14_series * setup_touch_slack_atr)))
            & (prev_close <= (prev_boll_lower + (prev_atr * setup_prev_band_slack_atr)))
            & (close_series >= (ema_9_series + (reclaim_base * setup_bounce_fraction)))
            & (close_series > open_series)
            & (prev_rsi <= setup_prev_rsi_ceiling)
            & (rsi_14_series >= setup_current_rsi_floor)
            & (rsi_14_series <= setup_current_rsi_ceiling)
            & (adx_14_series <= setup_adx_ceiling)
        ).fillna(False)
        di_spread_filtered_candidate_mask = base_candidate_mask.copy()
        if setup_di_spread_floor > 0.0:
            di_spread_filtered_candidate_mask &= (di_spread_series >= setup_di_spread_floor).fillna(False)
        candidate_mask = self._dedupe_candidate_mask_by_priority(
            di_spread_filtered_candidate_mask,
            di_spread_series,
            min_gap_bars=setup_candidate_min_gap_bars,
        )

        future_upside_atr = ((future_high - close_series) / atr_14_series).clip(lower=0.0)
        future_downside_atr = ((close_series - future_low) / atr_14_series).clip(lower=0.0)
        good_mask = (
            candidate_mask
            & (future_upside_atr >= good_excursion_threshold)
            & (future_upside_atr >= (good_dominance_ratio * future_downside_atr))
        ).fillna(False)
        bad_mask = (
            candidate_mask
            & (future_downside_atr >= bad_excursion_threshold)
            & (future_downside_atr >= (bad_dominance_ratio * future_upside_atr))
        ).fillna(False)

        quality_code = pd.Series(0.0, index=feature_frame.index, dtype=float)
        quality_code.loc[good_mask] = 1.0
        quality_code.loc[bad_mask] = -1.0
        first_touch_quality_code, first_touch_resolution_code = self._build_future_first_touch_codes(
            close_series,
            high_series,
            low_series,
            atr_14_series,
            horizon=horizon,
            good_excursion_threshold=good_excursion_threshold,
            bad_excursion_threshold=bad_excursion_threshold,
        )
        bullish_tp_sl_code, bullish_tp_sl_resolution_code = self._build_future_directional_tp_sl_codes(
            close_series,
            high_series,
            low_series,
            atr_14_series,
            horizon=horizon,
            take_profit_threshold=take_profit_atr,
            stop_loss_threshold=stop_loss_atr,
            direction='bullish',
        )
        code_to_class_index = {code: index for index, code in enumerate(SETUP_QUALITY_CLASS_CODES)}

        frame = feature_frame.copy()
        frame['source_bar_index'] = candles.index.to_numpy(dtype=int)
        frame['time'] = pd.to_numeric(candles['time'], errors='coerce')
        frame['close_price'] = close_series
        frame['atr_price'] = atr_14_series
        frame['target_future_upside_atr'] = future_upside_atr
        frame['target_future_downside_atr'] = future_downside_atr
        frame['target_setup_candidate'] = candidate_mask.astype(int)
        frame['target_setup_quality_code'] = quality_code
        frame['target_first_touch_setup_quality_code'] = first_touch_quality_code
        frame['target_first_touch_resolution_code'] = first_touch_resolution_code
        frame['target_bullish_tp_sl_code'] = bullish_tp_sl_code
        frame['target_bullish_tp_sl_resolution_code'] = bullish_tp_sl_resolution_code
        frame['target_class_index'] = frame['target_setup_quality_code'].map(code_to_class_index)
        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        frame['target_setup_candidate'] = frame['target_setup_candidate'].astype(int)
        frame['target_setup_quality_code'] = frame['target_setup_quality_code'].astype(int)
        frame['target_first_touch_setup_quality_code'] = frame['target_first_touch_setup_quality_code'].astype(int)
        frame['target_first_touch_resolution_code'] = frame['target_first_touch_resolution_code'].astype(int)
        frame['target_bullish_tp_sl_code'] = frame['target_bullish_tp_sl_code'].astype(int)
        frame['target_bullish_tp_sl_resolution_code'] = frame['target_bullish_tp_sl_resolution_code'].astype(int)
        frame['target_class_index'] = frame['target_class_index'].astype(int)

        candidate_rows = frame[frame['target_setup_candidate'] == 1]
        self._last_setup_candidate_summary = {
            'rows_after_cleaning': int(len(frame)),
            'candidate_rows': int(len(candidate_rows)),
            'candidate_rows_before_filters': int(base_candidate_mask.sum()),
            'candidate_rows_after_di_spread_floor': int(di_spread_filtered_candidate_mask.sum()),
            'candidate_rows_after_gap_dedup': int(candidate_mask.sum()),
            'good_rows': int((candidate_rows['target_setup_quality_code'] == 1).sum()) if not candidate_rows.empty else 0,
            'weak_rows': int((candidate_rows['target_setup_quality_code'] == 0).sum()) if not candidate_rows.empty else 0,
            'bad_rows': int((candidate_rows['target_setup_quality_code'] == -1).sum()) if not candidate_rows.empty else 0,
            'first_touch_good_rows': int((candidate_rows['target_first_touch_setup_quality_code'] == 1).sum()) if not candidate_rows.empty else 0,
            'first_touch_bad_rows': int((candidate_rows['target_first_touch_setup_quality_code'] == -1).sum()) if not candidate_rows.empty else 0,
            'first_touch_timeout_rows': int((candidate_rows['target_first_touch_resolution_code'] == 0).sum()) if not candidate_rows.empty else 0,
            'first_touch_ambiguous_rows': int((candidate_rows['target_first_touch_resolution_code'] == SETUP_QUALITY_FIRST_TOUCH_AMBIGUOUS_CODE).sum()) if not candidate_rows.empty else 0,
            'setup_adx_ceiling': setup_adx_ceiling,
            'setup_prev_rsi_ceiling': setup_prev_rsi_ceiling,
            'setup_current_rsi_floor': setup_current_rsi_floor,
            'setup_current_rsi_ceiling': setup_current_rsi_ceiling,
            'setup_touch_slack_atr': setup_touch_slack_atr,
            'setup_prev_band_slack_atr': setup_prev_band_slack_atr,
            'setup_bounce_fraction': setup_bounce_fraction,
            'setup_di_spread_floor': setup_di_spread_floor,
            'setup_candidate_min_gap_bars': setup_candidate_min_gap_bars,
            'good_excursion_threshold': good_excursion_threshold,
            'bad_excursion_threshold': bad_excursion_threshold,
            'good_dominance_ratio': good_dominance_ratio,
            'bad_dominance_ratio': bad_dominance_ratio,
            'take_profit_atr': take_profit_atr,
            'stop_loss_atr': stop_loss_atr,
        }
        return frame

    def build_ema_low_adx_setup_quality_sequence_dataset(self):
        frame = self.build_ema_low_adx_setup_quality_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        candidate_mask = frame['target_setup_candidate'].to_numpy(dtype=int) == 1
        feature_values = frame[feature_columns].to_numpy(dtype=float)
        target_classes = frame['target_class_index'].to_numpy(dtype=int)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Setup-quality dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        sequence_end_indexes = [
            int(end_index)
            for end_index in range(observation_window - 1, total_rows)
            if bool(candidate_mask[end_index])
        ]
        if not sequence_end_indexes:
            raise ValueError('No setup-quality candidate rows were available after feature generation.')

        X_sequences = []
        y_classes = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(target_classes[end_index])

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(SETUP_QUALITY_CLASS_CODES),
            'class_labels': dict(SETUP_QUALITY_CLASS_LABELS),
            'candidate_summary': self._last_setup_candidate_summary,
        }

    def build_ema_low_adx_setup_quality_v3_sequence_dataset(self):
        frame = self.build_ema_low_adx_setup_quality_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        feature_values = frame[feature_columns].to_numpy(dtype=float)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Setup-quality v3 dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        quality_codes = frame['target_first_touch_setup_quality_code'].to_numpy(dtype=int)
        resolution_codes = frame['target_first_touch_resolution_code'].to_numpy(dtype=int)
        candidate_mask = frame['target_setup_candidate'].to_numpy(dtype=int) == 1
        eligible_mask = candidate_mask & np.isin(quality_codes, [-1, 1])

        sequence_end_indexes = [
            int(end_index)
            for end_index in range(observation_window - 1, total_rows)
            if bool(eligible_mask[end_index])
        ]
        if not sequence_end_indexes:
            raise ValueError('No clean setup-quality v3 rows were available after first-touch filtering.')

        code_to_class_index = {code: index for index, code in enumerate(SETUP_QUALITY_BINARY_CLASS_CODES)}
        X_sequences = []
        y_classes = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(code_to_class_index[int(quality_codes[end_index])])

        candidate_summary = dict(self._last_setup_candidate_summary or {})
        candidate_summary.update({
            'binary_first_touch_candidate_rows': int(candidate_mask.sum()),
            'binary_first_touch_good_rows': int(np.sum(candidate_mask & (quality_codes == 1))),
            'binary_first_touch_bad_rows': int(np.sum(candidate_mask & (quality_codes == -1))),
            'binary_first_touch_timeout_rows': int(np.sum(candidate_mask & (resolution_codes == 0))),
            'binary_first_touch_ambiguous_rows': int(np.sum(candidate_mask & (resolution_codes == SETUP_QUALITY_FIRST_TOUCH_AMBIGUOUS_CODE))),
            'binary_first_touch_kept_rows': int(eligible_mask.sum()),
            'binary_first_touch_dropped_rows': int(np.sum(candidate_mask & ~eligible_mask)),
        })

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(SETUP_QUALITY_BINARY_CLASS_CODES),
            'class_labels': dict(SETUP_QUALITY_BINARY_CLASS_LABELS),
            'candidate_summary': candidate_summary,
        }

    def build_ema_low_adx_setup_quality_v2_sequence_dataset(self):
        frame = self.build_ema_low_adx_setup_quality_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        feature_values = frame[feature_columns].to_numpy(dtype=float)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Setup-quality v2 dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        good_counter_ceiling = max(
            0.0,
            float(getattr(self.config, 'target_quality_good_counter_excursion_ceiling', 0.45) or 0.0),
        )
        bad_counter_ceiling = max(
            0.0,
            float(getattr(self.config, 'target_quality_bad_counter_excursion_ceiling', 0.45) or 0.0),
        )

        quality_codes = frame['target_setup_quality_code'].to_numpy(dtype=int)
        candidate_mask = frame['target_setup_candidate'].to_numpy(dtype=int) == 1
        upside = frame['target_future_upside_atr'].to_numpy(dtype=float)
        downside = frame['target_future_downside_atr'].to_numpy(dtype=float)

        clean_good_mask = candidate_mask & (quality_codes == 1) & (downside <= good_counter_ceiling)
        clean_bad_mask = candidate_mask & (quality_codes == -1) & (upside <= bad_counter_ceiling)
        eligible_mask = clean_good_mask | clean_bad_mask

        sequence_end_indexes = [
            int(end_index)
            for end_index in range(observation_window - 1, total_rows)
            if bool(eligible_mask[end_index])
        ]
        if not sequence_end_indexes:
            raise ValueError('No clean setup-quality v2 rows were available after candidate filtering.')

        code_to_class_index = {code: index for index, code in enumerate(SETUP_QUALITY_BINARY_CLASS_CODES)}
        X_sequences = []
        y_classes = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(code_to_class_index[int(quality_codes[end_index])])

        candidate_summary = dict(self._last_setup_candidate_summary or {})
        candidate_summary.update({
            'binary_candidate_rows': int(candidate_mask.sum()),
            'binary_clean_good_rows': int(clean_good_mask.sum()),
            'binary_clean_bad_rows': int(clean_bad_mask.sum()),
            'binary_kept_rows': int(eligible_mask.sum()),
            'binary_dropped_ambiguous_rows': int(candidate_mask.sum() - eligible_mask.sum()),
            'target_quality_good_counter_excursion_ceiling': float(good_counter_ceiling),
            'target_quality_bad_counter_excursion_ceiling': float(bad_counter_ceiling),
        })

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(SETUP_QUALITY_BINARY_CLASS_CODES),
            'class_labels': dict(SETUP_QUALITY_BINARY_CLASS_LABELS),
            'candidate_summary': candidate_summary,
        }

    def build_ema_low_adx_setup_quality_v4_sequence_dataset(self):
        frame = self.build_ema_low_adx_setup_quality_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        feature_values = frame[feature_columns].to_numpy(dtype=float)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Setup-quality v4 dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        quality_codes = frame['target_first_touch_setup_quality_code'].to_numpy(dtype=int)
        resolution_codes = frame['target_first_touch_resolution_code'].to_numpy(dtype=int)
        candidate_mask = frame['target_setup_candidate'].to_numpy(dtype=int) == 1
        positive_mask = candidate_mask & (quality_codes == 1)
        negative_mask = candidate_mask & ~positive_mask
        eligible_mask = candidate_mask

        sequence_end_indexes = [
            int(end_index)
            for end_index in range(observation_window - 1, total_rows)
            if bool(eligible_mask[end_index])
        ]
        if not sequence_end_indexes:
            raise ValueError('No setup-quality v4 candidate rows were available after candidate filtering.')

        code_to_class_index = {code: index for index, code in enumerate(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES)}
        X_sequences = []
        y_classes = []
        event_rows = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            target_code = 1 if positive_mask[end_index] else 0
            y_classes.append(code_to_class_index[int(target_code)])
            row = frame.iloc[end_index]
            event_rows.append({
                'event_row_index': int(len(event_rows)),
                'time': int(row['time']),
                'source_bar_index': int(row['source_bar_index']),
                'entry_bar_index': int(row['source_bar_index']) + 1,
                'close_price': float(row['close_price']),
                'atr_price': float(row['atr_price']),
                'setup_side': 'long',
                'setup_side_code': 1,
                'actual_class_index': int(code_to_class_index[int(target_code)]),
                'actual_setup_quality_code': int(target_code),
                'target_first_touch_setup_quality_code': int(row['target_first_touch_setup_quality_code']),
                'target_first_touch_resolution_code': int(row['target_first_touch_resolution_code']),
                'target_future_upside_atr': float(row['target_future_upside_atr']),
                'target_future_downside_atr': float(row['target_future_downside_atr']),
            })

        candidate_summary = dict(self._last_setup_candidate_summary or {})
        candidate_summary.update({
            'good_vs_rest_candidate_rows': int(candidate_mask.sum()),
            'good_vs_rest_positive_rows': int(positive_mask.sum()),
            'good_vs_rest_negative_rows': int(negative_mask.sum()),
            'good_vs_rest_bad_rows': int(np.sum(candidate_mask & (quality_codes == -1))),
            'good_vs_rest_timeout_rows': int(np.sum(candidate_mask & (resolution_codes == 0))),
            'good_vs_rest_ambiguous_rows': int(np.sum(candidate_mask & (resolution_codes == SETUP_QUALITY_FIRST_TOUCH_AMBIGUOUS_CODE))),
        })

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES),
            'class_labels': dict(SETUP_QUALITY_GOOD_VS_REST_CLASS_LABELS),
            'candidate_summary': candidate_summary,
            'event_rows': event_rows,
        }

    def build_ema_low_adx_setup_quality_v7_sequence_dataset(self):
        frame = self.build_ema_low_adx_setup_quality_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        feature_values = frame[feature_columns].to_numpy(dtype=float)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Setup-quality v7 dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        tp_sl_codes = frame['target_bullish_tp_sl_code'].to_numpy(dtype=int)
        resolution_codes = frame['target_bullish_tp_sl_resolution_code'].to_numpy(dtype=int)
        candidate_mask = frame['target_setup_candidate'].to_numpy(dtype=int) == 1
        positive_mask = candidate_mask & (tp_sl_codes == 1)
        negative_mask = candidate_mask & ~positive_mask

        sequence_end_indexes = [
            int(end_index)
            for end_index in range(observation_window - 1, total_rows)
            if bool(candidate_mask[end_index])
        ]
        if not sequence_end_indexes:
            raise ValueError('No setup-quality v7 candidate rows were available after candidate filtering.')

        code_to_class_index = {code: index for index, code in enumerate(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES)}
        X_sequences = []
        y_classes = []
        event_rows = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            target_code = 1 if positive_mask[end_index] else 0
            y_classes.append(code_to_class_index[int(target_code)])
            row = frame.iloc[end_index]
            event_rows.append({
                'event_row_index': int(len(event_rows)),
                'time': int(row['time']),
                'source_bar_index': int(row['source_bar_index']),
                'entry_bar_index': int(row['source_bar_index']) + 1,
                'close_price': float(row['close_price']),
                'atr_price': float(row['atr_price']),
                'setup_side': 'long',
                'setup_side_code': 1,
                'actual_class_index': int(code_to_class_index[int(target_code)]),
                'actual_setup_quality_code': int(target_code),
                'target_bullish_tp_sl_code': int(row['target_bullish_tp_sl_code']),
                'target_bullish_tp_sl_resolution_code': int(row['target_bullish_tp_sl_resolution_code']),
                'target_first_touch_setup_quality_code': int(row['target_first_touch_setup_quality_code']),
                'target_first_touch_resolution_code': int(row['target_first_touch_resolution_code']),
                'target_future_upside_atr': float(row['target_future_upside_atr']),
                'target_future_downside_atr': float(row['target_future_downside_atr']),
            })

        candidate_summary = dict(self._last_setup_candidate_summary or {})
        candidate_summary.update({
            'tp_sl_good_vs_rest_candidate_rows': int(candidate_mask.sum()),
            'tp_sl_good_vs_rest_positive_rows': int(positive_mask.sum()),
            'tp_sl_good_vs_rest_negative_rows': int(negative_mask.sum()),
            'tp_sl_good_vs_rest_bad_rows': int(np.sum(candidate_mask & (tp_sl_codes == -1))),
            'tp_sl_good_vs_rest_timeout_rows': int(np.sum(candidate_mask & (resolution_codes == 0))),
            'tp_sl_good_vs_rest_ambiguous_rows': int(np.sum(candidate_mask & (resolution_codes == CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE))),
            'good_vs_rest_candidate_rows': int(candidate_mask.sum()),
            'good_vs_rest_positive_rows': int(positive_mask.sum()),
            'good_vs_rest_negative_rows': int(negative_mask.sum()),
            'good_vs_rest_bad_rows': int(np.sum(candidate_mask & (tp_sl_codes == -1))),
            'good_vs_rest_timeout_rows': int(np.sum(candidate_mask & (resolution_codes == 0))),
            'good_vs_rest_ambiguous_rows': int(np.sum(candidate_mask & (resolution_codes == CANDLE_REVERSAL_TP_SL_AMBIGUOUS_CODE))),
        })

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(SETUP_QUALITY_GOOD_VS_REST_CLASS_CODES),
            'class_labels': dict(SETUP_QUALITY_GOOD_VS_REST_CLASS_LABELS),
            'candidate_summary': candidate_summary,
            'event_rows': event_rows,
        }

    def build_micro_cost_edge_sequence_dataset(self):
        if not self._applied:
            self.apply()

        candles = self.symbol.candles.copy()
        feature_frame = candles[self._feature_columns].apply(pd.to_numeric, errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        horizon = max(1, int(getattr(self.config, 'target_horizon', 5) or 5))
        pip_size = max(1e-8, float(getattr(self.config, 'pip_size', 0.0001) or 0.0001))
        round_trip_cost_pips = max(0.0, float(getattr(self.config, 'round_trip_cost_pips', 1.6) or 0.0))
        edge_multiple = max(1.0, float(getattr(self.config, 'target_cost_edge_multiple', 1.75) or 1.0))
        edge_hurdle_pips = round_trip_cost_pips * edge_multiple
        edge_hurdle_price = edge_hurdle_pips * pip_size

        entry_ref_series = open_series.shift(-1)
        future_high, future_low = self._build_future_extrema(high_series, low_series, horizon)
        cost_edge_codes, resolution_codes = self._build_future_cost_edge_codes(
            entry_ref_series,
            high_series,
            low_series,
            horizon=horizon,
            edge_hurdle_price=edge_hurdle_price,
        )
        code_to_class_index = {code: index for index, code in enumerate(MICRO_COST_EDGE_CLASS_CODES)}

        frame = feature_frame.copy()
        frame['target_entry_ref'] = entry_ref_series
        frame['target_future_long_edge_pips'] = ((future_high - entry_ref_series) / pip_size).clip(lower=0.0)
        frame['target_future_short_edge_pips'] = ((entry_ref_series - future_low) / pip_size).clip(lower=0.0)
        frame['target_edge_hurdle_pips'] = edge_hurdle_pips
        frame['target_cost_edge_code'] = cost_edge_codes
        frame['target_cost_edge_resolution_code'] = resolution_codes
        frame['target_class_index'] = frame['target_cost_edge_code'].map(code_to_class_index)
        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        frame['target_cost_edge_code'] = frame['target_cost_edge_code'].astype(int)
        frame['target_cost_edge_resolution_code'] = frame['target_cost_edge_resolution_code'].astype(int)
        frame['target_class_index'] = frame['target_class_index'].astype(int)

        feature_columns = list(self.feature_columns)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Micro cost-edge dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        feature_values = frame[feature_columns].to_numpy(dtype=float)
        target_classes = frame['target_class_index'].to_numpy(dtype=int)
        sequence_end_indexes = list(range(observation_window - 1, total_rows))
        if not sequence_end_indexes:
            raise ValueError('No micro cost-edge rows were available after feature generation.')

        X_sequences = []
        y_classes = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(target_classes[end_index])

        candidate_summary = {
            'rows_after_cleaning': int(total_rows),
            'sequence_rows': int(len(X_sequences)),
            'long_edge_rows': int((frame['target_cost_edge_code'] == 1).sum()),
            'short_edge_rows': int((frame['target_cost_edge_code'] == -1).sum()),
            'no_edge_rows': int((frame['target_cost_edge_code'] == 0).sum()),
            'ambiguous_rows': int((frame['target_cost_edge_resolution_code'] == MICRO_COST_EDGE_FIRST_TOUCH_AMBIGUOUS_CODE).sum()),
            'timeout_rows': int((frame['target_cost_edge_resolution_code'] == 0).sum()),
            'pip_size': float(pip_size),
            'round_trip_cost_pips': float(round_trip_cost_pips),
            'target_cost_edge_multiple': float(edge_multiple),
            'edge_hurdle_pips': float(edge_hurdle_pips),
            'target_horizon': int(horizon),
        }

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(MICRO_COST_EDGE_CLASS_CODES),
            'class_labels': dict(MICRO_COST_EDGE_CLASS_LABELS),
            'candidate_summary': candidate_summary,
        }

    @staticmethod
    def _build_micro_cost_edge_canonical_views(feature_frame: pd.DataFrame):
        safe_frame = feature_frame.apply(pd.to_numeric, errors='coerce')
        long_view = pd.DataFrame(index=safe_frame.index)
        short_view = pd.DataFrame(index=safe_frame.index)

        rsi_7_centered = (safe_frame['mce_rsi_7'] - 50.0) / 50.0
        rsi_14_centered = (safe_frame['mce_rsi_14'] - 50.0) / 50.0

        long_view['mce2_return_1'] = safe_frame['mce_return_1']
        short_view['mce2_return_1'] = -safe_frame['mce_return_1']
        long_view['mce2_return_2'] = safe_frame['mce_return_2']
        short_view['mce2_return_2'] = -safe_frame['mce_return_2']
        long_view['mce2_return_3'] = safe_frame['mce_return_3']
        short_view['mce2_return_3'] = -safe_frame['mce_return_3']
        long_view['mce2_range_ratio'] = safe_frame['mce_range_ratio']
        short_view['mce2_range_ratio'] = safe_frame['mce_range_ratio']
        long_view['mce2_body_impulse_ratio'] = safe_frame['mce_body_ratio']
        short_view['mce2_body_impulse_ratio'] = -safe_frame['mce_body_ratio']
        long_view['mce2_lead_wick_ratio'] = safe_frame['mce_upper_wick_ratio']
        short_view['mce2_lead_wick_ratio'] = safe_frame['mce_lower_wick_ratio']
        long_view['mce2_trail_wick_ratio'] = safe_frame['mce_lower_wick_ratio']
        short_view['mce2_trail_wick_ratio'] = safe_frame['mce_upper_wick_ratio']
        long_view['mce2_close_progress'] = safe_frame['mce_close_location']
        short_view['mce2_close_progress'] = 1.0 - safe_frame['mce_close_location']
        long_view['mce2_volume_zscore_20'] = safe_frame['mce_volume_zscore_20']
        short_view['mce2_volume_zscore_20'] = safe_frame['mce_volume_zscore_20']
        long_view['mce2_ema_gap_9_21_ratio'] = safe_frame['mce_ema_gap_9_21_ratio']
        short_view['mce2_ema_gap_9_21_ratio'] = -safe_frame['mce_ema_gap_9_21_ratio']
        long_view['mce2_close_to_ema_9_ratio'] = safe_frame['mce_close_to_ema_9_ratio']
        short_view['mce2_close_to_ema_9_ratio'] = -safe_frame['mce_close_to_ema_9_ratio']
        long_view['mce2_close_to_ema_21_ratio'] = safe_frame['mce_close_to_ema_21_ratio']
        short_view['mce2_close_to_ema_21_ratio'] = -safe_frame['mce_close_to_ema_21_ratio']
        long_view['mce2_atr_14_ratio'] = safe_frame['mce_atr_14_ratio']
        short_view['mce2_atr_14_ratio'] = safe_frame['mce_atr_14_ratio']
        long_view['mce2_atr_slope_3'] = safe_frame['mce_atr_slope_3']
        short_view['mce2_atr_slope_3'] = safe_frame['mce_atr_slope_3']
        long_view['mce2_rsi_7_centered'] = rsi_7_centered
        short_view['mce2_rsi_7_centered'] = -rsi_7_centered
        long_view['mce2_rsi_14_centered'] = rsi_14_centered
        short_view['mce2_rsi_14_centered'] = -rsi_14_centered
        long_view['mce2_rsi_delta_1'] = safe_frame['mce_rsi_delta_1']
        short_view['mce2_rsi_delta_1'] = -safe_frame['mce_rsi_delta_1']
        long_view['mce2_adx_14'] = safe_frame['mce_adx_14']
        short_view['mce2_adx_14'] = safe_frame['mce_adx_14']
        long_view['mce2_di_pressure_14'] = safe_frame['mce_di_spread_14']
        short_view['mce2_di_pressure_14'] = -safe_frame['mce_di_spread_14']
        long_view['mce2_bb_width_ratio'] = safe_frame['mce_bb_width_ratio']
        short_view['mce2_bb_width_ratio'] = safe_frame['mce_bb_width_ratio']
        long_view['mce2_bb_progress'] = safe_frame['mce_bb_position']
        short_view['mce2_bb_progress'] = 1.0 - safe_frame['mce_bb_position']
        long_view['mce2_choppiness_14'] = safe_frame['mce_choppiness_14']
        short_view['mce2_choppiness_14'] = safe_frame['mce_choppiness_14']
        long_view['mce2_trendiness_14'] = safe_frame['mce_trendiness_14']
        short_view['mce2_trendiness_14'] = safe_frame['mce_trendiness_14']
        long_view['mce2_vwap_distance_ratio'] = safe_frame['mce_vwap_distance_ratio']
        short_view['mce2_vwap_distance_ratio'] = -safe_frame['mce_vwap_distance_ratio']
        long_view['mce2_recent_range_atr_5'] = safe_frame['mce_recent_range_atr_5']
        short_view['mce2_recent_range_atr_5'] = safe_frame['mce_recent_range_atr_5']
        long_view['mce2_recent_move_atr_3'] = safe_frame['mce_recent_move_atr_3']
        short_view['mce2_recent_move_atr_3'] = -safe_frame['mce_recent_move_atr_3']
        long_view['mce2_cost_to_atr_14'] = safe_frame['mce_cost_to_atr_14']
        short_view['mce2_cost_to_atr_14'] = safe_frame['mce_cost_to_atr_14']
        long_view['mce2_cost_to_range'] = safe_frame['mce_cost_to_range']
        short_view['mce2_cost_to_range'] = safe_frame['mce_cost_to_range']

        if {
            'mcep_bullish_reversal_score',
            'mcep_bearish_reversal_score',
            'mcep_bullish_continuation_score',
            'mcep_bearish_continuation_score',
        }.issubset(set(safe_frame.columns)):
            long_view['mce2_side_reversal_score'] = safe_frame['mcep_bullish_reversal_score']
            short_view['mce2_side_reversal_score'] = safe_frame['mcep_bearish_reversal_score']
            long_view['mce2_opposite_reversal_score'] = safe_frame['mcep_bearish_reversal_score']
            short_view['mce2_opposite_reversal_score'] = safe_frame['mcep_bullish_reversal_score']
            long_view['mce2_side_continuation_score'] = safe_frame['mcep_bullish_continuation_score']
            short_view['mce2_side_continuation_score'] = safe_frame['mcep_bearish_continuation_score']
            long_view['mce2_opposite_continuation_score'] = safe_frame['mcep_bearish_continuation_score']
            short_view['mce2_opposite_continuation_score'] = safe_frame['mcep_bullish_continuation_score']

        feature_columns = list(long_view.columns)
        return long_view, short_view, feature_columns

    def build_micro_cost_edge_canonical_sequence_dataset(self):
        if not self._applied:
            self.apply()

        candles = self.symbol.candles.copy()
        feature_frame = candles[self._feature_columns].apply(pd.to_numeric, errors='coerce')
        open_series = pd.to_numeric(candles['open'], errors='coerce')
        high_series = pd.to_numeric(candles['high'], errors='coerce')
        low_series = pd.to_numeric(candles['low'], errors='coerce')
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        horizon = max(1, int(getattr(self.config, 'target_horizon', 5) or 5))
        pip_size = max(1e-8, float(getattr(self.config, 'pip_size', 0.0001) or 0.0001))
        round_trip_cost_pips = max(0.0, float(getattr(self.config, 'round_trip_cost_pips', 1.6) or 0.0))
        edge_multiple = max(1.0, float(getattr(self.config, 'target_cost_edge_multiple', 1.75) or 1.0))
        edge_hurdle_pips = round_trip_cost_pips * edge_multiple
        edge_hurdle_price = edge_hurdle_pips * pip_size

        entry_ref_series = open_series.shift(-1)
        future_high, future_low = self._build_future_extrema(high_series, low_series, horizon)
        cost_edge_codes, resolution_codes = self._build_future_cost_edge_codes(
            entry_ref_series,
            high_series,
            low_series,
            horizon=horizon,
            edge_hurdle_price=edge_hurdle_price,
        )

        frame = feature_frame.copy()
        frame['target_entry_ref'] = entry_ref_series
        frame['target_future_long_edge_pips'] = ((future_high - entry_ref_series) / pip_size).clip(lower=0.0)
        frame['target_future_short_edge_pips'] = ((entry_ref_series - future_low) / pip_size).clip(lower=0.0)
        frame['target_edge_hurdle_pips'] = edge_hurdle_pips
        frame['target_cost_edge_code'] = cost_edge_codes.astype(int)
        frame['target_cost_edge_resolution_code'] = resolution_codes.astype(int)
        frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        frame['target_cost_edge_code'] = frame['target_cost_edge_code'].astype(int)
        frame['target_cost_edge_resolution_code'] = frame['target_cost_edge_resolution_code'].astype(int)

        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Micro cost-edge canonical dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        long_view, short_view, feature_columns = self._build_micro_cost_edge_canonical_views(frame[self.feature_columns])
        long_values = long_view.to_numpy(dtype=float)
        short_values = short_view.to_numpy(dtype=float)
        event_codes = frame['target_cost_edge_code'].to_numpy(dtype=int)
        sequence_end_indexes = list(range(observation_window - 1, total_rows))
        if not sequence_end_indexes:
            raise ValueError('No micro cost-edge canonical rows were available after feature generation.')

        X_long = []
        X_short = []
        y_long = []
        y_short = []
        y_event = []
        for end_index in sequence_end_indexes:
            start_index = end_index - observation_window + 1
            X_long.append(long_values[start_index:end_index + 1])
            X_short.append(short_values[start_index:end_index + 1])
            event_code = int(event_codes[end_index])
            y_long.append(1 if event_code == 1 else 0)
            y_short.append(1 if event_code == -1 else 0)
            y_event.append(event_code)

        candidate_summary = {
            'rows_after_cleaning': int(total_rows),
            'event_rows': int(len(X_long)),
            'side_rows': int(len(X_long) * 2),
            'long_edge_rows': int((frame['target_cost_edge_code'] == 1).sum()),
            'short_edge_rows': int((frame['target_cost_edge_code'] == -1).sum()),
            'no_edge_rows': int((frame['target_cost_edge_code'] == 0).sum()),
            'ambiguous_rows': int((frame['target_cost_edge_resolution_code'] == MICRO_COST_EDGE_FIRST_TOUCH_AMBIGUOUS_CODE).sum()),
            'timeout_rows': int((frame['target_cost_edge_resolution_code'] == 0).sum()),
            'pip_size': float(pip_size),
            'round_trip_cost_pips': float(round_trip_cost_pips),
            'target_cost_edge_multiple': float(edge_multiple),
            'edge_hurdle_pips': float(edge_hurdle_pips),
            'target_horizon': int(horizon),
        }

        return {
            'X_long': np.asarray(X_long, dtype=float),
            'X_short': np.asarray(X_short, dtype=float),
            'y_long': np.asarray(y_long, dtype=int),
            'y_short': np.asarray(y_short, dtype=int),
            'y_event_code': np.asarray(y_event, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_long),
            'side_rows': int(len(X_long) * 2),
            'observation_window': observation_window,
            'class_codes': list(MICRO_COST_EDGE_SIDE_CLASS_CODES),
            'class_labels': dict(MICRO_COST_EDGE_SIDE_CLASS_LABELS),
            'event_class_codes': list(MICRO_COST_EDGE_CLASS_CODES),
            'event_class_labels': dict(MICRO_COST_EDGE_CLASS_LABELS),
            'candidate_summary': candidate_summary,
        }

    def build_sequence_dataset(self):
        frame = self.build_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)

        if observation_window <= 1:
            X = frame[feature_columns].to_numpy(dtype=float)
            y = frame['target_signal_score'].to_numpy(dtype=float)
            return {
                'X': X[:, np.newaxis, :],
                'y': y,
                'feature_columns': feature_columns,
                'rows': len(frame),
                'observation_window': 1,
            }

        feature_values = frame[feature_columns].to_numpy(dtype=float)
        targets = frame['target_signal_score'].to_numpy(dtype=float)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Sequence dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        X_sequences = []
        y_sequences = []
        for end_index in range(observation_window - 1, total_rows):
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_sequences.append(targets[end_index])

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y': np.asarray(y_sequences, dtype=float),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
        }

    def build_regime_sequence_dataset(self):
        frame = self.build_regime_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)

        if observation_window <= 1:
            X = frame[feature_columns].to_numpy(dtype=float)
            y_class = frame['target_class_index'].to_numpy(dtype=int)
            return {
                'X': X[:, np.newaxis, :],
                'y_class': y_class,
                'feature_columns': feature_columns,
                'rows': len(frame),
                'observation_window': 1,
                'class_codes': list(REGIME_CLASS_CODES),
                'class_labels': dict(REGIME_CLASS_LABELS),
            }

        feature_values = frame[feature_columns].to_numpy(dtype=float)
        target_classes = frame['target_class_index'].to_numpy(dtype=int)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Sequence dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        X_sequences = []
        y_classes = []
        for end_index in range(observation_window - 1, total_rows):
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(target_classes[end_index])

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(REGIME_CLASS_CODES),
            'class_labels': dict(REGIME_CLASS_LABELS),
        }

    def build_candle_reversal_sequence_dataset(self):
        frame = self.build_candle_reversal_classification_dataset()
        observation_window = max(1, int(getattr(self.config, 'observation_window', 1) or 1))
        feature_columns = list(self.feature_columns)
        target_context_columns = [
            'target_prev_move_atr',
            'target_future_upside_atr',
            'target_future_downside_atr',
        ]

        if observation_window <= 1:
            X = frame[feature_columns].to_numpy(dtype=float)
            y_class = frame['target_class_index'].to_numpy(dtype=int)
            target_context = frame[target_context_columns].to_numpy(dtype=float)
            return {
                'X': X[:, np.newaxis, :],
                'y_class': y_class,
                'feature_columns': feature_columns,
                'target_context': target_context,
                'target_context_columns': target_context_columns,
                'rows': len(frame),
                'observation_window': 1,
                'class_codes': list(REVERSAL_CLASS_CODES),
                'class_labels': dict(REVERSAL_CLASS_LABELS),
                'target_filter_summary': self._last_target_filter_summary,
            }

        feature_values = frame[feature_columns].to_numpy(dtype=float)
        target_classes = frame['target_class_index'].to_numpy(dtype=int)
        target_context_values = frame[target_context_columns].to_numpy(dtype=float)
        total_rows = len(frame)
        if total_rows < observation_window:
            raise ValueError(
                f'Sequence dataset requires at least {observation_window} clean rows, but only {total_rows} are available.'
            )

        X_sequences = []
        y_classes = []
        target_context = []
        for end_index in range(observation_window - 1, total_rows):
            start_index = end_index - observation_window + 1
            X_sequences.append(feature_values[start_index:end_index + 1])
            y_classes.append(target_classes[end_index])
            target_context.append(target_context_values[end_index])

        return {
            'X': np.asarray(X_sequences, dtype=float),
            'y_class': np.asarray(y_classes, dtype=int),
            'feature_columns': feature_columns,
            'target_context': np.asarray(target_context, dtype=float),
            'target_context_columns': target_context_columns,
            'rows': len(X_sequences),
            'observation_window': observation_window,
            'class_codes': list(REVERSAL_CLASS_CODES),
            'class_labels': dict(REVERSAL_CLASS_LABELS),
            'target_filter_summary': self._last_target_filter_summary,
        }
