from dataclasses import dataclass
from typing import Sequence


@dataclass(slots=True)
class SupervisedFeatureConfig:
    symbol_name: str
    timeframe: str
    bars: int
    network_id: str = ''
    feature_profile: str = 'indicator_fusion'
    observation_window: int = 1
    include_volume: bool = True
    normalize_volume: bool = True
    normalization_mode: str = 'volume'
    normalization_columns: Sequence[str] | None = None
    target_horizon: int = 1
    target_mode: str = 'excursion_signal'
    target_std_window: int = 20
    target_std_threshold: float = 1.0
    target_regime_compression_threshold: float = 0.9
    target_regime_volatility_threshold: float = 2.2
    target_regime_trend_efficiency_threshold: float = 0.55
    target_regime_directional_move_threshold: float = 0.35
    target_regime_directional_dominance_threshold: float = 0.6
    target_pretrend_lookback: int = 6
    target_pretrend_threshold: float = 1.2
    target_reversal_threshold: float = 1.0
    target_dominance_ratio: float = 1.35
    target_reversal_take_profit_atr: float = 0.75
    target_reversal_stop_loss_atr: float = 1.0
    target_stage1_neutral_pretrend_ceiling: float = 0.85
    target_stage1_neutral_excursion_ceiling: float = 0.85
    target_stage1_positive_pretrend_floor: float = 0.0
    target_stage1_positive_excursion_floor: float = 0.0
    target_clean_neutral_pretrend_ceiling: float = 0.0
    target_clean_neutral_excursion_ceiling: float = 0.0
    target_clean_positive_pretrend_floor: float = 0.0
    target_clean_positive_excursion_floor: float = 0.0
    target_quality_good_excursion_threshold: float = 0.82
    target_quality_bad_excursion_threshold: float = 0.52
    target_quality_good_dominance_ratio: float = 1.1
    target_quality_bad_dominance_ratio: float = 1.1
    target_quality_good_counter_excursion_ceiling: float = 0.45
    target_quality_bad_counter_excursion_ceiling: float = 0.45
    pip_size: float = 0.0001
    round_trip_cost_pips: float = 1.6
    target_cost_edge_multiple: float = 1.75
    setup_adx_ceiling: float = 28.0
    setup_prev_rsi_ceiling: float = 38.0
    setup_current_rsi_floor: float = 38.0
    setup_current_rsi_ceiling: float = 50.0
    setup_touch_slack_atr: float = 0.06
    setup_prev_band_slack_atr: float = 0.08
    setup_bounce_fraction: float = 0.02
    setup_di_spread_floor: float = 0.0
    setup_candidate_min_gap_bars: int = 0


@dataclass(slots=True)
class SupervisedTrainingConfig:
    hidden_layers: list | None = None
    conv_filters: int = 16
    kernel_size: int = 3
    learning_rate: float = 0.01
    epochs: int = 120
    batch_size: int = 64
    threshold: float = 0.5
    seed: int = 42
    class_weight_mode: str = 'none'
    class_weight_exponent: float = 1.0
    neutral_retention: float = 1.0
