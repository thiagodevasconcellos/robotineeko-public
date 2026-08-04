from dataclasses import dataclass, field


@dataclass(slots=True)
class RLFeatureConfig:
    symbol_name: str
    timeframe: str
    bars: int
    feature_profile: str = 'vasconcellos'
    include_volume: bool = True
    volume_column_candidates: tuple[str, ...] = ('volume', 'tick_volume', 'real_volume')
    vasconcellos_reference: str = 'wick'
    vasconcellos_span: int = 2
    vasconcellos_delta_value: float = 0.0
    vasconcellos_delta_unit: str = 'std_dev'
    vasconcellos_relevant_support_left: int = 1
    vasconcellos_relevant_support_right: int = 3
    vasconcellos_relevant_resistance_left: int = 1
    vasconcellos_relevant_resistance_right: int = 3
    vasconcellos_lines: str = 'all'
    market_regime_ema_fast_period: int = 9
    market_regime_ema_slow_period: int = 21
    market_regime_adx_period: int = 14
    market_regime_atr_period: int = 14
    market_regime_bollinger_period: int = 20
    market_regime_bollinger_std_dev: float = 2.0
    market_regime_donchian_period: int = 20
    market_regime_choppiness_period: int = 14
    market_regime_supertrend_atr_period: int = 10
    market_regime_supertrend_multiplier: float = 3.0
    market_regime_vwap_source: str = 'hlc3'
    market_regime_score_smoothing_period: int = 5
    market_regime_regime_confirm_bars: int = 3

    def build_vasconcellos_params(self):
        return [
            self.vasconcellos_reference,
            self.vasconcellos_span,
            self.vasconcellos_delta_value,
            self.vasconcellos_delta_unit,
            self.vasconcellos_relevant_support_left,
            self.vasconcellos_relevant_support_right,
            self.vasconcellos_relevant_resistance_left,
            self.vasconcellos_relevant_resistance_right,
            self.vasconcellos_lines,
        ]

    def build_market_regime_params(self):
        return [
            self.market_regime_ema_fast_period,
            self.market_regime_ema_slow_period,
            self.market_regime_adx_period,
            self.market_regime_atr_period,
            self.market_regime_bollinger_period,
            self.market_regime_bollinger_std_dev,
            self.market_regime_donchian_period,
            self.market_regime_choppiness_period,
            self.market_regime_supertrend_atr_period,
            self.market_regime_supertrend_multiplier,
            self.market_regime_vwap_source,
            self.market_regime_score_smoothing_period,
            self.market_regime_regime_confirm_bars,
        ]


@dataclass(slots=True)
class RLTrainingConfig:
    algorithm: str = 'PPO'
    total_timesteps: int = 100_000
    learning_rate: float = 3e-4
    gamma: float = 0.99
    observation_window: int = 1
    transaction_cost: float = 0.0
    reward_scale: float = 1.0
    position_size: float = 1.0
    allow_short: bool = True
    holding_cost: float = 0.0
    flat_reward: float = 0.0
    imbalance_penalty: float = 0.0
    same_side_streak_penalty: float = 0.0
    seed: int | None = None
    algorithm_kwargs: dict = field(default_factory=dict)
