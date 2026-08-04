import argparse
from pathlib import Path

"""Legacy manual CLI helper.

The neural panel/runtime uses backend routes plus `backend/python/neural/runners.py`.
Keep this script only for ad-hoc local experiments.
"""

try:
    from .config import RLFeatureConfig, RLTrainingConfig
    from .features import VasconcellosRLFeaturePipeline
    from .trainer import StableBaselinesRLTrainer
except ImportError:
    from neural.reinforcement.config import RLFeatureConfig, RLTrainingConfig
    from neural.reinforcement.features import VasconcellosRLFeaturePipeline
    from neural.reinforcement.trainer import StableBaselinesRLTrainer


def build_argument_parser():
    parser = argparse.ArgumentParser(
        description='Train an offline RL trader using OHLC, volume and Vasconcellos Envelope features.',
    )
    parser.add_argument('--symbol', required=True, help='Trading symbol, e.g. EURUSD')
    parser.add_argument('--timeframe', required=True, help='Timeframe, e.g. M5')
    parser.add_argument('--bars', type=int, default=5000, help='Number of bars to fetch from the bridge')
    parser.add_argument('--timesteps', type=int, default=100000, help='RL total timesteps')
    parser.add_argument('--transaction-cost', type=float, default=0.0, help='Per-position-change cost in reward units')
    parser.add_argument('--reward-scale', type=float, default=1.0, help='Reward multiplier')
    parser.add_argument('--position-size', type=float, default=1.0, help='Position multiplier')
    parser.add_argument('--observation-window', type=int, default=1, help='How many bars compose one observation')
    parser.add_argument('--allow-short', action='store_true', help='Allow short actions in the RL environment')
    parser.add_argument('--normalize-volume', action='store_true', help='Z-score normalize volume before training')
    parser.add_argument('--export-dataset', default='', help='Optional CSV path to export the training frame')
    parser.add_argument('--save-model', default='', help='Optional path to save the trained model')
    return parser


def main():
    args = build_argument_parser().parse_args()

    feature_config = RLFeatureConfig(
        symbol_name=args.symbol,
        timeframe=args.timeframe,
        bars=max(1, int(args.bars)),
    )

    pipeline = VasconcellosRLFeaturePipeline.from_bridge(feature_config)
    training_frame = pipeline.build_training_frame(
        dropna=True,
        normalize_volume=bool(args.normalize_volume),
    )

    print(f'Built training frame with {len(training_frame)} rows.')
    print(f'Observation columns: {", ".join(pipeline.observation_columns)}')

    if args.export_dataset:
        export_path = Path(args.export_dataset).expanduser().resolve()
        export_path.parent.mkdir(parents=True, exist_ok=True)
        training_frame.to_csv(export_path, index=False)
        print(f'Exported dataset to {export_path}')

    training_config = RLTrainingConfig(
        total_timesteps=max(1, int(args.timesteps)),
        transaction_cost=float(args.transaction_cost),
        reward_scale=float(args.reward_scale),
        position_size=float(args.position_size),
        observation_window=max(1, int(args.observation_window)),
        allow_short=bool(args.allow_short),
    )

    trainer = StableBaselinesRLTrainer(
        training_frame,
        feature_columns=pipeline.observation_columns,
        config=training_config,
    )

    model = trainer.train()

    if args.save_model:
        save_path = Path(args.save_model).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        model.save(str(save_path))
        print(f'Saved model to {save_path}')

    print('RL training completed successfully.')


if __name__ == '__main__':
    main()
