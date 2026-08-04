import numpy as np
import pandas as pd

from ...calculator import Calculator


VALID_REFERENCES = {'body', 'shadow', 'wick'}
VALID_DELTA_UNITS = {'abs', 'percentage', 'std_dev'}
VALID_STD_DEV_MODES = {'rolling', 'expanding', 'full_series'}


def _ema_like_legacy(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(1, int(period or 1))
    return pd.to_numeric(series, errors='coerce').ewm(
        span=safe_period,
        adjust=False,
        min_periods=safe_period,
    ).mean()


def _normalize_reference(reference: str) -> str:
    safe_reference = str(reference or 'body').strip().lower() or 'body'
    if safe_reference not in VALID_REFERENCES:
        raise ValueError("reference must be one of: 'body', 'shadow', 'wick'")
    return 'shadow' if safe_reference == 'wick' else safe_reference


def _normalize_delta_unit(delta_unit: str) -> str:
    safe_delta_unit = str(delta_unit or 'abs').strip().lower() or 'abs'
    if safe_delta_unit not in VALID_DELTA_UNITS:
        raise ValueError("delta_unit must be one of: 'abs', 'percentage', 'std_dev'")
    return safe_delta_unit


def _normalize_std_dev_mode(std_dev_mode: str) -> str:
    safe_mode = str(std_dev_mode or 'rolling').strip().lower() or 'rolling'
    if safe_mode not in VALID_STD_DEV_MODES:
        raise ValueError("std_dev_mode must be one of: 'rolling', 'expanding', 'full_series'")
    return safe_mode


def _validate_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise TypeError('df must be a pandas.DataFrame')

    required_columns = ['open', 'high', 'low', 'close']
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise ValueError(f'df is missing required OHLC columns: {missing_columns}')

    frame = df.loc[:, required_columns].copy()
    for column in required_columns:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame


def _resolve_top_bottom(frame: pd.DataFrame, reference: str) -> tuple[np.ndarray, np.ndarray]:
    open_ = frame['open']
    high = frame['high']
    low = frame['low']
    close = frame['close']

    if reference == 'body':
        top = pd.concat([open_, close], axis=1).max(axis=1)
        bottom = pd.concat([open_, close], axis=1).min(axis=1)
    else:
        top = high
        bottom = low

    return (
        pd.to_numeric(top, errors='coerce').to_numpy(dtype=float),
        pd.to_numeric(bottom, errors='coerce').to_numpy(dtype=float),
    )


def _build_delta_series(
    top: np.ndarray,
    bottom: np.ndarray,
    *,
    delta_value: float,
    delta_unit: str,
    std_dev_mode: str,
    std_dev_window: int,
    legacy_mode: bool,
) -> tuple[np.ndarray, np.ndarray]:
    length = len(top)
    safe_delta_value = float(delta_value or 0.0)
    safe_top = pd.Series(top, dtype=float)
    safe_bottom = pd.Series(bottom, dtype=float)

    if safe_delta_value == 0.0:
        return (
            np.zeros(length, dtype=float),
            np.zeros(length, dtype=float),
        )

    if delta_unit == 'abs':
        return (
            np.full(length, safe_delta_value, dtype=float),
            np.full(length, safe_delta_value, dtype=float),
        )

    if delta_unit == 'percentage':
        delta_top = np.zeros(length, dtype=float)
        delta_bottom = np.zeros(length, dtype=float)
        if length > 1:
            delta_top[1:] = np.nan_to_num(top[:-1], nan=0.0) * safe_delta_value
            delta_bottom[1:] = np.nan_to_num(bottom[:-1], nan=0.0) * safe_delta_value
        return delta_top, delta_bottom

    if legacy_mode or std_dev_mode == 'full_series':
        top_std = float(np.nanstd(top))
        bottom_std = float(np.nanstd(bottom))
        return (
            np.full(length, top_std * safe_delta_value, dtype=float),
            np.full(length, bottom_std * safe_delta_value, dtype=float),
        )

    if std_dev_mode == 'expanding':
        top_std_series = safe_top.expanding(min_periods=2).std(ddof=0).shift(1)
        bottom_std_series = safe_bottom.expanding(min_periods=2).std(ddof=0).shift(1)
    else:
        safe_window = max(2, int(std_dev_window or 2))
        top_std_series = safe_top.rolling(window=safe_window, min_periods=safe_window).std(ddof=0).shift(1)
        bottom_std_series = safe_bottom.rolling(window=safe_window, min_periods=safe_window).std(ddof=0).shift(1)

    return (
        top_std_series.fillna(0.0).to_numpy(dtype=float) * safe_delta_value,
        bottom_std_series.fillna(0.0).to_numpy(dtype=float) * safe_delta_value,
    )


def _safe_nanmax(values: np.ndarray) -> float:
    finite_values = values[np.isfinite(values)]
    if finite_values.size == 0:
        return np.nan
    return float(np.max(finite_values))


def _safe_nanmin(values: np.ndarray) -> float:
    finite_values = values[np.isfinite(values)]
    if finite_values.size == 0:
        return np.nan
    return float(np.min(finite_values))


def _equal_float(left: float, right: float, *, causal: bool) -> bool:
    if not (np.isfinite(left) and np.isfinite(right)):
        return False
    if not causal:
        return bool(left == right)
    return bool(np.isclose(left, right, rtol=1e-9, atol=1e-12))


def compute_vasconcellos_envelope(
    df: pd.DataFrame,
    *,
    reference: str = 'body',
    span: int = 2,
    delta_value: float = 0.0,
    delta_unit: str = 'abs',
    momentum_ema_period: int = 14,
    std_dev_mode: str = 'rolling',
    std_dev_window: int = 20,
    legacy_mode: bool = False,
) -> pd.DataFrame:
    """
    Compute stepwise support/resistance levels plus structural momentum.

    `legacy_mode=True` reproduces the historical robot behavior as closely as
    possible, including:
    - `resistance` warmup starting at `0`
    - `support` warmup starting at `inf`
    - global full-series `std_dev` delta
    - the original `i + 1 <= span` boundary, which allows a negative index read

    `legacy_mode=False` uses the corrected causal implementation:
    - warmup levels remain `NaN` until a valid level exists
    - `std_dev` delta is computed from past-only rolling/expanding history
    - the first valid index requires `candidate_index - 1 >= 0`
    - pivot equality uses tolerant float comparison
    """

    frame = _validate_ohlc_frame(df)
    safe_reference = _normalize_reference(reference)
    safe_delta_unit = _normalize_delta_unit(delta_unit)
    safe_std_dev_mode = _normalize_std_dev_mode(std_dev_mode)
    safe_span = max(1, int(span or 1))
    safe_momentum_period = max(1, int(momentum_ema_period or 1))
    safe_std_window = max(2, int(std_dev_window or 2))

    top, bottom = _resolve_top_bottom(frame, safe_reference)
    delta_top_series, delta_bottom_series = _build_delta_series(
        top,
        bottom,
        delta_value=float(delta_value or 0.0),
        delta_unit=safe_delta_unit,
        std_dev_mode=safe_std_dev_mode,
        std_dev_window=safe_std_window,
        legacy_mode=bool(legacy_mode),
    )

    length = len(top)
    if legacy_mode:
        resistance = np.zeros(length, dtype=float)
        support = np.full(length, np.inf, dtype=float)
    else:
        resistance = np.full(length, np.nan, dtype=float)
        support = np.full(length, np.nan, dtype=float)

    for i in range(length):
        if legacy_mode:
            if i + 1 <= safe_span:
                continue
        else:
            if i <= safe_span:
                continue

        candidate_index = i - safe_span
        previous_candidate_index = candidate_index - 1
        if not legacy_mode and previous_candidate_index < 0:
            continue

        delta_top = float(delta_top_series[i]) if i < len(delta_top_series) else 0.0
        delta_bottom = float(delta_bottom_series[i]) if i < len(delta_bottom_series) else 0.0

        previous_resistance = resistance[i - 1]
        previous_support = support[i - 1]
        resistance[i] = previous_resistance
        support[i] = previous_support

        if candidate_index < 0 or candidate_index >= length:
            continue

        candidate_top = float(top[candidate_index]) if np.isfinite(top[candidate_index]) else np.nan
        candidate_bottom = float(bottom[candidate_index]) if np.isfinite(bottom[candidate_index]) else np.nan
        previous_top = float(top[previous_candidate_index]) if -length <= previous_candidate_index < length and np.isfinite(top[previous_candidate_index]) else np.nan
        previous_bottom = float(bottom[previous_candidate_index]) if -length <= previous_candidate_index < length and np.isfinite(bottom[previous_candidate_index]) else np.nan

        top_window = top[candidate_index:i]
        bottom_window = bottom[candidate_index:i]

        if np.isfinite(candidate_top):
            window_max = _safe_nanmax(top_window)
            candidate_breaks_previous = (
                not np.isfinite(previous_resistance)
                or candidate_top > previous_resistance + delta_top
                or candidate_top > previous_top + delta_top
            )
            if _equal_float(window_max, candidate_top, causal=not legacy_mode) and candidate_breaks_previous:
                resistance[i] = candidate_top

        if np.isfinite(candidate_bottom):
            window_min = _safe_nanmin(bottom_window)
            candidate_breaks_previous = (
                not np.isfinite(previous_support)
                or candidate_bottom < previous_support - delta_bottom
                or candidate_bottom < previous_bottom - delta_bottom
            )
            if _equal_float(window_min, candidate_bottom, causal=not legacy_mode) and candidate_breaks_previous:
                support[i] = candidate_bottom

    top_momentum = np.zeros(length, dtype=float)
    bottom_momentum = np.zeros(length, dtype=float)

    for i in range(1, length):
        previous_resistance = resistance[i - 1]
        current_resistance = resistance[i]
        previous_support = support[i - 1]
        current_support = support[i]

        if np.isfinite(current_resistance) and not np.isfinite(previous_resistance):
            top_momentum[i] = 1.0
        elif np.isfinite(current_resistance) and np.isfinite(previous_resistance):
            if current_resistance > previous_resistance:
                top_momentum[i] = 1.0 if top_momentum[i - 1] < 0 else top_momentum[i - 1] + 1.0
            elif current_resistance < previous_resistance:
                top_momentum[i] = -1.0 if top_momentum[i - 1] > 0 else top_momentum[i - 1] - 1.0
            else:
                top_momentum[i] = top_momentum[i - 1]
        else:
            top_momentum[i] = top_momentum[i - 1]

        if np.isfinite(current_support) and not np.isfinite(previous_support):
            bottom_momentum[i] = -1.0
        elif np.isfinite(current_support) and np.isfinite(previous_support):
            if current_support > previous_support:
                bottom_momentum[i] = 1.0 if bottom_momentum[i - 1] < 0 else bottom_momentum[i - 1] + 1.0
            elif current_support < previous_support:
                bottom_momentum[i] = -1.0 if bottom_momentum[i - 1] > 0 else bottom_momentum[i - 1] - 1.0
            else:
                bottom_momentum[i] = bottom_momentum[i - 1]
        else:
            bottom_momentum[i] = bottom_momentum[i - 1]

    momentum = top_momentum + bottom_momentum
    momentum_ema = _ema_like_legacy(pd.Series(momentum, index=df.index), safe_momentum_period).to_numpy(dtype=float)

    return pd.DataFrame(
        {
            'resistance': resistance,
            'support': support,
            'momentum': momentum,
            'top_momentum': top_momentum,
            'bottom_momentum': bottom_momentum,
            'momentum_ema': momentum_ema,
        },
        index=df.index,
    )


class VasconcellosEnvelope(Calculator):
    def __init__(
        self,
        symbol,
        reference='body',
        span=2,
        deltaValue=0.0,
        deltaUnit='abs',
        momentumEmaPeriod=14,
        stdDevMode='rolling',
        stdDevWindow=20,
    ):
        safe_reference = _normalize_reference(reference)
        safe_span = max(1, int(span or 1))
        safe_delta_value = float(deltaValue or 0.0)
        safe_delta_unit = _normalize_delta_unit(deltaUnit)
        safe_momentum_period = max(1, int(momentumEmaPeriod or 1))
        safe_std_dev_mode = _normalize_std_dev_mode(stdDevMode)
        safe_std_dev_window = max(2, int(stdDevWindow or 2))

        super().__init__(
            'VasconcellosEnvelope',
            safe_reference,
            safe_span,
            safe_delta_value,
            safe_delta_unit,
            safe_momentum_period,
            safe_std_dev_mode,
            safe_std_dev_window,
        )

        computed = compute_vasconcellos_envelope(
            symbol.candles,
            reference=safe_reference,
            span=safe_span,
            delta_value=safe_delta_value,
            delta_unit=safe_delta_unit,
            momentum_ema_period=safe_momentum_period,
            std_dev_mode=safe_std_dev_mode,
            std_dev_window=safe_std_dev_window,
            legacy_mode=False,
        )

        symbol.add_feature(self.name + '_resistance', computed['resistance'])
        symbol.add_feature(self.name + '_support', computed['support'])
        symbol.add_feature(self.name + '_momentum', computed['momentum'])
        symbol.add_feature(self.name + '_top_momentum', computed['top_momentum'])
        symbol.add_feature(self.name + '_bottom_momentum', computed['bottom_momentum'])
        symbol.add_feature(self.name + '_momentum_ema', computed['momentum_ema'])
