from ...calculator import Calculator
from ...symbol import Symbol
from ....neural.supervised.config import SupervisedFeatureConfig
from ....neural.supervised.features import BasicFeedForwardFeaturePipeline


FEATURE_COLUMNS = [
    'slq_di_spread_14',
    'slq_rsi_delta_3',
    'slq_reclaim_strength',
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


def _build_feature_config(symbol: Symbol) -> SupervisedFeatureConfig:
    return SupervisedFeatureConfig(
        symbol_name=str(symbol.name),
        timeframe=str(symbol.timeframe),
        bars=int(len(symbol.candles.index)),
        network_id='ema_low_adx_setup_quality_cnn_v6',
        feature_profile='ema_low_adx_setup_quality_pattern_score_cluster_context',
        observation_window=24,
        include_volume=True,
        normalize_volume=False,
        normalization_columns=[],
        target_horizon=8,
        target_mode='ema_low_adx_setup_quality_good_vs_rest_classification',
        target_quality_good_excursion_threshold=0.82,
        target_quality_bad_excursion_threshold=0.52,
        target_quality_good_dominance_ratio=1.1,
        target_quality_bad_dominance_ratio=1.1,
        target_quality_good_counter_excursion_ceiling=0.45,
        target_quality_bad_counter_excursion_ceiling=0.45,
        setup_adx_ceiling=28.0,
        setup_prev_rsi_ceiling=38.0,
        setup_current_rsi_floor=38.0,
        setup_current_rsi_ceiling=50.0,
        setup_touch_slack_atr=0.06,
        setup_prev_band_slack_atr=0.08,
        setup_bounce_fraction=0.02,
    )


class EmaLowAdxSetupClusterContextV1(Calculator):
    """
    Deterministic cluster-context surface reused from the v6 setup-quality neural pipeline.

    Outputs the causal local-cluster features needed to test whether recent candidate spacing,
    density, and relative reclaim strength can improve the paper-37 ordering surface without
    depending on a sparse exported neural score.
    """

    def __init__(self, symbol):
        super().__init__('EmaLowAdxSetupClusterContextV1')

        temp_symbol = Symbol(symbol.name, symbol.timeframe, len(symbol.candles), candles=symbol.candles)
        pipeline = BasicFeedForwardFeaturePipeline(temp_symbol, _build_feature_config(temp_symbol))
        pipeline.apply()

        missing_columns = [column for column in FEATURE_COLUMNS if column not in temp_symbol.candles.columns]
        if missing_columns:
            missing_list = ', '.join(missing_columns)
            raise ValueError(
                'EmaLowAdxSetupClusterContextV1 is missing expected cluster-context columns: '
                f'{missing_list}'
            )

        for column_name in FEATURE_COLUMNS:
            symbol.add_feature(f'{self.name}_{column_name}', temp_symbol.candles[column_name])
