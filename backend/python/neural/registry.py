from copy import deepcopy


NETWORK_FAMILY_REGISTRY = {
    'reinforcement_learning': {
        'id': 'reinforcement_learning',
        'label': 'Reinforcement Learning',
        'description': 'Agents that learn trading policies from reward feedback.',
    },
    'supervised_learning': {
        'id': 'supervised_learning',
        'label': 'Supervised Learning',
        'description': 'Models trained from labeled targets such as returns, classes or regimes.',
    },
}

NETWORK_ARCHITECTURE_REGISTRY = {
    'feed_forward': {
        'id': 'feed_forward',
        'label': 'Feed Forward',
        'description': 'Dense fully connected models over engineered feature vectors.',
    },
    'lstm': {
        'id': 'lstm',
        'label': 'LSTM',
        'description': 'Recurrent sequence models for temporal state and memory.',
    },
    'convolutional': {
        'id': 'convolutional',
        'label': 'Convolutional',
        'description': 'CNN-style models for local pattern extraction over sequences.',
    },
    'reinforcement': {
        'id': 'reinforcement',
        'label': 'Reinforcement',
        'description': 'Policy-learning agents trained from reward feedback.',
    },
}


NETWORK_REGISTRY = {
    'market_regime_rl_v1': {
        'id': 'market_regime_rl_v1',
        'label': 'Market Regime RL PPO',
        'family': 'reinforcement_learning',
        'architecture_type': 'reinforcement',
        'description': (
            'PPO trading agent trained on OHLCV plus Market Regime state features, so the policy can react '
            'to both raw candle movement and the current trend/compression context.'
        ),
        'signature': (
            'Each observation is built from OHLCV and the continuous Market Regime outputs: trend score, '
            'volatility score, compression score, direction score, stability score, regime age, and regime code. '
            'This gives the policy direct access to both the tape and a regime abstraction instead of forcing it '
            'to infer everything from price alone.\n\n'
            'Observation vector per candle:\n'
            'open, high, low, close, volume,\n'
            'market_regime_trend_score,\n'
            'market_regime_volatility_score,\n'
            'market_regime_compression_score,\n'
            'market_regime_direction_score,\n'
            'market_regime_stability_score,\n'
            'market_regime_regime_age,\n'
            'market_regime_regime_code\n\n'
            'The offline RL environment then rolls these features into a fixed observation window and trains a PPO '
            'policy over flat, long, and short actions with transaction-cost-aware rewards.'
        ),
        'network_type': 'stable_baselines3_ppo',
        'runner_id': 'market_regime_rl_v1',
        'score_metric': 'mean_reward',
        'score_label': 'Mean reward',
        'task_label': 'OHLCV + Market Regime PPO policy',
        'train_action_label': 'Train',
        'test_action_label': 'Test',
        'test_source_options': [
            {'id': 'latest_train', 'label': 'Test latest train'},
            {'id': 'best_model', 'label': 'Test best model'},
        ],
        'feature_set': [
            'ohlcv',
            'market_regime_trend_score',
            'market_regime_volatility_score',
            'market_regime_compression_score',
            'market_regime_direction_score',
            'market_regime_stability_score',
            'market_regime_regime_age',
            'market_regime_regime_code',
        ],
        'snapshot_cards': [
            {
                'id': 'best_validation_reward',
                'label': 'Best validation reward',
                'source': 'best_model',
                'metric_path': 'validation.mean_reward',
                'format': 'score',
                'hint': 'Best promoted PPO model measured on validation episodes.',
            },
            {
                'id': 'latest_validation_reward',
                'label': 'Latest validation reward',
                'source': 'latest_train',
                'metric_path': 'validation.mean_reward',
                'format': 'score',
                'hint': 'Latest completed training run validation result.',
            },
            {
                'id': 'latest_test_reward',
                'label': 'Latest test reward',
                'source': 'latest_test',
                'metric_path': 'mean_reward',
                'format': 'score',
                'hint': 'Latest chronological holdout evaluation.',
            },
            {
                'id': 'latest_trade_count',
                'label': 'Latest trade count',
                'source': 'latest_test',
                'metric_path': 'trade_count',
                'format': 'integer',
                'hint': 'How many position changes happened during the latest test run.',
            },
        ],
        'metric_sections': [
            {
                'id': 'validation_metrics',
                'label': 'Validation metrics',
                'source': 'latest_train',
                'metric_root': 'validation',
                'metrics': [
                    {'key': 'mean_reward', 'label': 'Mean reward', 'format': 'score'},
                    {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                    {'key': 'win_rate', 'label': 'Win rate', 'format': 'percent'},
                    {'key': 'profit_factor', 'label': 'Profit factor', 'format': 'score'},
                    {'key': 'trade_count', 'label': 'Trade count', 'format': 'integer'},
                    {'key': 'max_drawdown', 'label': 'Max drawdown', 'format': 'score'},
                ],
            },
            {
                'id': 'test_metrics',
                'label': 'Test metrics',
                'source': 'latest_test',
                'metrics': [
                    {'key': 'mean_reward', 'label': 'Mean reward', 'format': 'score'},
                    {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                    {'key': 'win_rate', 'label': 'Win rate', 'format': 'percent'},
                    {'key': 'profit_factor', 'label': 'Profit factor', 'format': 'score'},
                    {'key': 'trade_count', 'label': 'Trade count', 'format': 'integer'},
                    {'key': 'max_drawdown', 'label': 'Max drawdown', 'format': 'score'},
                ],
            },
        ],
        'defaults': {
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 10000,
            'algorithm': 'PPO',
            'validationSplit': 0.15,
            'testSplit': 0.15,
            'observationWindow': 16,
            'totalTimesteps': 75000,
            'learningRate': 0.0003,
            'gamma': 0.99,
            'transactionCost': 0.0001,
            'rewardScale': 1.0,
            'positionSize': 1.0,
            'allowShort': True,
            'normalizeVolume': True,
            'testEpisodes': 3,
            'marketRegimeEmaFastPeriod': 9,
            'marketRegimeEmaSlowPeriod': 21,
            'marketRegimeAdxPeriod': 14,
            'marketRegimeAtrPeriod': 14,
            'marketRegimeBollingerPeriod': 20,
            'marketRegimeBollingerStdDev': 2.0,
            'marketRegimeDonchianPeriod': 20,
            'marketRegimeChoppinessPeriod': 14,
            'marketRegimeSupertrendAtrPeriod': 10,
            'marketRegimeSupertrendMultiplier': 3.0,
            'marketRegimeVwapSource': 'hlc3',
            'marketRegimeScoreSmoothingPeriod': 5,
            'marketRegimeConfirmBars': 3,
        },
        'parameter_schema': [
            {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
            {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
            {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 500, 'group': 'dataset'},
            {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
            {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
            {'key': 'algorithm', 'label': 'Algorithm', 'type': 'string', 'group': 'training', 'options': [{'value': 'PPO', 'label': 'PPO'}]},
            {'key': 'totalTimesteps', 'label': 'Total timesteps', 'type': 'number', 'min': 1000, 'group': 'training'},
            {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
            {'key': 'gamma', 'label': 'Gamma', 'type': 'number', 'min': 0.5, 'max': 0.9999, 'step': 'any', 'group': 'training'},
            {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 1, 'group': 'architecture'},
            {'key': 'transactionCost', 'label': 'Transaction cost', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'training'},
            {'key': 'rewardScale', 'label': 'Reward scale', 'type': 'number', 'min': 0.000001, 'step': 'any', 'group': 'training'},
            {'key': 'positionSize', 'label': 'Position size', 'type': 'number', 'min': 0.000001, 'step': 'any', 'group': 'training'},
            {'key': 'allowShort', 'label': 'Allow short', 'type': 'boolean', 'group': 'training'},
            {'key': 'normalizeVolume', 'label': 'Normalize volume', 'type': 'boolean', 'group': 'normalization'},
            {'key': 'testEpisodes', 'label': 'Test episodes', 'type': 'number', 'min': 1, 'group': 'training'},
            {'key': 'marketRegimeEmaFastPeriod', 'label': 'EMA fast', 'type': 'number', 'min': 1, 'group': 'special'},
            {'key': 'marketRegimeEmaSlowPeriod', 'label': 'EMA slow', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeAdxPeriod', 'label': 'ADX period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeAtrPeriod', 'label': 'ATR period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeBollingerPeriod', 'label': 'Bollinger period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeBollingerStdDev', 'label': 'Bollinger std dev', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
            {'key': 'marketRegimeDonchianPeriod', 'label': 'Donchian period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeChoppinessPeriod', 'label': 'Choppiness period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeSupertrendAtrPeriod', 'label': 'Supertrend ATR', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeSupertrendMultiplier', 'label': 'Supertrend multiplier', 'type': 'number', 'min': 0.1, 'step': 'any', 'group': 'special'},
            {'key': 'marketRegimeVwapSource', 'label': 'VWAP source', 'type': 'string', 'group': 'special', 'options': [{'value': 'hlc3', 'label': 'hlc3'}, {'value': 'ohlc4', 'label': 'ohlc4'}, {'value': 'close', 'label': 'close'}]},
            {'key': 'marketRegimeScoreSmoothingPeriod', 'label': 'Score smoothing', 'type': 'number', 'min': 1, 'group': 'special'},
            {'key': 'marketRegimeConfirmBars', 'label': 'Regime confirm bars', 'type': 'number', 'min': 1, 'group': 'special'},
        ],
        'parameter_groups': [
            {'id': 'dataset', 'label': 'Dataset'},
            {'id': 'architecture', 'label': 'Architecture'},
            {'id': 'training', 'label': 'Training'},
            {'id': 'normalization', 'label': 'Normalization'},
            {'id': 'special', 'label': 'Market Regime'},
        ],
    },
    'market_regime_rl_v2': {
        'id': 'market_regime_rl_v2',
        'label': 'Market Regime RL PPO v2',
        'family': 'reinforcement_learning',
        'architecture_type': 'reinforcement',
        'description': (
            'Second PPO variant over OHLCV plus Market Regime features, with reward shaping to reduce one-sided '
            'policy collapse and encourage more balanced participation.'
        ),
        'signature': (
            'Same observation space as v1, but the reward now includes extra discipline terms: holding cost, '
            'flat reward, directional imbalance penalty, and same-side streak penalty. The goal is to stop the '
            'agent from discovering trivial always-short or always-long behavior on a narrow sample.\n\n'
            'Observation vector per candle:\n'
            'open, high, low, close, volume,\n'
            'market_regime_trend_score,\n'
            'market_regime_volatility_score,\n'
            'market_regime_compression_score,\n'
            'market_regime_direction_score,\n'
            'market_regime_stability_score,\n'
            'market_regime_regime_age,\n'
            'market_regime_regime_code'
        ),
        'network_type': 'stable_baselines3_ppo',
        'runner_id': 'market_regime_rl_v2',
        'score_metric': 'mean_reward',
        'score_label': 'Mean reward',
        'task_label': 'Balanced OHLCV + Market Regime PPO policy',
        'train_action_label': 'Train',
        'test_action_label': 'Test',
        'test_source_options': [
            {'id': 'latest_train', 'label': 'Test latest train'},
            {'id': 'best_model', 'label': 'Test best model'},
        ],
        'feature_set': [
            'ohlcv',
            'market_regime_trend_score',
            'market_regime_volatility_score',
            'market_regime_compression_score',
            'market_regime_direction_score',
            'market_regime_stability_score',
            'market_regime_regime_age',
            'market_regime_regime_code',
        ],
        'snapshot_cards': [
            {
                'id': 'best_validation_reward',
                'label': 'Best validation reward',
                'source': 'best_model',
                'metric_path': 'validation.mean_reward',
                'format': 'score',
                'hint': 'Best promoted v2 model measured on validation episodes.',
            },
            {
                'id': 'latest_validation_reward',
                'label': 'Latest validation reward',
                'source': 'latest_train',
                'metric_path': 'validation.mean_reward',
                'format': 'score',
                'hint': 'Latest completed v2 training run validation result.',
            },
            {
                'id': 'latest_test_reward',
                'label': 'Latest test reward',
                'source': 'latest_test',
                'metric_path': 'mean_reward',
                'format': 'score',
                'hint': 'Latest chronological holdout evaluation.',
            },
            {
                'id': 'latest_profit_factor',
                'label': 'Latest profit factor',
                'source': 'latest_test',
                'metric_path': 'profit_factor',
                'format': 'score',
                'hint': 'Most recent test profitability after the v2 reward shaping.',
            },
        ],
        'metric_sections': [
            {
                'id': 'validation_metrics',
                'label': 'Validation metrics',
                'source': 'latest_train',
                'metric_root': 'validation',
                'metrics': [
                    {'key': 'mean_reward', 'label': 'Mean reward', 'format': 'score'},
                    {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                    {'key': 'win_rate', 'label': 'Win rate', 'format': 'percent'},
                    {'key': 'profit_factor', 'label': 'Profit factor', 'format': 'score'},
                    {'key': 'long_rate', 'label': 'Long rate', 'format': 'percent'},
                    {'key': 'short_rate', 'label': 'Short rate', 'format': 'percent'},
                    {'key': 'flat_rate', 'label': 'Flat rate', 'format': 'percent'},
                    {'key': 'max_drawdown', 'label': 'Max drawdown', 'format': 'score'},
                ],
            },
            {
                'id': 'test_metrics',
                'label': 'Test metrics',
                'source': 'latest_test',
                'metrics': [
                    {'key': 'mean_reward', 'label': 'Mean reward', 'format': 'score'},
                    {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                    {'key': 'win_rate', 'label': 'Win rate', 'format': 'percent'},
                    {'key': 'profit_factor', 'label': 'Profit factor', 'format': 'score'},
                    {'key': 'long_rate', 'label': 'Long rate', 'format': 'percent'},
                    {'key': 'short_rate', 'label': 'Short rate', 'format': 'percent'},
                    {'key': 'flat_rate', 'label': 'Flat rate', 'format': 'percent'},
                    {'key': 'max_drawdown', 'label': 'Max drawdown', 'format': 'score'},
                ],
            },
        ],
        'defaults': {
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 10000,
            'algorithm': 'PPO',
            'validationSplit': 0.15,
            'testSplit': 0.15,
            'observationWindow': 16,
            'totalTimesteps': 100000,
            'learningRate': 0.0003,
            'gamma': 0.99,
            'transactionCost': 0.0001,
            'rewardScale': 1.0,
            'positionSize': 1.0,
            'allowShort': True,
            'normalizeVolume': True,
            'testEpisodes': 3,
            'holdingCost': 0.00002,
            'flatReward': 0.000003,
            'imbalancePenalty': 0.00003,
            'sameSideStreakPenalty': 0.00001,
            'marketRegimeEmaFastPeriod': 9,
            'marketRegimeEmaSlowPeriod': 21,
            'marketRegimeAdxPeriod': 14,
            'marketRegimeAtrPeriod': 14,
            'marketRegimeBollingerPeriod': 20,
            'marketRegimeBollingerStdDev': 2.0,
            'marketRegimeDonchianPeriod': 20,
            'marketRegimeChoppinessPeriod': 14,
            'marketRegimeSupertrendAtrPeriod': 10,
            'marketRegimeSupertrendMultiplier': 3.0,
            'marketRegimeVwapSource': 'hlc3',
            'marketRegimeScoreSmoothingPeriod': 5,
            'marketRegimeConfirmBars': 3,
        },
        'parameter_schema': [
            {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
            {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
            {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 500, 'group': 'dataset'},
            {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
            {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
            {'key': 'algorithm', 'label': 'Algorithm', 'type': 'string', 'group': 'training', 'options': [{'value': 'PPO', 'label': 'PPO'}]},
            {'key': 'totalTimesteps', 'label': 'Total timesteps', 'type': 'number', 'min': 1000, 'group': 'training'},
            {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
            {'key': 'gamma', 'label': 'Gamma', 'type': 'number', 'min': 0.5, 'max': 0.9999, 'step': 'any', 'group': 'training'},
            {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 1, 'group': 'architecture'},
            {'key': 'transactionCost', 'label': 'Transaction cost', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'training'},
            {'key': 'holdingCost', 'label': 'Holding cost', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'training'},
            {'key': 'flatReward', 'label': 'Flat reward', 'type': 'number', 'step': 'any', 'group': 'training'},
            {'key': 'imbalancePenalty', 'label': 'Imbalance penalty', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'training'},
            {'key': 'sameSideStreakPenalty', 'label': 'Same-side streak penalty', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'training'},
            {'key': 'rewardScale', 'label': 'Reward scale', 'type': 'number', 'min': 0.000001, 'step': 'any', 'group': 'training'},
            {'key': 'positionSize', 'label': 'Position size', 'type': 'number', 'min': 0.000001, 'step': 'any', 'group': 'training'},
            {'key': 'allowShort', 'label': 'Allow short', 'type': 'boolean', 'group': 'training'},
            {'key': 'normalizeVolume', 'label': 'Normalize volume', 'type': 'boolean', 'group': 'normalization'},
            {'key': 'testEpisodes', 'label': 'Test episodes', 'type': 'number', 'min': 1, 'group': 'training'},
            {'key': 'marketRegimeEmaFastPeriod', 'label': 'EMA fast', 'type': 'number', 'min': 1, 'group': 'special'},
            {'key': 'marketRegimeEmaSlowPeriod', 'label': 'EMA slow', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeAdxPeriod', 'label': 'ADX period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeAtrPeriod', 'label': 'ATR period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeBollingerPeriod', 'label': 'Bollinger period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeBollingerStdDev', 'label': 'Bollinger std dev', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
            {'key': 'marketRegimeDonchianPeriod', 'label': 'Donchian period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeChoppinessPeriod', 'label': 'Choppiness period', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeSupertrendAtrPeriod', 'label': 'Supertrend ATR', 'type': 'number', 'min': 2, 'group': 'special'},
            {'key': 'marketRegimeSupertrendMultiplier', 'label': 'Supertrend multiplier', 'type': 'number', 'min': 0.1, 'step': 'any', 'group': 'special'},
            {'key': 'marketRegimeVwapSource', 'label': 'VWAP source', 'type': 'string', 'group': 'special', 'options': [{'value': 'hlc3', 'label': 'hlc3'}, {'value': 'ohlc4', 'label': 'ohlc4'}, {'value': 'close', 'label': 'close'}]},
            {'key': 'marketRegimeScoreSmoothingPeriod', 'label': 'Score smoothing', 'type': 'number', 'min': 1, 'group': 'special'},
            {'key': 'marketRegimeConfirmBars', 'label': 'Regime confirm bars', 'type': 'number', 'min': 1, 'group': 'special'},
        ],
        'parameter_groups': [
            {'id': 'dataset', 'label': 'Dataset'},
            {'id': 'architecture', 'label': 'Architecture'},
            {'id': 'training', 'label': 'Training'},
            {'id': 'normalization', 'label': 'Normalization'},
            {'id': 'special', 'label': 'Market Regime'},
        ],
    },
    'temporal_cnn_indicator_fusion_v1': {
        'id': 'temporal_cnn_indicator_fusion_v1',
        'label': 'Temporal CNN Indicator Fusion',
        'family': 'supervised_learning',
        'architecture_type': 'convolutional',
        'description': (
            'Temporal CNN over price-action and indicator-fusion features, combining candle geometry, '
            'volatility, momentum and trend-state signals instead of raw price plus custom envelopes.'
        ),
        'signature': (
            'Builds each temporal sample from engineered forex features rather than absolute price levels. '
            'For every candle it derives short-horizon returns, candle body and wick ratios, rolling volume '
            'z-score, EMA distance and EMA gap ratios, ATR ratio, RSI, ADX with DI spread, MACD line/signal/'
            'histogram ratios, Bollinger width and band position, Stochastic K/D, and ROC. The model then '
            'consumes a rolling observation window of those stationary features and applies a 1D temporal '
            'convolution before the dense prediction head.\n\n'
            'Target formulas for this signature:\n'
            'future_upside_ratio = (max(high[t+1:t+N]) - close[t]) / close[t]\n'
            'future_downside_ratio = (close[t] - min(low[t+1:t+N])) / close[t]\n'
            'upside_std = rolling_std(future_upside_ratio, std_window)\n'
            'downside_std = rolling_std(future_downside_ratio, std_window)\n'
            'target_signal = 1,  if future_upside_ratio >= future_downside_ratio + std_threshold * downside_std\n'
            'target_signal = -1, if future_downside_ratio >= future_upside_ratio + std_threshold * upside_std\n'
            'target_signal = 0,  otherwise\n'
            'Rows without a valid future window, without a full rolling std window, or without a full '
            'observation window are discarded before training.'
        ),
        'network_type': 'numpy_temporal_cnn_regressor',
        'runner_id': 'temporal_cnn_indicator_fusion_v1',
        'score_metric': 'signal_directional_accuracy',
        'score_label': 'Signal accuracy',
        'task_label': 'Indicator-fusion CNN ternary regression',
        'train_action_label': 'Train',
        'test_action_label': 'Test',
        'test_source_options': [
            {'id': 'latest_train', 'label': 'Test latest train'},
            {'id': 'best_model', 'label': 'Test best model'},
        ],
        'feature_set': [
            'returns',
            'candle_geometry',
            'volume_zscore',
            'ema_gap',
            'atr_ratio',
            'rsi',
            'adx_di_spread',
            'macd',
            'bollinger_band_position',
            'stochastic',
            'roc',
        ],
        'normalization_targets': [
            {'id': 'ffx_return_1', 'label': 'Return 1'},
            {'id': 'ffx_return_3', 'label': 'Return 3'},
            {'id': 'ffx_return_8', 'label': 'Return 8'},
            {'id': 'ffx_range_ratio', 'label': 'Range ratio'},
            {'id': 'ffx_body_ratio', 'label': 'Body ratio'},
            {'id': 'ffx_upper_wick_ratio', 'label': 'Upper wick ratio'},
            {'id': 'ffx_lower_wick_ratio', 'label': 'Lower wick ratio'},
            {'id': 'ffx_volume_zscore_20', 'label': 'Volume z-score 20'},
            {'id': 'ffx_ema_gap_9_21_ratio', 'label': 'EMA gap 9/21'},
            {'id': 'ffx_close_to_ema_9_ratio', 'label': 'Close to EMA 9'},
            {'id': 'ffx_close_to_ema_21_ratio', 'label': 'Close to EMA 21'},
            {'id': 'ffx_atr_14_ratio', 'label': 'ATR 14 ratio'},
            {'id': 'ffx_rsi_7', 'label': 'RSI 7'},
            {'id': 'ffx_rsi_14', 'label': 'RSI 14'},
            {'id': 'ffx_adx_14', 'label': 'ADX 14'},
            {'id': 'ffx_di_spread_14', 'label': 'DI spread 14'},
            {'id': 'ffx_macd_line', 'label': 'MACD line'},
            {'id': 'ffx_macd_signal', 'label': 'MACD signal'},
            {'id': 'ffx_macd_histogram', 'label': 'MACD histogram'},
            {'id': 'ffx_bb_width_ratio', 'label': 'Bollinger width ratio'},
            {'id': 'ffx_bb_position', 'label': 'Bollinger position'},
            {'id': 'ffx_stoch_k', 'label': 'Stochastic K'},
            {'id': 'ffx_stoch_d', 'label': 'Stochastic D'},
            {'id': 'ffx_roc_10', 'label': 'ROC 10'},
        ],
        'snapshot_cards': [
            {
                'id': 'best_validation_accuracy',
                'label': 'Best validation accuracy',
                'source': 'best_model',
                'metric_path': 'validation.signal_directional_accuracy',
                'format': 'percent',
                'hint': 'Best promoted model measured on the validation split.',
            },
            {
                'id': 'latest_validation_accuracy',
                'label': 'Latest validation accuracy',
                'source': 'latest_train',
                'metric_path': 'validation.signal_directional_accuracy',
                'format': 'percent',
                'hint': 'Latest completed training run validation result.',
            },
            {
                'id': 'latest_test_accuracy',
                'label': 'Latest test accuracy',
                'source': 'latest_test',
                'metric_path': 'signal_directional_accuracy',
                'format': 'percent',
                'hint': 'Latest chronological holdout evaluation.',
            },
            {
                'id': 'latest_observation_window',
                'label': 'Observation window',
                'source': 'latest_train',
                'metric_path': 'observation_window',
                'format': 'integer',
                'hint': 'How many candles each temporal sample used.',
            },
        ],
        'metric_sections': [
            {
                'id': 'validation_metrics',
                'label': 'Validation metrics',
                'source': 'latest_train',
                'metric_root': 'validation',
                'metrics': [
                    {'key': 'signal_directional_accuracy', 'label': 'Signal accuracy', 'format': 'percent'},
                    {'key': 'signal_mae', 'label': 'Signal MAE', 'format': 'score'},
                    {'key': 'signal_rmse', 'label': 'Signal RMSE', 'format': 'score'},
                    {'key': 'mean_predicted_signal', 'label': 'Predicted signal', 'format': 'score'},
                    {'key': 'mean_actual_signal', 'label': 'Actual signal', 'format': 'score'},
                    {'key': 'long_bias_rate', 'label': 'Long bias rate', 'format': 'percent'},
                    {'key': 'short_bias_rate', 'label': 'Short bias rate', 'format': 'percent'},
                ],
            },
            {
                'id': 'test_metrics',
                'label': 'Test metrics',
                'source': 'latest_test',
                'metrics': [
                    {'key': 'signal_directional_accuracy', 'label': 'Signal accuracy', 'format': 'percent'},
                    {'key': 'signal_mae', 'label': 'Signal MAE', 'format': 'score'},
                    {'key': 'signal_rmse', 'label': 'Signal RMSE', 'format': 'score'},
                    {'key': 'mean_predicted_signal', 'label': 'Predicted signal', 'format': 'score'},
                    {'key': 'mean_actual_signal', 'label': 'Actual signal', 'format': 'score'},
                    {'key': 'long_bias_rate', 'label': 'Long bias rate', 'format': 'percent'},
                    {'key': 'short_bias_rate', 'label': 'Short bias rate', 'format': 'percent'},
                ],
            },
        ],
        'defaults': {
            'symbol': 'EURUSD',
            'timeframe': 'M1',
            'bars': 10000,
            'validationSplit': 0.15,
            'testSplit': 0.15,
            'observationWindow': 24,
            'convFilters': 32,
            'kernelSize': 5,
            'targetHorizon': 8,
            'targetStdWindow': 20,
            'targetStdThreshold': 1.2,
            'learningRate': 0.0005,
            'epochs': 220,
            'batchSize': 128,
            'hiddenLayers': [
                {'id': 'layer_1', 'size': 64, 'activation': 'relu', 'dropout': 0.05},
                {'id': 'layer_2', 'size': 32, 'activation': 'relu', 'dropout': 0.05},
            ],
            'normalizationColumns': [],
            'seed': 42,
        },
        'parameter_schema': [
            {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
            {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
            {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 200, 'max': 10000, 'group': 'dataset'},
            {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
            {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
            {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 4, 'group': 'architecture', 'description': 'How many past candles are packed into each temporal sample.'},
            {'key': 'convFilters', 'label': 'Conv filters', 'type': 'number', 'min': 4, 'group': 'architecture', 'description': 'How many temporal pattern detectors the convolution layer learns.'},
            {'key': 'kernelSize', 'label': 'Kernel size', 'type': 'number', 'min': 2, 'group': 'architecture', 'description': 'How many candles each convolution kernel sees at once.'},
            {'key': 'targetHorizon', 'label': 'Future candles', 'type': 'number', 'min': 1, 'group': 'special', 'description': 'How many next candles define the future long-vs-short excursion window.'},
            {'key': 'targetStdWindow', 'label': 'Std window', 'type': 'number', 'min': 2, 'group': 'special', 'description': 'Rolling window used to estimate upside and downside excursion standard deviation.'},
            {'key': 'targetStdThreshold', 'label': 'Std threshold', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special', 'description': 'How many rolling standard deviations are required to emit a long or short target.'},
            {'key': 'epochs', 'label': 'Epochs', 'type': 'number', 'min': 10, 'group': 'training'},
            {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
            {'key': 'batchSize', 'label': 'Batch size', 'type': 'number', 'min': 8, 'group': 'training'},
            {'key': 'seed', 'label': 'Seed', 'type': 'number', 'min': 1, 'group': 'training'},
        ],
        'parameter_groups': [
            {'id': 'dataset', 'label': 'Dataset'},
            {'id': 'architecture', 'label': 'Architecture'},
            {'id': 'training', 'label': 'Training'},
            {'id': 'normalization', 'label': 'Normalization'},
            {'id': 'special', 'label': 'Special parameters'},
        ],
    },
}


NETWORK_REGISTRY['neural_market_regime_cnn_v1'] = deepcopy(NETWORK_REGISTRY['temporal_cnn_indicator_fusion_v1'])
NETWORK_REGISTRY['neural_market_regime_cnn_v1'].update({
    'id': 'neural_market_regime_cnn_v1',
    'label': 'Neural Market Regime CNN',
    'description': (
        'Temporal CNN that learns an alternative market-regime state directly from future price structure, '
        'using indicator-fusion features plus regime-oriented context such as choppiness, Donchian width, '
        'Supertrend direction and VWAP distance.'
    ),
    'signature': (
        'Builds each temporal sample from stationary price-action and regime-context features. '
        'Inputs include short-horizon returns, candle geometry, rolling volume z-score, EMA distance and gap '
        'ratios, ATR ratio, RSI, ADX with DI spread, MACD line/signal/histogram ratios, Bollinger width and '
        'band position, Stochastic K/D, ROC, Choppiness and trendiness, Donchian width ratio, Supertrend '
        'direction and VWAP distance ratio. The model consumes a rolling observation window of those features '
        'and applies a 1D temporal convolution before the dense classification head.\n\n'
        'Future regime target formulas for this signature:\n'
        'future_upside_ratio = (max(high[t+1:t+N]) - close[t]) / close[t]\n'
        'future_downside_ratio = (close[t] - min(low[t+1:t+N])) / close[t]\n'
        'future_return_ratio = (close[t+N] - close[t]) / close[t]\n'
        'future_range_ratio = (max(high[t+1:t+N]) - min(low[t+1:t+N])) / close[t]\n'
        'atr_ratio = ATR_14[t] / close[t]\n'
        'future_volatility_multiple = future_range_ratio / atr_ratio\n'
        'directional_efficiency = abs(future_return_ratio) / future_range_ratio\n'
        'directional_move_multiple = abs(future_return_ratio) / atr_ratio\n'
        'directional_dominance = abs(future_upside_ratio - future_downside_ratio) / future_range_ratio\n\n'
        'target_regime = compression, if future_volatility_multiple <= compression_threshold\n'
        'target_regime = trend_up/down, if directional_efficiency, directional_move_multiple and directional_'
        'dominance all exceed their thresholds, with sign from future_return_ratio\n'
        'target_regime = volatile_up/down, if the path is not compression or trend and future_volatility_multiple '
        'exceeds volatility_threshold, with direction from future_return_ratio or side dominance\n'
        'target_regime = range, otherwise\n\n'
        'Rows without a valid future window or without a full observation window are discarded before training.'
    ),
    'network_type': 'numpy_temporal_cnn_classifier',
    'runner_id': 'neural_market_regime_cnn_v1',
    'score_metric': 'macro_f1',
    'score_label': 'Macro F1',
    'task_label': 'Alternative market regime CNN classifier',
    'feature_set': [
        'returns',
        'candle_geometry',
        'volume_zscore',
        'ema_gap',
        'atr_ratio',
        'rsi',
        'adx_di_spread',
        'macd',
        'bollinger_band_position',
        'stochastic',
        'roc',
        'choppiness',
        'donchian_width',
        'supertrend_direction',
        'vwap_distance',
    ],
    'snapshot_cards': [
        {
            'id': 'best_validation_macro_f1',
            'label': 'Best validation macro F1',
            'source': 'best_model',
            'metric_path': 'validation.macro_f1',
            'format': 'percent',
            'hint': 'Best promoted model measured on the validation split with class-balanced F1.',
        },
        {
            'id': 'latest_validation_macro_f1',
            'label': 'Latest validation macro F1',
            'source': 'latest_train',
            'metric_path': 'validation.macro_f1',
            'format': 'percent',
            'hint': 'Latest completed training run validation result.',
        },
        {
            'id': 'latest_test_macro_f1',
            'label': 'Latest test macro F1',
            'source': 'latest_test',
            'metric_path': 'macro_f1',
            'format': 'percent',
            'hint': 'Latest chronological holdout evaluation.',
        },
        {
            'id': 'latest_observation_window',
            'label': 'Observation window',
            'source': 'latest_train',
            'metric_path': 'observation_window',
            'format': 'integer',
            'hint': 'How many candles each temporal sample used.',
        },
    ],
    'metric_sections': [
        {
            'id': 'validation_metrics',
            'label': 'Validation metrics',
            'source': 'latest_train',
            'metric_root': 'validation',
            'metrics': [
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
                {'key': 'actual_transition_rate', 'label': 'Actual churn', 'format': 'percent'},
                {'key': 'predicted_transition_rate', 'label': 'Predicted churn', 'format': 'percent'},
                {'key': 'class_compression_recall', 'label': 'Compression recall', 'format': 'percent'},
                {'key': 'class_range_recall', 'label': 'Range recall', 'format': 'percent'},
                {'key': 'class_trend_up_recall', 'label': 'Trend up recall', 'format': 'percent'},
                {'key': 'class_trend_down_recall', 'label': 'Trend down recall', 'format': 'percent'},
                {'key': 'class_volatile_up_recall', 'label': 'Volatile up recall', 'format': 'percent'},
                {'key': 'class_volatile_down_recall', 'label': 'Volatile down recall', 'format': 'percent'},
            ],
        },
        {
            'id': 'test_metrics',
            'label': 'Test metrics',
            'source': 'latest_test',
            'metrics': [
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
                {'key': 'actual_transition_rate', 'label': 'Actual churn', 'format': 'percent'},
                {'key': 'predicted_transition_rate', 'label': 'Predicted churn', 'format': 'percent'},
                {'key': 'class_compression_recall', 'label': 'Compression recall', 'format': 'percent'},
                {'key': 'class_range_recall', 'label': 'Range recall', 'format': 'percent'},
                {'key': 'class_trend_up_recall', 'label': 'Trend up recall', 'format': 'percent'},
                {'key': 'class_trend_down_recall', 'label': 'Trend down recall', 'format': 'percent'},
                {'key': 'class_volatile_up_recall', 'label': 'Volatile up recall', 'format': 'percent'},
                {'key': 'class_volatile_down_recall', 'label': 'Volatile down recall', 'format': 'percent'},
            ],
        },
    ],
    'defaults': {
        'symbol': 'EURUSD',
        'timeframe': 'M15',
        'bars': 10000,
        'validationSplit': 0.15,
        'testSplit': 0.15,
        'observationWindow': 64,
        'convFilters': 48,
        'kernelSize': 5,
        'targetHorizon': 12,
        'targetRegimeCompressionThreshold': 0.9,
        'targetRegimeVolatilityThreshold': 2.2,
        'targetRegimeTrendEfficiencyThreshold': 0.55,
        'targetRegimeDirectionalMoveThreshold': 0.35,
        'targetRegimeDirectionalDominanceThreshold': 0.6,
        'learningRate': 0.0004,
        'epochs': 260,
        'batchSize': 128,
        'hiddenLayers': [
            {'id': 'layer_1', 'size': 96, 'activation': 'relu', 'dropout': 0.08},
            {'id': 'layer_2', 'size': 48, 'activation': 'relu', 'dropout': 0.08},
        ],
        'normalizationColumns': [],
        'seed': 42,
    },
    'parameter_schema': [
        {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
        {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
        {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 200, 'max': 10000, 'group': 'dataset'},
        {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 4, 'group': 'architecture', 'description': 'How many past candles are packed into each temporal sample.'},
        {'key': 'convFilters', 'label': 'Conv filters', 'type': 'number', 'min': 4, 'group': 'architecture', 'description': 'How many temporal pattern detectors the convolution layer learns.'},
        {'key': 'kernelSize', 'label': 'Kernel size', 'type': 'number', 'min': 2, 'group': 'architecture', 'description': 'How many candles each convolution kernel sees at once.'},
        {'key': 'targetHorizon', 'label': 'Future candles', 'type': 'number', 'min': 1, 'group': 'special', 'description': 'How many future candles define the alternative regime target window.'},
        {'key': 'targetRegimeCompressionThreshold', 'label': 'Compression threshold', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special', 'description': 'Maximum future range measured in ATR multiples still considered compression.'},
        {'key': 'targetRegimeVolatilityThreshold', 'label': 'Volatility threshold', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special', 'description': 'Minimum future range measured in ATR multiples required for a volatile regime.'},
        {'key': 'targetRegimeTrendEfficiencyThreshold', 'label': 'Trend efficiency', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special', 'description': 'Minimum net-move efficiency required before the future path can count as trend.'},
        {'key': 'targetRegimeDirectionalMoveThreshold', 'label': 'Directional move', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special', 'description': 'Minimum net move measured in ATR multiples required for a trend regime.'},
        {'key': 'targetRegimeDirectionalDominanceThreshold', 'label': 'Directional dominance', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special', 'description': 'How much one side of the future excursion must dominate the other before trend wins over range.'},
        {'key': 'epochs', 'label': 'Epochs', 'type': 'number', 'min': 10, 'group': 'training'},
        {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
        {'key': 'batchSize', 'label': 'Batch size', 'type': 'number', 'min': 8, 'group': 'training'},
        {'key': 'seed', 'label': 'Seed', 'type': 'number', 'min': 1, 'group': 'training'},
    ],
})
NETWORK_REGISTRY['neural_market_regime_cnn_v1']['normalization_targets'] = (
    list(NETWORK_REGISTRY['neural_market_regime_cnn_v1']['normalization_targets'])
    + [
        {'id': 'nmr_choppiness_14', 'label': 'Choppiness 14'},
        {'id': 'nmr_trendiness_14', 'label': 'Trendiness 14'},
        {'id': 'nmr_donchian_width_20_ratio', 'label': 'Donchian width 20'},
        {'id': 'nmr_supertrend_direction', 'label': 'Supertrend direction'},
        {'id': 'nmr_vwap_distance_ratio', 'label': 'VWAP distance ratio'},
    ]
)


NETWORK_REGISTRY['candle_reversal_cnn_v1'] = deepcopy(NETWORK_REGISTRY['neural_market_regime_cnn_v1'])
NETWORK_REGISTRY['candle_reversal_cnn_v1'].update({
    'id': 'candle_reversal_cnn_v1',
    'label': 'Candle Reversal CNN',
    'description': (
        'Temporal CNN focused on reversal structures in candle sequences. '
        'It learns from candle geometry and short-term price structure rather than from textbook pattern labels.'
    ),
    'signature': (
        'Each sample is built from a rolling candle window using geometry-first features rather than chart images. '
        'Inputs include short returns, candle range, signed body ratio, upper/lower wick ratios, close position '
        'inside the candle, gap ratio, ATR-normalized true range, body-to-range ratio, and wick imbalance ratio. '
        'The model consumes a rolling observation window and applies a 1D temporal convolution before the dense '
        'classification head.\n\n'
        'Target formulas:\n'
        'atr = ATR_14[t]\n'
        'prev_move_atr = (close[t] - close[t-P]) / atr\n'
        'future_upside_atr = (max(high[t+1:t+H]) - close[t]) / atr\n'
        'future_downside_atr = (close[t] - min(low[t+1:t+H])) / atr\n\n'
        'target = bullish_reversal, if prev_move_atr <= -pretrend_threshold and '
        'future_upside_atr >= reversal_threshold and '
        'future_upside_atr >= dominance_ratio * future_downside_atr\n'
        'target = bearish_reversal, if prev_move_atr >= pretrend_threshold and '
        'future_downside_atr >= reversal_threshold and '
        'future_downside_atr >= dominance_ratio * future_upside_atr\n'
        'target = no_reversal, otherwise\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v1',
    'score_metric': 'macro_f1',
    'score_label': 'Macro F1',
    'task_label': 'Candle-sequence reversal classifier',
    'feature_set': [
        'returns',
        'candle_geometry',
        'gap_structure',
        'atr_normalized_range',
        'wick_imbalance',
    ],
    'snapshot_cards': [
        {
            'id': 'best_validation_macro_f1',
            'label': 'Best validation macro F1',
            'source': 'best_model',
            'metric_path': 'validation.macro_f1',
            'format': 'percent',
            'hint': 'Best promoted model measured on the validation split with class-balanced F1.',
        },
        {
            'id': 'latest_validation_macro_f1',
            'label': 'Latest validation macro F1',
            'source': 'latest_train',
            'metric_path': 'validation.macro_f1',
            'format': 'percent',
            'hint': 'Latest completed training run validation result.',
        },
        {
            'id': 'latest_test_macro_f1',
            'label': 'Latest test macro F1',
            'source': 'latest_test',
            'metric_path': 'macro_f1',
            'format': 'percent',
            'hint': 'Latest chronological holdout evaluation.',
        },
        {
            'id': 'latest_observation_window',
            'label': 'Observation window',
            'source': 'latest_train',
            'metric_path': 'observation_window',
            'format': 'integer',
            'hint': 'How many candles each temporal sample used.',
        },
    ],
    'metric_sections': [
        {
            'id': 'validation_metrics',
            'label': 'Validation metrics',
            'source': 'latest_train',
            'metric_root': 'validation',
            'metrics': [
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
                {'key': 'class_bullish_reversal_precision', 'label': 'Bullish precision', 'format': 'percent'},
                {'key': 'class_bullish_reversal_recall', 'label': 'Bullish recall', 'format': 'percent'},
                {'key': 'class_bearish_reversal_precision', 'label': 'Bearish precision', 'format': 'percent'},
                {'key': 'class_bearish_reversal_recall', 'label': 'Bearish recall', 'format': 'percent'},
                {'key': 'class_no_reversal_recall', 'label': 'No reversal recall', 'format': 'percent'},
            ],
        },
        {
            'id': 'test_metrics',
            'label': 'Test metrics',
            'source': 'latest_test',
            'metrics': [
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
                {'key': 'class_bullish_reversal_precision', 'label': 'Bullish precision', 'format': 'percent'},
                {'key': 'class_bullish_reversal_recall', 'label': 'Bullish recall', 'format': 'percent'},
                {'key': 'class_bearish_reversal_precision', 'label': 'Bearish precision', 'format': 'percent'},
                {'key': 'class_bearish_reversal_recall', 'label': 'Bearish recall', 'format': 'percent'},
                {'key': 'class_no_reversal_recall', 'label': 'No reversal recall', 'format': 'percent'},
            ],
        },
    ],
    'defaults': {
        'symbol': 'EURUSD',
        'timeframe': 'M15',
        'bars': 10000,
        'validationSplit': 0.15,
        'testSplit': 0.15,
        'observationWindow': 16,
        'convFilters': 48,
        'kernelSize': 5,
        'targetHorizon': 6,
        'pretrendLookback': 6,
        'pretrendThreshold': 1.2,
        'reversalThreshold': 1.0,
        'dominanceRatio': 1.35,
        'learningRate': 0.0004,
        'epochs': 220,
        'batchSize': 128,
        'hiddenLayers': [
            {'id': 'layer_1', 'size': 64, 'activation': 'relu', 'dropout': 0.05},
            {'id': 'layer_2', 'size': 32, 'activation': 'relu', 'dropout': 0.05},
        ],
        'normalizationColumns': [],
        'seed': 42,
    },
    'parameter_schema': [
        {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
        {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
        {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 200, 'max': 10000, 'group': 'dataset'},
        {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 4, 'group': 'architecture', 'description': 'How many past candles are packed into each temporal sample.'},
        {'key': 'convFilters', 'label': 'Conv filters', 'type': 'number', 'min': 4, 'group': 'architecture', 'description': 'How many temporal pattern detectors the convolution layer learns.'},
        {'key': 'kernelSize', 'label': 'Kernel size', 'type': 'number', 'min': 2, 'group': 'architecture', 'description': 'How many candles each convolution kernel sees at once.'},
        {'key': 'targetHorizon', 'label': 'Future candles', 'type': 'number', 'min': 2, 'group': 'special', 'description': 'How many future candles define the reversal outcome window.'},
        {'key': 'pretrendLookback', 'label': 'Pretrend lookback', 'type': 'number', 'min': 2, 'group': 'special', 'description': 'How many candles define the move that must exist before a reversal can be counted.'},
        {'key': 'pretrendThreshold', 'label': 'Pretrend threshold ATR', 'type': 'number', 'min': 0.1, 'step': 'any', 'group': 'special', 'description': 'Minimum prior move in ATR units required before a reversal label is allowed.'},
        {'key': 'reversalThreshold', 'label': 'Reversal threshold ATR', 'type': 'number', 'min': 0.1, 'step': 'any', 'group': 'special', 'description': 'Minimum future move in ATR units required for a reversal label.'},
        {'key': 'dominanceRatio', 'label': 'Dominance ratio', 'type': 'number', 'min': 1.0, 'step': 'any', 'group': 'special', 'description': 'How much the winning future excursion must dominate the opposing excursion.'},
        {'key': 'epochs', 'label': 'Epochs', 'type': 'number', 'min': 10, 'group': 'training'},
        {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
        {'key': 'batchSize', 'label': 'Batch size', 'type': 'number', 'min': 8, 'group': 'training'},
        {'key': 'seed', 'label': 'Seed', 'type': 'number', 'min': 1, 'group': 'training'},
    ],
})
NETWORK_REGISTRY['candle_reversal_cnn_v1']['normalization_targets'] = [
    {'id': 'crx_return_1', 'label': 'Return 1'},
    {'id': 'crx_return_2', 'label': 'Return 2'},
    {'id': 'crx_return_3', 'label': 'Return 3'},
    {'id': 'crx_range_ratio', 'label': 'Range ratio'},
    {'id': 'crx_signed_body_ratio', 'label': 'Signed body ratio'},
    {'id': 'crx_upper_wick_ratio', 'label': 'Upper wick ratio'},
    {'id': 'crx_lower_wick_ratio', 'label': 'Lower wick ratio'},
    {'id': 'crx_close_location', 'label': 'Close location'},
    {'id': 'crx_gap_ratio', 'label': 'Gap ratio'},
    {'id': 'crx_true_range_atr_ratio', 'label': 'True range ATR ratio'},
    {'id': 'crx_body_to_range_ratio', 'label': 'Body to range ratio'},
    {'id': 'crx_wick_imbalance_ratio', 'label': 'Wick imbalance ratio'},
]


NETWORK_REGISTRY['candle_reversal_cnn_v2'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v1'])
NETWORK_REGISTRY['candle_reversal_cnn_v2'].update({
    'id': 'candle_reversal_cnn_v2',
    'label': 'Candle Reversal CNN v2',
    'description': (
        'Class-balanced follow-up to the candle reversal CNN. '
        'It keeps the same candle-geometry target but uses train-side balancing to reduce collapse into always predicting no_reversal.'
    ),
    'runner_id': 'candle_reversal_cnn_v2',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v1']['defaults'],
        'classWeightMode': 'inverse_frequency',
        'classWeightExponent': 0.75,
        'neutralRetention': 0.35,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v2']['parameter_schema'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v1']['parameter_schema'])
    + [
        {
            'key': 'classWeightMode',
            'label': 'Class weighting',
            'type': 'string',
            'group': 'training',
            'options': [
                {'value': 'none', 'label': 'None'},
                {'value': 'inverse_frequency', 'label': 'Inverse frequency'},
            ],
            'description': 'Applies class-balanced weighting to the classification loss during training.',
        },
        {
            'key': 'classWeightExponent',
            'label': 'Weight exponent',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'training',
            'description': 'Controls how aggressively inverse-frequency weighting amplifies minority classes.',
        },
        {
            'key': 'neutralRetention',
            'label': 'No-reversal retention',
            'type': 'number',
            'min': 0.05,
            'max': 1.0,
            'step': 'any',
            'group': 'training',
            'description': 'Keeps only this fraction of no_reversal samples in the train split before fitting the model.',
        },
    ]
)
NETWORK_REGISTRY['candle_reversal_cnn_v2']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v3'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v2'])
NETWORK_REGISTRY['candle_reversal_cnn_v3'].update({
    'id': 'candle_reversal_cnn_v3',
    'label': 'Candle Reversal CNN v3',
    'description': (
        'Hierarchical follow-up to the candle reversal CNN. '
        'It first learns whether a reversal exists at all, then uses a second classifier to choose bearish versus bullish direction.'
    ),
    'signature': (
        'This hierarchical temporal CNN keeps the same candle-geometry feature signature as the earlier reversal models, '
        'but splits the prediction into two stages. Stage 1 learns reversal versus no_reversal. '
        'Stage 2 learns bearish versus bullish direction using only reversal rows from the train split. '
        'The final three-class prediction is reconstructed by gating the directional head with the reversal probability, '
        'and the reversal decision threshold is selected on the validation split to maximize macro F1.\n\n'
        'Stage 1 target:\n'
        'reversal = 1, if target_reversal_code != 0\n'
        'reversal = 0, otherwise\n\n'
        'Stage 2 target:\n'
        'direction = bearish_reversal, if target_reversal_code = -1\n'
        'direction = bullish_reversal, if target_reversal_code = 1\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v3',
    'task_label': 'Hierarchical candle-sequence reversal classifier',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v2']['defaults'],
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v3']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v2']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v3']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v4'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v3'])
NETWORK_REGISTRY['candle_reversal_cnn_v4'].update({
    'id': 'candle_reversal_cnn_v4',
    'label': 'Candle Reversal CNN v4',
    'description': (
        'Filtered hierarchical follow-up to the candle reversal CNN. '
        'It keeps the two-stage architecture, but teaches the stage-1 gate with cleaner neutral examples to reduce label ambiguity.'
    ),
    'signature': (
        'This filtered hierarchical temporal CNN keeps the same candle-geometry feature signature and two-stage structure as v3, '
        'but it changes the stage-1 training set. Stage 1 still learns reversal versus no_reversal, yet neutral rows are filtered so the gate is trained '
        'mostly against cleaner non-reversal examples instead of the full ambiguous no_reversal bucket. '
        'Stage 2 still learns bearish versus bullish direction using only reversal rows from the train split. '
        'The final three-class prediction is reconstructed by gating the directional head with the reversal probability, '
        'and the reversal decision threshold is selected on the validation split to maximize macro F1.\n\n'
        'Stage 1 clean-neutral rule:\n'
        'abs(prev_move_atr) <= pretrend_threshold * stage1_neutral_pretrend_ceiling\n'
        'and max(future_upside_atr, future_downside_atr) <= reversal_threshold * stage1_neutral_excursion_ceiling\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v4',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v3']['defaults'],
        'neutralRetention': 1.0,
        'stage1NeutralPretrendCeiling': 0.85,
        'stage1NeutralExcursionCeiling': 0.85,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v4']['parameter_schema'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v3']['parameter_schema'])
    + [
        {
            'key': 'stage1NeutralPretrendCeiling',
            'label': 'Gate neutral pretrend ceiling',
            'type': 'number',
            'min': 0.0,
            'max': 1.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage-1 gate keeps neutral training rows only when prior move size stays below this fraction of the pretrend threshold.',
        },
        {
            'key': 'stage1NeutralExcursionCeiling',
            'label': 'Gate neutral excursion ceiling',
            'type': 'number',
            'min': 0.0,
            'max': 1.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage-1 gate keeps neutral training rows only when future excursion stays below this fraction of the reversal threshold.',
        },
    ]
)
NETWORK_REGISTRY['candle_reversal_cnn_v4']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v5'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v4'])
NETWORK_REGISTRY['candle_reversal_cnn_v5'].update({
    'id': 'candle_reversal_cnn_v5',
    'label': 'Candle Reversal CNN v5',
    'description': (
        'Margin-filtered hierarchical follow-up to the candle reversal CNN. '
        'It trains the stage-1 gate only on strong reversal positives and strong neutral negatives.'
    ),
    'signature': (
        'This margin-filtered hierarchical temporal CNN keeps the same candle-geometry feature signature and two-stage structure as v3 and v4, '
        'but it changes both sides of the stage-1 training set. Stage 1 still learns reversal versus no_reversal, yet it only sees '
        'strong reversal positives and clean neutral negatives so the gate is not trained on the ambiguous middle bucket. '
        'Stage 2 still learns bearish versus bullish direction using all reversal rows from the train split. '
        'The final three-class prediction is reconstructed by gating the directional head with the reversal probability, '
        'and the reversal decision threshold is selected on the validation split to maximize macro F1.\n\n'
        'Stage 1 clean-positive rule:\n'
        'abs(prev_move_atr) >= pretrend_threshold * stage1_positive_pretrend_floor\n'
        'and max(future_upside_atr, future_downside_atr) >= reversal_threshold * stage1_positive_excursion_floor\n\n'
        'Stage 1 clean-neutral rule:\n'
        'abs(prev_move_atr) <= pretrend_threshold * stage1_neutral_pretrend_ceiling\n'
        'and max(future_upside_atr, future_downside_atr) <= reversal_threshold * stage1_neutral_excursion_ceiling\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v5',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v4']['defaults'],
        'stage1PositivePretrendFloor': 1.05,
        'stage1PositiveExcursionFloor': 1.1,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v5']['parameter_schema'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v4']['parameter_schema'])
    + [
        {
            'key': 'stage1PositivePretrendFloor',
            'label': 'Gate positive pretrend floor',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage-1 gate keeps reversal positives only when prior move size exceeds this multiple of the pretrend threshold.',
        },
        {
            'key': 'stage1PositiveExcursionFloor',
            'label': 'Gate positive excursion floor',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage-1 gate keeps reversal positives only when future excursion exceeds this multiple of the reversal threshold.',
        },
    ]
)
NETWORK_REGISTRY['candle_reversal_cnn_v5']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v6'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v3'])
NETWORK_REGISTRY['candle_reversal_cnn_v6'].update({
    'id': 'candle_reversal_cnn_v6',
    'label': 'Candle Reversal CNN v6',
    'description': (
        'Context-aware hierarchical follow-up to the candle reversal CNN. '
        'It keeps the v3 two-stage structure but augments candle geometry with local trend, volatility and location context.'
    ),
    'signature': (
        'This context-aware hierarchical temporal CNN keeps the same two-stage reversal architecture as v3, '
        'yet expands the feature signature beyond raw candle geometry. Stage 1 still learns reversal versus no_reversal, '
        'and stage 2 still learns bearish versus bullish direction, but both stages now see local trend and volatility context '
        'such as EMA gap, close distance to EMA, ATR ratio, ADX, DI spread, Bollinger width and Bollinger position. '
        'The final three-class prediction is reconstructed by gating the directional head with the reversal probability, '
        'and the reversal decision threshold is selected on the validation split to maximize macro F1.\n\n'
        'Context additions:\n'
        'return_6, volume_zscore_20,\n'
        'ema_gap_9_21_ratio, close_to_ema_9_ratio, close_to_ema_21_ratio,\n'
        'atr_14_ratio, adx_14, di_spread_14,\n'
        'bb_width_ratio, bb_position\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v6',
    'feature_set': [
        'returns',
        'candle_geometry',
        'gap_structure',
        'atr_normalized_range',
        'wick_imbalance',
        'trend_context',
        'volatility_context',
        'location_context',
    ],
})
NETWORK_REGISTRY['candle_reversal_cnn_v6']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v3']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v6']['normalization_targets'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v1']['normalization_targets'])
    + [
        {'id': 'crx_return_6', 'label': 'Return 6'},
        {'id': 'crx_volume_zscore_20', 'label': 'Volume z-score 20'},
        {'id': 'crx_ema_gap_9_21_ratio', 'label': 'EMA gap 9-21 ratio'},
        {'id': 'crx_close_to_ema_9_ratio', 'label': 'Close to EMA 9 ratio'},
        {'id': 'crx_close_to_ema_21_ratio', 'label': 'Close to EMA 21 ratio'},
        {'id': 'crx_atr_14_ratio', 'label': 'ATR 14 ratio'},
        {'id': 'crx_adx_14', 'label': 'ADX 14'},
        {'id': 'crx_di_spread_14', 'label': 'DI spread 14'},
        {'id': 'crx_bb_width_ratio', 'label': 'Bollinger width ratio'},
        {'id': 'crx_bb_position', 'label': 'Bollinger position'},
    ]
)

NETWORK_REGISTRY['candle_reversal_cnn_v7'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v6'])
NETWORK_REGISTRY['candle_reversal_cnn_v7'].update({
    'id': 'candle_reversal_cnn_v7',
    'label': 'Candle Reversal CNN v7',
    'description': (
        'Clean-target context-aware follow-up to the candle reversal CNN. '
        'It keeps the v6 context inputs and hierarchical structure, but drops ambiguous middle-bucket rows from the training target itself.'
    ),
    'signature': (
        'This clean-target hierarchical temporal CNN keeps the same context-aware feature signature as v6 and the same two-stage architecture as v3, '
        'but changes the target set. Reversal rows are kept only when they still satisfy the positive floors, '
        'and no_reversal rows are kept only when they remain clean neutral examples under the neutral ceilings. '
        'Ambiguous middle rows are excluded from the supervised dataset before the sequence windows are built.\n\n'
        'Target-clean positive rule:\n'
        'abs(prev_move_atr) >= pretrend_threshold * target_clean_positive_pretrend_floor\n'
        'and max(future_upside_atr, future_downside_atr) >= reversal_threshold * target_clean_positive_excursion_floor\n\n'
        'Target-clean neutral rule:\n'
        'abs(prev_move_atr) <= pretrend_threshold * target_clean_neutral_pretrend_ceiling\n'
        'and max(future_upside_atr, future_downside_atr) <= reversal_threshold * target_clean_neutral_excursion_ceiling\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v7',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v6']['defaults'],
        'targetCleanNeutralPretrendCeiling': 0.9,
        'targetCleanNeutralExcursionCeiling': 0.9,
        'targetCleanPositivePretrendFloor': 1.0,
        'targetCleanPositiveExcursionFloor': 1.0,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v7']['parameter_schema'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v6']['parameter_schema'])
    + [
        {
            'key': 'targetCleanNeutralPretrendCeiling',
            'label': 'Target neutral pretrend ceiling',
            'type': 'number',
            'min': 0.0,
            'max': 1.0,
            'step': 'any',
            'group': 'special',
            'description': 'Keeps no_reversal rows only when prior move size stays below this fraction of the pretrend threshold.',
        },
        {
            'key': 'targetCleanNeutralExcursionCeiling',
            'label': 'Target neutral excursion ceiling',
            'type': 'number',
            'min': 0.0,
            'max': 1.0,
            'step': 'any',
            'group': 'special',
            'description': 'Keeps no_reversal rows only when future excursion stays below this fraction of the reversal threshold.',
        },
        {
            'key': 'targetCleanPositivePretrendFloor',
            'label': 'Target positive pretrend floor',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'special',
            'description': 'Keeps reversal rows only when prior move size exceeds this multiple of the pretrend threshold.',
        },
        {
            'key': 'targetCleanPositiveExcursionFloor',
            'label': 'Target positive excursion floor',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'special',
            'description': 'Keeps reversal rows only when future excursion exceeds this multiple of the reversal threshold.',
        },
    ]
)
NETWORK_REGISTRY['candle_reversal_cnn_v7']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v6']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v7_1'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v7'])
NETWORK_REGISTRY['candle_reversal_cnn_v7_1'].update({
    'id': 'candle_reversal_cnn_v7_1',
    'label': 'Candle Reversal CNN v7.1',
    'description': (
        'Balanced clean-target follow-up to the candle reversal CNN. '
        'It keeps the v7 context-aware hierarchical target cleanup, but relaxes neutral preservation so the model does not collapse into almost-all-reversal predictions.'
    ),
    'signature': (
        'This balanced clean-target hierarchical temporal CNN keeps the same context-aware feature signature as v7 and the same two-stage architecture as v3, '
        'but relaxes the clean-target intervention so neutral examples survive in larger numbers. '
        'Reversal rows are still kept only when they satisfy the positive floors, while no_reversal rows are kept under softer clean-neutral ceilings. '
        'The train-side gate also stops downsampling neutral rows, so the stage-1 detector sees the full surviving neutral set instead of an even more reversal-heavy subset.\n\n'
        'Target-clean positive rule:\n'
        'abs(prev_move_atr) >= pretrend_threshold * target_clean_positive_pretrend_floor\n'
        'and max(future_upside_atr, future_downside_atr) >= reversal_threshold * target_clean_positive_excursion_floor\n\n'
        'Target-clean neutral rule:\n'
        'abs(prev_move_atr) <= pretrend_threshold * target_clean_neutral_pretrend_ceiling\n'
        'and max(future_upside_atr, future_downside_atr) <= reversal_threshold * target_clean_neutral_excursion_ceiling\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v7_1',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v7']['defaults'],
        'neutralRetention': 1.0,
        'targetCleanNeutralPretrendCeiling': 1.0,
        'targetCleanNeutralExcursionCeiling': 1.0,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v8'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v7_1'])
NETWORK_REGISTRY['candle_reversal_cnn_v8'].update({
    'id': 'candle_reversal_cnn_v8',
    'label': 'Candle Reversal CNN v8',
    'description': (
        'Dominant-setup gate follow-up to the candle reversal CNN. '
        'It keeps the v7.1 clean-target context-aware flow, but stage 1 no longer learns generic reversal-vs-neutral; '
        'it learns only strong dominant reversal setups and treats weak reversal rows as gate negatives.'
    ),
    'signature': (
        'This dominant-setup hierarchical temporal CNN keeps the same clean-target context-aware dataset as v7.1 and the same two-stage reconstruction path as v3, '
        'but changes the stage-1 contract itself. Stage 1 is trained only on dominant reversal setups, not on every non-neutral reversal row. '
        'A row is stage-1 positive only when it is already a supervised reversal row and also satisfies stronger setup floors on prior move size, winning excursion size, '
        'and future directional dominance. Weak reversal rows remain part of the final three-class validation target, but they are treated as stage-1 negatives during gate training, '
        'so the model learns a more selective actionable setup detector instead of a broad “any reversal happened” gate.\n\n'
        'Stage-1 setup positive rule:\n'
        'abs(prev_move_atr) >= pretrend_threshold * stage1_setup_pretrend_floor\n'
        'and max(future_upside_atr, future_downside_atr) >= reversal_threshold * stage1_setup_excursion_floor\n'
        'and max(future_upside_atr, future_downside_atr) / min(future_upside_atr, future_downside_atr) >= stage1_setup_dominance_floor\n'
        'and max(future_upside_atr, future_downside_atr) - min(future_upside_atr, future_downside_atr) >= stage1_setup_margin_floor\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v8',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['defaults'],
        'stage1SetupPretrendFloor': 1.15,
        'stage1SetupExcursionFloor': 1.1,
        'stage1SetupDominanceFloor': 2.0,
        'stage1SetupMarginFloor': 0.0,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v8']['parameter_schema'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['parameter_schema'])
    + [
        {
            'key': 'stage1SetupPretrendFloor',
            'label': 'Stage 1 setup pretrend floor',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage 1 labels a reversal row as a positive setup only when prior move size exceeds this multiple of the pretrend threshold.',
        },
        {
            'key': 'stage1SetupExcursionFloor',
            'label': 'Stage 1 setup excursion floor',
            'type': 'number',
            'min': 0.0,
            'max': 2.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage 1 labels a reversal row as a positive setup only when the winning future excursion exceeds this multiple of the reversal threshold.',
        },
        {
            'key': 'stage1SetupDominanceFloor',
            'label': 'Stage 1 setup dominance floor',
            'type': 'number',
            'min': 1.0,
            'max': 5.0,
            'step': 'any',
            'group': 'special',
            'description': 'Stage 1 labels a reversal row as a positive setup only when the winning excursion dominates the losing excursion by at least this ratio.',
        },
        {
            'key': 'stage1SetupMarginFloor',
            'label': 'Stage 1 setup margin floor',
            'type': 'number',
            'min': 0.0,
            'max': 5.0,
            'step': 'any',
            'group': 'special',
            'description': 'Optional absolute ATR margin that the winning excursion must exceed over the losing excursion for stage-1 setup positives.',
        },
    ]
)
NETWORK_REGISTRY['candle_reversal_cnn_v8']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v9'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v7_1'])
NETWORK_REGISTRY['candle_reversal_cnn_v9'].update({
    'id': 'candle_reversal_cnn_v9',
    'label': 'Candle Reversal CNN v9',
    'description': (
        'Directional dual-head follow-up to the candle reversal CNN. '
        'It keeps the v7.1 clean-target context-aware dataset, but replaces the single gate with separate bearish-setup and bullish-setup heads.'
    ),
    'signature': (
        'This directional dual-head temporal CNN keeps the same clean-target context-aware dataset as v7.1, '
        'but no longer uses one generic reversal gate plus a directional classifier. Instead it trains two one-vs-rest heads over the same sequence inputs: '
        'a bearish setup head and a bullish setup head. Each head learns its own threshold on the validation split, and the final three-class prediction is reconstructed '
        'by comparing the two directional setup scores against their own thresholds.\n\n'
        'Final reconstruction rule:\n'
        'if bearish_score < bearish_threshold and bullish_score < bullish_threshold: no_reversal\n'
        'else choose the side with the larger active score\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v9',
})
NETWORK_REGISTRY['candle_reversal_cnn_v9']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v9']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v10'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v7_1'])
NETWORK_REGISTRY['candle_reversal_cnn_v10'].update({
    'id': 'candle_reversal_cnn_v10',
    'label': 'Candle Reversal CNN v10',
    'description': (
        'Tri-head follow-up to the candle reversal CNN. '
        'It keeps the v7.1 clean-target context-aware dataset, but adds an explicit neutral head alongside bearish and bullish setup heads.'
    ),
    'signature': (
        'This tri-head temporal CNN keeps the same clean-target context-aware dataset as v7.1, '
        'but no longer infers no_reversal as leftover from directional heads. Instead it trains three one-vs-rest heads over the same sequence inputs: '
        'a bearish setup head, a neutral setup head, and a bullish setup head. Each head learns its own validation threshold, and the final three-class prediction is reconstructed '
        'from threshold-adjusted head strengths.\n\n'
        'Final reconstruction rule:\n'
        'strength = score / threshold for each head\n'
        'if max(strengths) < 1.0: no_reversal\n'
        'else choose the class with the largest active strength\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v10',
})
NETWORK_REGISTRY['candle_reversal_cnn_v10']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v10']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v7_1']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v10_1'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v10'])
NETWORK_REGISTRY['candle_reversal_cnn_v10_1'].update({
    'id': 'candle_reversal_cnn_v10_1',
    'label': 'Candle Reversal CNN v10.1',
    'description': (
        'Directional-rest-rebalanced follow-up to the tri-head candle reversal CNN. '
        'It keeps the explicit neutral head from v10, but down-samples rest only for the bearish and bullish heads during training.'
    ),
    'signature': (
        'This tri-head temporal CNN keeps the same explicit bearish, neutral, and bullish setup heads as v10, '
        'but it changes only the train-time balance of the directional heads. The bearish and bullish one-vs-rest heads '
        'see a reduced fraction of rest rows during fitting, while the neutral head still sees the full neutral-vs-rest problem. '
        'Threshold search and final reconstruction stay identical to v10.\n\n'
        'Directional-head train rebalance:\n'
        'retain rest rows for bearish head by directional_head_rest_retention\n'
        'retain rest rows for bullish head by directional_head_rest_retention\n'
        'keep neutral head rows unchanged\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, or observation window are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v10_1',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v10']['defaults'],
        'directionalHeadRestRetention': 0.6,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['parameter_schema'] = (
    list(NETWORK_REGISTRY['candle_reversal_cnn_v10']['parameter_schema'])
    + [
        {
            'key': 'directionalHeadRestRetention',
            'label': 'Directional rest retention',
            'type': 'number',
            'min': 0.05,
            'max': 1.0,
            'step': 'any',
            'group': 'training',
            'description': 'Keeps only this fraction of rest rows for the bearish and bullish one-vs-rest heads during training.',
        },
    ]
)
NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v10']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_cnn_v11'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v10_1'])
NETWORK_REGISTRY['candle_reversal_cnn_v11'].update({
    'id': 'candle_reversal_cnn_v11',
    'label': 'Candle Reversal CNN v11',
    'description': (
        'Candlestick-pattern context follow-up to the tri-head candle reversal CNN. '
        'It keeps the v10.1 clean-target tri-head contract, but augments the context feature signature with classical candlestick pattern scores and exact flags.'
    ),
    'signature': (
        'This tri-head temporal CNN keeps the same explicit bearish, neutral, and bullish setup heads and the same clean-target context-aware dataset as v10.1, '
        'but it expands the feature signature with a frozen classical candlestick-pattern map. '
        'The sequence window still includes candle geometry, trend, volatility, and location context, and now also includes '
        'CandlestickPatterns(trendLookback=5, bodyAveragePeriod=14) score heads and exact pattern flags. '
        'Those added context features are descriptive inputs only; the supervised target remains future reversal behavior, not textbook pattern labels.\n\n'
        'Pattern-context additions:\n'
        'bullish_reversal_score, bearish_reversal_score,\n'
        'bullish_continuation_score, bearish_continuation_score,\n'
        'hammer, shooting_star,\n'
        'bullish_engulfing, bearish_engulfing,\n'
        'bullish_harami, bearish_harami,\n'
        'morning_star, evening_star,\n'
        'rising_three_methods, falling_three_methods\n\n'
        'Directional-head train rebalance stays identical to v10.1:\n'
        'retain rest rows for bearish head by directional_head_rest_retention\n'
        'retain rest rows for bullish head by directional_head_rest_retention\n'
        'keep neutral head rows unchanged\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, observation window, or CandlestickPatterns inputs are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v11',
    'feature_set': list(NETWORK_REGISTRY['candle_reversal_cnn_v10_1'].get('feature_set') or []) + [
        'candlestick_pattern_scores',
        'candlestick_pattern_flags',
    ],
})
NETWORK_REGISTRY['candle_reversal_cnn_v11']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v11']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['normalization_targets']
) + [
    {'id': 'crxp_bullish_reversal_score', 'label': 'Pattern bullish reversal score'},
    {'id': 'crxp_bearish_reversal_score', 'label': 'Pattern bearish reversal score'},
    {'id': 'crxp_bullish_continuation_score', 'label': 'Pattern bullish continuation score'},
    {'id': 'crxp_bearish_continuation_score', 'label': 'Pattern bearish continuation score'},
    {'id': 'crxp_hammer', 'label': 'Pattern hammer'},
    {'id': 'crxp_shooting_star', 'label': 'Pattern shooting star'},
    {'id': 'crxp_bullish_engulfing', 'label': 'Pattern bullish engulfing'},
    {'id': 'crxp_bearish_engulfing', 'label': 'Pattern bearish engulfing'},
    {'id': 'crxp_bullish_harami', 'label': 'Pattern bullish harami'},
    {'id': 'crxp_bearish_harami', 'label': 'Pattern bearish harami'},
    {'id': 'crxp_morning_star', 'label': 'Pattern morning star'},
    {'id': 'crxp_evening_star', 'label': 'Pattern evening star'},
    {'id': 'crxp_rising_three_methods', 'label': 'Pattern rising three methods'},
    {'id': 'crxp_falling_three_methods', 'label': 'Pattern falling three methods'},
]

NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v10_1'])
NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only'].update({
    'id': 'candle_reversal_cnn_v11_scores_only',
    'label': 'Candle Reversal CNN v11 Scores Only',
    'description': (
        'Score-only candlestick-pattern ablation for the tri-head candle reversal CNN. '
        'It keeps the v10.1 clean-target tri-head contract, adds the four CandlestickPatterns score heads, and intentionally omits the exact classical pattern flags.'
    ),
    'signature': (
        'This tri-head temporal CNN keeps the same explicit bearish, neutral, and bullish setup heads and the same clean-target context-aware dataset as v10.1, '
        'but it adds only the frozen CandlestickPatterns(trendLookback=5, bodyAveragePeriod=14) score heads. '
        'The exact classical pattern flags are intentionally excluded so the model gets softer pattern context without the full sparse one-hot pattern map.\n\n'
        'Pattern-score additions:\n'
        'bullish_reversal_score, bearish_reversal_score,\n'
        'bullish_continuation_score, bearish_continuation_score\n\n'
        'Directional-head train rebalance stays identical to v10.1:\n'
        'retain rest rows for bearish head by directional_head_rest_retention\n'
        'retain rest rows for bullish head by directional_head_rest_retention\n'
        'keep neutral head rows unchanged\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, observation window, or CandlestickPatterns score inputs are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v11_scores_only',
    'feature_set': list(NETWORK_REGISTRY['candle_reversal_cnn_v10_1'].get('feature_set') or []) + [
        'candlestick_pattern_scores',
    ],
})
NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only']['parameter_schema'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['parameter_schema']
)
NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['normalization_targets']
) + [
    {'id': 'crxp_bullish_reversal_score', 'label': 'Pattern bullish reversal score'},
    {'id': 'crxp_bearish_reversal_score', 'label': 'Pattern bearish reversal score'},
    {'id': 'crxp_bullish_continuation_score', 'label': 'Pattern bullish continuation score'},
    {'id': 'crxp_bearish_continuation_score', 'label': 'Pattern bearish continuation score'},
]

NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only'])
NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only'].update({
    'id': 'candle_reversal_cnn_v12_scores_only',
    'label': 'Candle Reversal CNN v12 Scores Only',
    'description': (
        'Economic-target follow-up to the score-only candlestick-pattern candle reversal CNN. '
        'It keeps the v11 score-only context surface, but replaces the generic future-reversal label with a first-touch TP-before-SL target.'
    ),
    'signature': (
        'This tri-head temporal CNN keeps the same explicit bearish, neutral, and bullish setup heads and the same score-only '
        'CandlestickPatterns context surface as v11_scores_only, but the supervised target becomes more execution-aware. '
        'Instead of asking whether future reversal excursion eventually dominates the opposite side, it asks whether a side-specific '
        'take-profit ATR multiple is touched before the opposite stop-loss ATR multiple within the target horizon. '
        'The prior move still gates whether a bullish or bearish reversal label is even allowed.\n\n'
        'Pattern-score additions stay identical to v11_scores_only:\n'
        'bullish_reversal_score, bearish_reversal_score,\n'
        'bullish_continuation_score, bearish_continuation_score\n\n'
        'Economic target:\n'
        'bullish label requires prior downside >= pretrend_threshold ATR and bullish TP touched before bullish SL\n'
        'bearish label requires prior upside >= pretrend_threshold ATR and bearish TP touched before bearish SL\n'
        'otherwise no_reversal\n\n'
        'Directional-head train rebalance stays identical to v10.1:\n'
        'retain rest rows for bearish head by directional_head_rest_retention\n'
        'retain rest rows for bullish head by directional_head_rest_retention\n'
        'keep neutral head rows unchanged\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, observation window, or CandlestickPatterns score inputs are discarded.'
    ),
    'runner_id': 'candle_reversal_cnn_v12_scores_only',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only']['defaults'],
        'targetMode': 'future_candle_reversal_tp_sl_classification',
        'reversalTakeProfitAtr': 0.75,
        'reversalStopLossAtr': 1.0,
    },
})
NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only']['parameter_schema'] = [
    field
    for field in NETWORK_REGISTRY['candle_reversal_cnn_v10_1']['parameter_schema']
    if str(field.get('key') or '').strip() not in {'reversalThreshold', 'dominanceRatio'}
] + [
    {
        'key': 'reversalTakeProfitAtr',
        'label': 'Take-profit ATR',
        'type': 'number',
        'min': 0.1,
        'step': 'any',
        'group': 'special',
        'description': 'ATR multiple that must be touched first in the predicted reversal direction.',
    },
    {
        'key': 'reversalStopLossAtr',
        'label': 'Stop-loss ATR',
        'type': 'number',
        'min': 0.1,
        'step': 'any',
        'group': 'special',
        'description': 'ATR multiple on the opposite side that invalidates the reversal label if touched first.',
    },
]
NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v11_scores_only']['normalization_targets']
)

NETWORK_REGISTRY['candle_reversal_setup_quality_cnn_v1'] = deepcopy(NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only'])
NETWORK_REGISTRY['candle_reversal_setup_quality_cnn_v1'].update({
    'id': 'candle_reversal_setup_quality_cnn_v1',
    'label': 'Candle Reversal Setup Quality CNN v1',
    'description': (
        'Good-vs-rest setup-quality follow-up to the score-only candlestick-pattern reversal family. '
        'It keeps the same CandlestickPatterns score context surface as v12, but changes the supervised objective '
        'from direct reversal classification to deterministic reversal-candidate quality filtering.'
    ),
    'signature': (
        'This temporal CNN keeps the same score-only CandlestickPatterns context surface as v12, '
        'but stops asking whether every bar is bullish, neutral, or bearish reversal. Instead, it opens a deterministic '
        'candidate gate whenever the prior move already implies a possible reversal setup, and then learns whether that '
        'candidate deserves to trade at all.\n\n'
        'Candidate event gate:\n'
        'bullish candidate when prev_move_atr <= -pretrend_threshold\n'
        'bearish candidate when prev_move_atr >= pretrend_threshold\n\n'
        'Good-vs-rest target on candidate rows:\n'
        'good_setup when the reversal-side TP ATR multiple is touched before the opposite SL ATR multiple within the future horizon\n'
        'not_good_setup otherwise, including first-touch bad, timeouts, and same-bar ambiguous rows\n\n'
        'Pattern-score context stays identical to v12:\n'
        'bullish_reversal_score, bearish_reversal_score,\n'
        'bullish_continuation_score, bearish_continuation_score\n\n'
        'Rows without full ATR history, pretrend lookback, future horizon, observation window, or CandlestickPatterns score inputs are discarded.'
    ),
    'runner_id': 'candle_reversal_setup_quality_cnn_v1',
    'score_metric': 'class_good_setup_f1',
    'score_label': 'Good F1',
    'task_label': 'Candle-reversal good-setup detector CNN',
    'defaults': {
        **NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only']['defaults'],
        'targetMode': 'candle_reversal_setup_quality_good_vs_rest_classification',
        'classWeightMode': 'inverse_frequency',
        'classWeightExponent': 0.75,
    },
})
NETWORK_REGISTRY['candle_reversal_setup_quality_cnn_v1']['metric_sections'] = [
    {
        'id': 'validation_metrics',
        'label': 'Validation metrics',
        'source': 'latest_train',
        'metric_root': 'validation',
        'metrics': [
            {'key': 'class_good_setup_f1', 'label': 'Good F1', 'format': 'percent'},
            {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
            {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
            {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
            {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
            {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
            {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            {'key': 'class_not_good_setup_recall', 'label': 'Rest recall', 'format': 'percent'},
        ],
    },
    {
        'id': 'test_metrics',
        'label': 'Test metrics',
        'source': 'latest_test',
        'metrics': [
            {'key': 'class_good_setup_f1', 'label': 'Good F1', 'format': 'percent'},
            {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
            {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
            {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
            {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
            {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
            {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            {'key': 'class_not_good_setup_recall', 'label': 'Rest recall', 'format': 'percent'},
        ],
    },
]
NETWORK_REGISTRY['candle_reversal_setup_quality_cnn_v1']['parameter_schema'] = [
    field
    for field in NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only']['parameter_schema']
    if str(field.get('key') or '').strip() not in {
        'neutralRetention',
        'targetCleanNeutralPretrendCeiling',
        'targetCleanNeutralExcursionCeiling',
        'targetCleanPositivePretrendFloor',
        'targetCleanPositiveExcursionFloor',
        'stage1NeutralPretrendCeiling',
        'stage1NeutralExcursionCeiling',
        'stage1PositivePretrendFloor',
        'stage1PositiveExcursionFloor',
        'stage1SetupPretrendFloor',
        'stage1SetupExcursionFloor',
        'stage1SetupDominanceFloor',
        'stage1SetupMarginFloor',
        'directionalHeadRestRetention',
    }
]
NETWORK_REGISTRY['candle_reversal_setup_quality_cnn_v1']['normalization_targets'] = list(
    NETWORK_REGISTRY['candle_reversal_cnn_v12_scores_only']['normalization_targets']
)

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1'] = deepcopy(NETWORK_REGISTRY['neural_market_regime_cnn_v1'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v1',
    'label': 'EMA Low ADX Setup Quality CNN',
    'description': (
        'Temporal CNN that scores the quality of low-ADX Bollinger reclaim events inspired by scalp seed 120. '
        'It keeps the deterministic setup idea and learns which candidate events are good, weak, or bad follow-through.'
    ),
    'signature': (
        'Builds each temporal sample from a rolling window of scalp-context features centered on a deterministic '
        'low-ADX Bollinger reclaim candidate. Inputs include short returns, candle geometry, volume z-score, EMA '
        'distance and gap ratios, ATR ratio, RSI level and delta, ADX with DI spread, Bollinger width and position, '
        'touch depth below the lower band, close distance to the lower and middle bands, and reclaim strength from '
        'EMA9 back toward the Bollinger middle band.\n\n'
        'Candidate event gate inspired by seed 120:\n'
        'low[t] <= bb_lower[t] + setup_touch_slack_atr * ATR_14[t]\n'
        'close[t-1] <= bb_lower[t-1] + setup_prev_band_slack_atr * ATR_14[t-1]\n'
        'close[t] >= EMA_9[t] + setup_bounce_fraction * max(bb_middle[t] - EMA_9[t], 0)\n'
        'close[t] > open[t]\n'
        'RSI_14[t-1] <= setup_prev_rsi_ceiling\n'
        'setup_current_rsi_floor <= RSI_14[t] <= setup_current_rsi_ceiling\n'
        'ADX_14[t] <= setup_adx_ceiling\n\n'
        'Future quality target formulas for candidate rows:\n'
        'future_upside_atr = (max(high[t+1:t+H]) - close[t]) / ATR_14[t]\n'
        'future_downside_atr = (close[t] - min(low[t+1:t+H])) / ATR_14[t]\n'
        'target = good_setup, if future_upside_atr >= target_quality_good_excursion_threshold '
        'and future_upside_atr >= target_quality_good_dominance_ratio * future_downside_atr\n'
        'target = bad_setup, if future_downside_atr >= target_quality_bad_excursion_threshold '
        'and future_downside_atr >= target_quality_bad_dominance_ratio * future_upside_atr\n'
        'target = weak_setup, otherwise\n\n'
        'Rows without full future horizon or a full observation window are discarded. '
        'Only rows passing the candidate-event gate become training examples.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v1',
    'score_metric': 'macro_f1',
    'score_label': 'Macro F1',
    'task_label': 'Seed-120 setup quality CNN classifier',
    'feature_set': [
        'returns',
        'candle_geometry',
        'volume_zscore',
        'ema_gap',
        'atr_ratio',
        'rsi_reclaim',
        'adx_di_spread',
        'bollinger_location',
        'lower_band_touch_depth',
        'reclaim_strength',
    ],
    'snapshot_cards': [
        {
            'id': 'best_validation_macro_f1',
            'label': 'Best validation macro F1',
            'source': 'best_model',
            'metric_path': 'validation.macro_f1',
            'format': 'percent',
            'hint': 'Best promoted setup-quality model measured on the validation split with class-balanced F1.',
        },
        {
            'id': 'latest_validation_macro_f1',
            'label': 'Latest validation macro F1',
            'source': 'latest_train',
            'metric_path': 'validation.macro_f1',
            'format': 'percent',
            'hint': 'Latest completed training run validation result.',
        },
        {
            'id': 'latest_test_macro_f1',
            'label': 'Latest test macro F1',
            'source': 'latest_test',
            'metric_path': 'macro_f1',
            'format': 'percent',
            'hint': 'Latest chronological holdout evaluation.',
        },
        {
            'id': 'latest_candidate_rows',
            'label': 'Candidate rows',
            'source': 'latest_train',
            'metric_path': 'candidate_summary.candidate_rows',
            'format': 'integer',
            'hint': 'How many deterministic seed-120-like events became candidate rows.',
        },
    ],
    'metric_sections': [
        {
            'id': 'validation_metrics',
            'label': 'Validation metrics',
            'source': 'latest_train',
            'metric_root': 'validation',
            'metrics': [
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
                {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
                {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
                {'key': 'class_bad_setup_precision', 'label': 'Bad precision', 'format': 'percent'},
                {'key': 'class_bad_setup_recall', 'label': 'Bad recall', 'format': 'percent'},
                {'key': 'class_weak_setup_recall', 'label': 'Weak recall', 'format': 'percent'},
            ],
        },
        {
            'id': 'test_metrics',
            'label': 'Test metrics',
            'source': 'latest_test',
            'metrics': [
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
                {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
                {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
                {'key': 'class_bad_setup_precision', 'label': 'Bad precision', 'format': 'percent'},
                {'key': 'class_bad_setup_recall', 'label': 'Bad recall', 'format': 'percent'},
                {'key': 'class_weak_setup_recall', 'label': 'Weak recall', 'format': 'percent'},
            ],
        },
    ],
    'defaults': {
        'symbol': 'EURUSD',
        'timeframe': 'M5',
        'bars': 50000,
        'validationSplit': 0.15,
        'testSplit': 0.15,
        'observationWindow': 24,
        'convFilters': 48,
        'kernelSize': 5,
        'targetHorizon': 8,
        'setupAdxCeiling': 28.0,
        'setupPrevRsiCeiling': 38.0,
        'setupCurrentRsiFloor': 38.0,
        'setupCurrentRsiCeiling': 50.0,
        'setupTouchSlackAtr': 0.06,
        'setupPrevBandSlackAtr': 0.08,
        'setupBounceFraction': 0.02,
        'setupDiSpreadFloor': 0.0,
        'setupCandidateMinGapBars': 0,
        'targetQualityGoodExcursionThreshold': 0.82,
        'targetQualityBadExcursionThreshold': 0.52,
        'targetQualityGoodDominanceRatio': 1.1,
        'targetQualityBadDominanceRatio': 1.1,
        'learningRate': 0.0004,
        'epochs': 240,
        'batchSize': 128,
        'classWeightMode': 'inverse_frequency',
        'classWeightExponent': 0.75,
        'neutralRetention': 1.0,
        'hiddenLayers': [
            {'id': 'layer_1', 'size': 96, 'activation': 'relu', 'dropout': 0.08},
            {'id': 'layer_2', 'size': 48, 'activation': 'relu', 'dropout': 0.08},
        ],
        'normalizationColumns': [],
        'seed': 42,
    },
    'parameter_schema': [
        {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
        {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
        {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 200, 'max': 100000, 'group': 'dataset'},
        {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 4, 'group': 'architecture'},
        {'key': 'convFilters', 'label': 'Conv filters', 'type': 'number', 'min': 4, 'group': 'architecture'},
        {'key': 'kernelSize', 'label': 'Kernel size', 'type': 'number', 'min': 2, 'group': 'architecture'},
        {'key': 'targetHorizon', 'label': 'Future candles', 'type': 'number', 'min': 2, 'group': 'special'},
        {'key': 'setupAdxCeiling', 'label': 'ADX ceiling', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupPrevRsiCeiling', 'label': 'Prev RSI ceiling', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupCurrentRsiFloor', 'label': 'Current RSI floor', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupCurrentRsiCeiling', 'label': 'Current RSI ceiling', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupTouchSlackAtr', 'label': 'Touch slack ATR', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupPrevBandSlackAtr', 'label': 'Prev band slack ATR', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupBounceFraction', 'label': 'Bounce fraction', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupDiSpreadFloor', 'label': 'DI spread floor', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'setupCandidateMinGapBars', 'label': 'Candidate min gap bars', 'type': 'number', 'min': 0, 'group': 'special'},
        {'key': 'targetQualityGoodExcursionThreshold', 'label': 'Good excursion ATR', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'targetQualityBadExcursionThreshold', 'label': 'Bad excursion ATR', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'targetQualityGoodDominanceRatio', 'label': 'Good dominance ratio', 'type': 'number', 'min': 1.0, 'step': 'any', 'group': 'special'},
        {'key': 'targetQualityBadDominanceRatio', 'label': 'Bad dominance ratio', 'type': 'number', 'min': 1.0, 'step': 'any', 'group': 'special'},
        {'key': 'epochs', 'label': 'Epochs', 'type': 'number', 'min': 10, 'group': 'training'},
        {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
        {'key': 'batchSize', 'label': 'Batch size', 'type': 'number', 'min': 8, 'group': 'training'},
        {'key': 'classWeightMode', 'label': 'Class weights', 'type': 'string', 'group': 'training'},
        {'key': 'classWeightExponent', 'label': 'Class weight exponent', 'type': 'number', 'min': 0.0, 'step': 'any', 'group': 'training'},
        {'key': 'neutralRetention', 'label': 'Weak retention', 'type': 'number', 'min': 0.05, 'max': 1.0, 'step': 'any', 'group': 'training'},
        {'key': 'seed', 'label': 'Seed', 'type': 'number', 'min': 1, 'group': 'training'},
    ],
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['normalization_targets'] = [
    {'id': 'slq_return_1', 'label': 'Return 1'},
    {'id': 'slq_return_3', 'label': 'Return 3'},
    {'id': 'slq_return_6', 'label': 'Return 6'},
    {'id': 'slq_body_ratio', 'label': 'Body ratio'},
    {'id': 'slq_upper_wick_ratio', 'label': 'Upper wick ratio'},
    {'id': 'slq_lower_wick_ratio', 'label': 'Lower wick ratio'},
    {'id': 'slq_close_location', 'label': 'Close location'},
    {'id': 'slq_volume_zscore_20', 'label': 'Volume z-score 20'},
    {'id': 'slq_close_to_ema_9_ratio', 'label': 'Close to EMA9'},
    {'id': 'slq_close_to_ema_21_ratio', 'label': 'Close to EMA21'},
    {'id': 'slq_ema_gap_9_21_ratio', 'label': 'EMA gap 9/21'},
    {'id': 'slq_atr_14_ratio', 'label': 'ATR 14 ratio'},
    {'id': 'slq_rsi_14', 'label': 'RSI 14'},
    {'id': 'slq_rsi_delta_1', 'label': 'RSI delta 1'},
    {'id': 'slq_rsi_delta_3', 'label': 'RSI delta 3'},
    {'id': 'slq_adx_14', 'label': 'ADX 14'},
    {'id': 'slq_di_spread_14', 'label': 'DI spread 14'},
    {'id': 'slq_bb_width_ratio', 'label': 'BB width ratio'},
    {'id': 'slq_bb_position', 'label': 'BB position'},
    {'id': 'slq_low_to_bb_lower_atr', 'label': 'Low to BB lower ATR'},
    {'id': 'slq_close_to_bb_lower_atr', 'label': 'Close to BB lower ATR'},
    {'id': 'slq_close_to_bb_middle_ratio', 'label': 'Close to BB middle'},
    {'id': 'slq_reclaim_strength', 'label': 'Reclaim strength'},
]

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2'] = deepcopy(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v2',
    'label': 'EMA Low ADX Setup Quality CNN v2',
    'description': (
        'Binary temporal CNN that keeps the deterministic seed-120 candidate gate, but drops ambiguous middle outcomes. '
        'It learns to separate clean good follow-through from clean failed setups.'
    ),
    'signature': (
        'Builds each temporal sample from the same low-ADX Bollinger reclaim candidate gate used by v1, over the same '
        'scalp-context feature signature. Unlike v1, v2 does not keep a weak middle class. Candidate rows are labeled only '
        'when the future path is clean enough to be treated as a good or bad setup.\n\n'
        'Candidate event gate inspired by seed 120:\n'
        'low[t] <= bb_lower[t] + setup_touch_slack_atr * ATR_14[t]\n'
        'close[t-1] <= bb_lower[t-1] + setup_prev_band_slack_atr * ATR_14[t-1]\n'
        'close[t] >= EMA_9[t] + setup_bounce_fraction * max(bb_middle[t] - EMA_9[t], 0)\n'
        'close[t] > open[t]\n'
        'RSI_14[t-1] <= setup_prev_rsi_ceiling\n'
        'setup_current_rsi_floor <= RSI_14[t] <= setup_current_rsi_ceiling\n'
        'ADX_14[t] <= setup_adx_ceiling\n\n'
        'Future clean-target formulas for candidate rows:\n'
        'future_upside_atr = (max(high[t+1:t+H]) - close[t]) / ATR_14[t]\n'
        'future_downside_atr = (close[t] - min(low[t+1:t+H])) / ATR_14[t]\n'
        'target = good_setup, if future_upside_atr >= target_quality_good_excursion_threshold '
        'and future_upside_atr >= target_quality_good_dominance_ratio * future_downside_atr '
        'and future_downside_atr <= target_quality_good_counter_excursion_ceiling\n'
        'target = bad_setup, if future_downside_atr >= target_quality_bad_excursion_threshold '
        'and future_downside_atr >= target_quality_bad_dominance_ratio * future_upside_atr '
        'and future_upside_atr <= target_quality_bad_counter_excursion_ceiling\n'
        'candidate rows that do not meet either clean target are dropped from the supervised dataset\n\n'
        'Rows without full future horizon or a full observation window are discarded.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v2',
    'task_label': 'Seed-120 clean setup quality CNN classifier',
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2']['metric_sections'] = [
    {
        'id': 'validation_metrics',
        'label': 'Validation metrics',
        'source': 'latest_train',
        'metric_root': 'validation',
        'metrics': [
            {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
            {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
            {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
            {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
            {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
            {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
            {'key': 'class_bad_setup_precision', 'label': 'Bad precision', 'format': 'percent'},
            {'key': 'class_bad_setup_recall', 'label': 'Bad recall', 'format': 'percent'},
        ],
    },
    {
        'id': 'test_metrics',
        'label': 'Test metrics',
        'source': 'latest_test',
        'metrics': [
            {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
            {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
            {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
            {'key': 'directional_accuracy', 'label': 'Directional accuracy', 'format': 'percent'},
            {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
            {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
            {'key': 'class_bad_setup_precision', 'label': 'Bad precision', 'format': 'percent'},
            {'key': 'class_bad_setup_recall', 'label': 'Bad recall', 'format': 'percent'},
        ],
    },
]
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2']['defaults'] = {
    **NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['defaults'],
    'targetQualityGoodCounterExcursionCeiling': 0.45,
    'targetQualityBadCounterExcursionCeiling': 0.45,
}
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2']['parameter_schema'] = (
    list(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['parameter_schema'])
    + [
        {'key': 'targetQualityGoodCounterExcursionCeiling', 'label': 'Good counter ATR ceiling', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
        {'key': 'targetQualityBadCounterExcursionCeiling', 'label': 'Bad counter ATR ceiling', 'type': 'number', 'min': 0, 'step': 'any', 'group': 'special'},
    ]
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2']['normalization_targets'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v3'] = deepcopy(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v2'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v3'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v3',
    'label': 'EMA Low ADX Setup Quality CNN v3',
    'description': (
        'Binary temporal CNN that keeps the deterministic seed-120 candidate gate, but changes the target '
        'contract to first-touch path ordering. It learns only on rows where good or bad follow-through is '
        'decided cleanly by which ATR excursion threshold is reached first.'
    ),
    'signature': (
        'Builds each temporal sample from the same low-ADX Bollinger reclaim candidate gate used by v1 and v2, over the same '
        'scalp-context feature signature. Unlike v2, v3 does not label by future extrema alone. Candidate rows are kept only '
        'when the future path reaches a clean good or bad ATR threshold first.\n\n'
        'Candidate event gate inspired by seed 120:\n'
        'low[t] <= bb_lower[t] + setup_touch_slack_atr * ATR_14[t]\n'
        'close[t-1] <= bb_lower[t-1] + setup_prev_band_slack_atr * ATR_14[t-1]\n'
        'close[t] >= EMA_9[t] + setup_bounce_fraction * max(bb_middle[t] - EMA_9[t], 0)\n'
        'close[t] > open[t]\n'
        'RSI_14[t-1] <= setup_prev_rsi_ceiling\n'
        'setup_current_rsi_floor <= RSI_14[t] <= setup_current_rsi_ceiling\n'
        'ADX_14[t] <= setup_adx_ceiling\n\n'
        'First-touch target formulas for candidate rows:\n'
        'good_target = close[t] + target_quality_good_excursion_threshold * ATR_14[t]\n'
        'bad_target = close[t] - target_quality_bad_excursion_threshold * ATR_14[t]\n'
        'scan future bars from t+1 to t+H in order\n'
        'target = good_setup, if high first reaches good_target before low reaches bad_target\n'
        'target = bad_setup, if low first reaches bad_target before high reaches good_target\n'
        'rows where neither target is touched within horizon are dropped\n'
        'rows where both targets are first crossed in the same future bar are dropped as ambiguous\n\n'
        'Rows without full future horizon or a full observation window are discarded.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v3',
    'task_label': 'Seed-120 first-touch setup quality CNN classifier',
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v3']['defaults'] = dict(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['defaults']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v3']['parameter_schema'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['parameter_schema']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v3']['normalization_targets'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4'] = deepcopy(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v3'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v4',
    'label': 'EMA Low ADX Setup Quality CNN v4',
    'description': (
        'Binary temporal CNN that keeps the deterministic seed-120 candidate gate and first-touch future path, '
        'but changes the supervised objective to good-setup detection. It learns to separate first-touch good setups '
        'from the full rest bucket of bad, timeout, and ambiguous candidate events.'
    ),
    'signature': (
        'Builds each temporal sample from the same low-ADX Bollinger reclaim candidate gate used by v1-v3, over the same '
        'scalp-context feature signature. Unlike v3, v4 is not a symmetric good-vs-bad classifier. It keeps the first-touch '
        'path scan, but treats only clean good first-touch rows as the positive class and everything else as rest.\n\n'
        'Candidate event gate inspired by seed 120:\n'
        'low[t] <= bb_lower[t] + setup_touch_slack_atr * ATR_14[t]\n'
        'close[t-1] <= bb_lower[t-1] + setup_prev_band_slack_atr * ATR_14[t-1]\n'
        'close[t] >= EMA_9[t] + setup_bounce_fraction * max(bb_middle[t] - EMA_9[t], 0)\n'
        'close[t] > open[t]\n'
        'RSI_14[t-1] <= setup_prev_rsi_ceiling\n'
        'setup_current_rsi_floor <= RSI_14[t] <= setup_current_rsi_ceiling\n'
        'ADX_14[t] <= setup_adx_ceiling\n\n'
        'First-touch good-vs-rest target:\n'
        'good_target = close[t] + target_quality_good_excursion_threshold * ATR_14[t]\n'
        'bad_target = close[t] - target_quality_bad_excursion_threshold * ATR_14[t]\n'
        'scan future bars from t+1 to t+H in order\n'
        'target = good_setup, if high first reaches good_target before low reaches bad_target\n'
        'target = not_good_setup, otherwise for all remaining candidate rows, including first-touch bad, timeouts, and same-bar ambiguous rows\n\n'
        'Rows without full future horizon or a full observation window are discarded.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v4',
    'score_metric': 'class_good_setup_f1',
    'score_label': 'Good F1',
    'task_label': 'Seed-120 good-setup detector CNN',
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['metric_sections'] = [
    {
        'id': 'validation_metrics',
        'label': 'Validation metrics',
        'source': 'latest_train',
        'metric_root': 'validation',
        'metrics': [
            {'key': 'class_good_setup_f1', 'label': 'Good F1', 'format': 'percent'},
            {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
            {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
            {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
            {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
            {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
            {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            {'key': 'class_not_good_setup_recall', 'label': 'Rest recall', 'format': 'percent'},
        ],
    },
    {
        'id': 'test_metrics',
        'label': 'Test metrics',
        'source': 'latest_test',
        'metrics': [
            {'key': 'class_good_setup_f1', 'label': 'Good F1', 'format': 'percent'},
            {'key': 'class_good_setup_precision', 'label': 'Good precision', 'format': 'percent'},
            {'key': 'class_good_setup_recall', 'label': 'Good recall', 'format': 'percent'},
            {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
            {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
            {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
            {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            {'key': 'class_not_good_setup_recall', 'label': 'Rest recall', 'format': 'percent'},
        ],
    },
]
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['defaults'] = dict(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['defaults']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['parameter_schema'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['parameter_schema']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['normalization_targets'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5'] = deepcopy(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v5',
    'label': 'EMA Low ADX Setup Quality CNN v5',
    'description': (
        'Pattern-context follow-up to the seed-120 good-vs-rest setup-quality CNN. '
        'It keeps the same deterministic low-ADX Bollinger reclaim gate and first-touch good-vs-rest target as v4, '
        'but augments the context with CandlestickPatterns score heads.'
    ),
    'signature': (
        'This temporal CNN keeps the exact same deterministic seed-120 candidate gate and the same first-touch '
        'good-vs-rest target contract used by v4. The difference is in the context surface: beyond the low-ADX '
        'reclaim geometry, trend, volatility, and Bollinger location features, it also sees the frozen '
        'CandlestickPatterns(trendLookback=5, bodyAveragePeriod=14) score heads.\n\n'
        'Pattern-score additions:\n'
        'bullish_reversal_score, bearish_reversal_score,\n'
        'bullish_continuation_score, bearish_continuation_score\n\n'
        'Candidate event gate inspired by seed 120:\n'
        'low[t] <= bb_lower[t] + setup_touch_slack_atr * ATR_14[t]\n'
        'close[t-1] <= bb_lower[t-1] + setup_prev_band_slack_atr * ATR_14[t-1]\n'
        'close[t] >= EMA_9[t] + setup_bounce_fraction * max(bb_middle[t] - EMA_9[t], 0)\n'
        'close[t] > open[t]\n'
        'RSI_14[t-1] <= setup_prev_rsi_ceiling\n'
        'setup_current_rsi_floor <= RSI_14[t] <= setup_current_rsi_ceiling\n'
        'ADX_14[t] <= setup_adx_ceiling\n\n'
        'First-touch good-vs-rest target:\n'
        'good_target = close[t] + target_quality_good_excursion_threshold * ATR_14[t]\n'
        'bad_target = close[t] - target_quality_bad_excursion_threshold * ATR_14[t]\n'
        'scan future bars from t+1 to t+H in order\n'
        'target = good_setup, if high first reaches good_target before low reaches bad_target\n'
        'target = not_good_setup, otherwise for all remaining candidate rows, including first-touch bad, timeouts, and same-bar ambiguous rows\n\n'
        'Rows without full future horizon, observation window, or CandlestickPatterns score inputs are discarded.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v5',
    'task_label': 'Seed-120 good-setup detector CNN with pattern-score context',
    'feature_set': list(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4'].get('feature_set') or []) + [
        'candlestick_pattern_scores',
    ],
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['defaults'] = dict(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['defaults']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['parameter_schema'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['parameter_schema']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['normalization_targets'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v4']['normalization_targets']
) + [
    {'id': 'slqp_bullish_reversal_score', 'label': 'Pattern bullish reversal score'},
    {'id': 'slqp_bearish_reversal_score', 'label': 'Pattern bearish reversal score'},
    {'id': 'slqp_bullish_continuation_score', 'label': 'Pattern bullish continuation score'},
    {'id': 'slqp_bearish_continuation_score', 'label': 'Pattern bearish continuation score'},
]

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v6'] = deepcopy(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v6'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v6',
    'label': 'EMA Low ADX Setup Quality CNN v6',
    'description': (
        'Cluster-context follow-up to the seed-120 good-vs-rest setup-quality CNN. '
        'It keeps the same deterministic low-ADX Bollinger reclaim gate and CandlestickPatterns score context as v5, '
        'but also gives the network causal information about recent setup clustering, candidate recency, and relative local strength.'
    ),
    'signature': (
        'This temporal CNN keeps the exact same deterministic seed-120 candidate gate and the same first-touch '
        'good-vs-rest target contract used by v5. The change is in the context surface: beyond the pattern-score inputs, '
        'it also sees causal cluster features derived from prior candidate behavior, such as recent candidate density, '
        'bars since the previous candidate, DI spread relative to the last and recent-best candidates, and reclaim strength '
        'relative to the recent cluster maximum.\n\n'
        'The goal is to let the model learn when a reclaim is merely another weak member of a noisy local cluster versus '
        'the strongest candidate in that neighborhood, without hard-coding new DI floors or gap filters into the contract.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v6',
    'task_label': 'Seed-120 good-setup detector CNN with pattern and cluster context',
    'feature_set': list(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5'].get('feature_set') or []) + [
        'candidate_cluster_context',
    ],
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v6']['defaults'] = dict(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['defaults']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v6']['parameter_schema'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['parameter_schema']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v6']['normalization_targets'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['normalization_targets']
) + [
    {'id': 'slqc_base_candidate_flag', 'label': 'Cluster base candidate flag'},
    {'id': 'slqc_prev_candidate_gap_24', 'label': 'Cluster previous candidate gap 24'},
    {'id': 'slqc_recent_candidate_density_12', 'label': 'Cluster candidate density 12'},
    {'id': 'slqc_recent_candidate_density_24', 'label': 'Cluster candidate density 24'},
    {'id': 'slqc_last_candidate_di_spread', 'label': 'Cluster last candidate DI spread'},
    {'id': 'slqc_di_vs_last_candidate', 'label': 'Cluster DI vs last candidate'},
    {'id': 'slqc_di_vs_recent_candidate_max_12', 'label': 'Cluster DI vs recent max 12'},
    {'id': 'slqc_di_vs_recent_candidate_mean_12', 'label': 'Cluster DI vs recent mean 12'},
    {'id': 'slqc_reclaim_vs_recent_candidate_max_12', 'label': 'Cluster reclaim vs recent max 12'},
]

NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v7'] = deepcopy(NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5'])
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v7'].update({
    'id': 'ema_low_adx_setup_quality_cnn_v7',
    'label': 'EMA Low ADX Setup Quality CNN v7',
    'description': (
        'Target-aligned follow-up to the seed-120 good-vs-rest setup-quality CNN. '
        'It keeps the v5 pattern-score context, but replaces the generic first-touch good-vs-rest label '
        'with the exact long-side tp100/sl100/h8 economic outcome used by the downstream probe frontier.'
    ),
    'signature': (
        'This temporal CNN keeps the exact same deterministic seed-120 candidate gate and the same CandlestickPatterns '
        'score context used by v5, but changes the training target to mirror the active downstream lane directly.\n\n'
        'Candidate event gate:\n'
        'low[t] <= bb_lower[t] + setup_touch_slack_atr * ATR_14[t]\n'
        'close[t-1] <= bb_lower[t-1] + setup_prev_band_slack_atr * ATR_14[t-1]\n'
        'close[t] >= EMA_9[t] + setup_bounce_fraction * max(bb_middle[t] - EMA_9[t], 0)\n'
        'close[t] > open[t]\n'
        'RSI_14[t-1] <= setup_prev_rsi_ceiling\n'
        'setup_current_rsi_floor <= RSI_14[t] <= setup_current_rsi_ceiling\n'
        'ADX_14[t] <= setup_adx_ceiling\n\n'
        'Target-aligned label:\n'
        'positive = future bullish tp/sl code == take_profit_first within target_horizon\n'
        'negative = all other candidate rows, including stop-loss-first, timeout, and ambiguous rows\n'
        'with target_reversal_take_profit_atr = 1.0, target_reversal_stop_loss_atr = 1.0, target_horizon = 8 by default.\n\n'
        'The goal is to train directly against the exact economic lane that the fresh frontier keeps testing, '
        'instead of hoping the broader good-setup proxy sorts it out later.'
    ),
    'runner_id': 'ema_low_adx_setup_quality_cnn_v7',
    'task_label': 'Seed-120 tp/sl-aligned good-setup detector CNN with pattern-score context',
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v7']['defaults'] = dict(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['defaults']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v7']['defaults'].update({
    'targetHorizon': 8,
    'targetReversalTakeProfitAtr': 1.0,
    'targetReversalStopLossAtr': 1.0,
})
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v7']['parameter_schema'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['parameter_schema']
)
NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v7']['normalization_targets'] = list(
    NETWORK_REGISTRY['ema_low_adx_setup_quality_cnn_v5']['normalization_targets']
)

NETWORK_REGISTRY['micro_cost_edge_cnn_v1'] = deepcopy(NETWORK_REGISTRY['neural_market_regime_cnn_v1'])
NETWORK_REGISTRY['micro_cost_edge_cnn_v1'].update({
    'id': 'micro_cost_edge_cnn_v1',
    'label': 'Micro Cost Edge CNN v1',
    'description': (
        'Temporal CNN that learns whether the next few candles can beat round-trip forex cost to the upside or downside. '
        'It uses an execution-anchored first-touch target and produces long-edge, no-edge, or short-edge probabilities '
        'that are usable as an aggressive micro-scalp indicator.'
    ),
    'signature': (
        'Builds each temporal sample from a rolling window of short-horizon microstructure and volatility features, '
        'including candle geometry, short returns, volume z-score, EMA 9/21 gap, ATR ratio and slope, RSI 7/14, ADX with '
        'DI spread, Bollinger width and position, choppiness/trendiness, VWAP distance ratio, recent range/move in ATR terms, '
        'and explicit cost-to-volatility ratios.\n\n'
        'Execution-anchored first-touch target:\n'
        'entry_ref = open[t+1]\n'
        'edge_hurdle_pips = round_trip_cost_pips * target_cost_edge_multiple\n'
        'long_target = entry_ref + edge_hurdle_pips * pip_size\n'
        'short_target = entry_ref - edge_hurdle_pips * pip_size\n'
        'scan bars from t+1 to t+H in order\n'
        'target = long_edge, if high first reaches long_target before low reaches short_target\n'
        'target = short_edge, if low first reaches short_target before high reaches long_target\n'
        'target = no_edge, if neither side reaches the hurdle inside the horizon or both sides are first crossed in the same bar\n\n'
        'Rows without full future horizon or a full observation window are discarded.'
    ),
    'runner_id': 'micro_cost_edge_cnn_v1',
    'score_metric': 'directional_edge_macro_f1',
    'score_label': 'Directional edge F1',
    'task_label': 'Execution-aware micro cost-edge CNN',
    'feature_set': [
        'micro_returns',
        'candle_geometry',
        'volume_zscore',
        'ema_gap',
        'atr_ratio_and_slope',
        'rsi_levels',
        'adx_di_spread',
        'bollinger_location',
        'choppiness_trendiness',
        'vwap_distance',
        'recent_range_pressure',
        'cost_to_volatility_ratio',
    ],
    'snapshot_cards': [
        {
            'id': 'best_validation_directional_edge_f1',
            'label': 'Best validation directional edge F1',
            'source': 'best_model',
            'metric_path': 'validation.directional_edge_macro_f1',
            'format': 'percent',
            'hint': 'Best promoted model measured by the mean F1 of long-edge and short-edge classes.',
        },
        {
            'id': 'latest_test_directional_edge_f1',
            'label': 'Latest test directional edge F1',
            'source': 'latest_test',
            'metric_path': 'directional_edge_macro_f1',
            'format': 'percent',
            'hint': 'Latest chronological holdout evaluation on the directional edge classes.',
        },
        {
            'id': 'latest_sequence_rows',
            'label': 'Sequence rows',
            'source': 'latest_train',
            'metric_path': 'candidate_summary.sequence_rows',
            'format': 'integer',
            'hint': 'How many chronological sequence rows survived feature generation and horizon alignment.',
        },
    ],
    'metric_sections': [
        {
            'id': 'validation_metrics',
            'label': 'Validation metrics',
            'source': 'latest_train',
            'metric_root': 'validation',
            'metrics': [
                {'key': 'directional_edge_macro_f1', 'label': 'Directional edge F1', 'format': 'percent'},
                {'key': 'tradability_f1', 'label': 'Tradability F1', 'format': 'percent'},
                {'key': 'class_long_edge_f1', 'label': 'Long-edge F1', 'format': 'percent'},
                {'key': 'class_short_edge_f1', 'label': 'Short-edge F1', 'format': 'percent'},
                {'key': 'class_no_edge_recall', 'label': 'No-edge recall', 'format': 'percent'},
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            ],
        },
        {
            'id': 'test_metrics',
            'label': 'Test metrics',
            'source': 'latest_test',
            'metrics': [
                {'key': 'directional_edge_macro_f1', 'label': 'Directional edge F1', 'format': 'percent'},
                {'key': 'tradability_f1', 'label': 'Tradability F1', 'format': 'percent'},
                {'key': 'class_long_edge_f1', 'label': 'Long-edge F1', 'format': 'percent'},
                {'key': 'class_short_edge_f1', 'label': 'Short-edge F1', 'format': 'percent'},
                {'key': 'class_no_edge_recall', 'label': 'No-edge recall', 'format': 'percent'},
                {'key': 'macro_f1', 'label': 'Macro F1', 'format': 'percent'},
                {'key': 'accuracy', 'label': 'Accuracy', 'format': 'percent'},
                {'key': 'balanced_accuracy', 'label': 'Balanced accuracy', 'format': 'percent'},
                {'key': 'mean_confidence', 'label': 'Mean confidence', 'format': 'percent'},
            ],
        },
    ],
    'defaults': {
        'symbol': 'EURUSD',
        'timeframe': 'M1',
        'bars': 10000,
        'validationSplit': 0.15,
        'testSplit': 0.15,
        'observationWindow': 24,
        'convFilters': 48,
        'kernelSize': 5,
        'targetHorizon': 5,
        'pipSize': 0.0001,
        'roundTripCostPips': 1.6,
        'targetCostEdgeMultiple': 1.75,
        'learningRate': 0.0004,
        'epochs': 180,
        'batchSize': 256,
        'classWeightMode': 'inverse_frequency',
        'classWeightExponent': 1.25,
        'neutralRetention': 0.5,
        'hiddenLayers': [
            {'id': 'layer_1', 'size': 96, 'activation': 'relu', 'dropout': 0.08},
            {'id': 'layer_2', 'size': 48, 'activation': 'relu', 'dropout': 0.08},
        ],
        'normalizationColumns': [],
        'seed': 42,
    },
    'parameter_schema': [
        {'key': 'symbol', 'label': 'Symbol', 'type': 'string', 'group': 'dataset'},
        {'key': 'timeframe', 'label': 'Timeframe', 'type': 'string', 'group': 'dataset'},
        {'key': 'bars', 'label': 'Bars', 'type': 'number', 'min': 200, 'max': 100000, 'group': 'dataset'},
        {'key': 'validationSplit', 'label': 'Validation split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'testSplit', 'label': 'Test split', 'type': 'number', 'min': 0.05, 'max': 0.4, 'step': '0.01', 'group': 'dataset'},
        {'key': 'observationWindow', 'label': 'Observation window', 'type': 'number', 'min': 4, 'group': 'architecture'},
        {'key': 'convFilters', 'label': 'Conv filters', 'type': 'number', 'min': 4, 'group': 'architecture'},
        {'key': 'kernelSize', 'label': 'Kernel size', 'type': 'number', 'min': 2, 'group': 'architecture'},
        {'key': 'targetHorizon', 'label': 'Future candles', 'type': 'number', 'min': 2, 'group': 'special'},
        {'key': 'pipSize', 'label': 'Pip size', 'type': 'number', 'min': 0.000001, 'step': 'any', 'group': 'special'},
        {'key': 'roundTripCostPips', 'label': 'Round-trip cost pips', 'type': 'number', 'min': 0.0, 'step': 'any', 'group': 'special'},
        {'key': 'targetCostEdgeMultiple', 'label': 'Cost hurdle multiple', 'type': 'number', 'min': 1.0, 'step': 'any', 'group': 'special'},
        {'key': 'epochs', 'label': 'Epochs', 'type': 'number', 'min': 10, 'group': 'training'},
        {'key': 'learningRate', 'label': 'Learning rate', 'type': 'number', 'step': 'any', 'group': 'training'},
        {'key': 'batchSize', 'label': 'Batch size', 'type': 'number', 'min': 8, 'group': 'training'},
        {'key': 'classWeightMode', 'label': 'Class weights', 'type': 'string', 'group': 'training'},
        {'key': 'classWeightExponent', 'label': 'Class weight exponent', 'type': 'number', 'min': 0.0, 'step': 'any', 'group': 'training'},
        {'key': 'neutralRetention', 'label': 'No-edge retention', 'type': 'number', 'min': 0.05, 'max': 1.0, 'step': 'any', 'group': 'training'},
        {'key': 'seed', 'label': 'Seed', 'type': 'number', 'min': 1, 'group': 'training'},
    ],
})
NETWORK_REGISTRY['micro_cost_edge_cnn_v1']['normalization_targets'] = [
    {'id': 'mce_return_1', 'label': 'Return 1'},
    {'id': 'mce_return_2', 'label': 'Return 2'},
    {'id': 'mce_return_3', 'label': 'Return 3'},
    {'id': 'mce_range_ratio', 'label': 'Range ratio'},
    {'id': 'mce_body_ratio', 'label': 'Body ratio'},
    {'id': 'mce_upper_wick_ratio', 'label': 'Upper wick ratio'},
    {'id': 'mce_lower_wick_ratio', 'label': 'Lower wick ratio'},
    {'id': 'mce_close_location', 'label': 'Close location'},
    {'id': 'mce_volume_zscore_20', 'label': 'Volume z-score 20'},
    {'id': 'mce_ema_gap_9_21_ratio', 'label': 'EMA gap 9/21'},
    {'id': 'mce_close_to_ema_9_ratio', 'label': 'Close to EMA9'},
    {'id': 'mce_close_to_ema_21_ratio', 'label': 'Close to EMA21'},
    {'id': 'mce_atr_14_ratio', 'label': 'ATR 14 ratio'},
    {'id': 'mce_atr_slope_3', 'label': 'ATR slope 3'},
    {'id': 'mce_rsi_7', 'label': 'RSI 7'},
    {'id': 'mce_rsi_14', 'label': 'RSI 14'},
    {'id': 'mce_rsi_delta_1', 'label': 'RSI delta 1'},
    {'id': 'mce_adx_14', 'label': 'ADX 14'},
    {'id': 'mce_di_spread_14', 'label': 'DI spread 14'},
    {'id': 'mce_bb_width_ratio', 'label': 'BB width ratio'},
    {'id': 'mce_bb_position', 'label': 'BB position'},
    {'id': 'mce_choppiness_14', 'label': 'Choppiness 14'},
    {'id': 'mce_trendiness_14', 'label': 'Trendiness 14'},
    {'id': 'mce_vwap_distance_ratio', 'label': 'VWAP distance'},
    {'id': 'mce_recent_range_atr_5', 'label': 'Recent range ATR 5'},
    {'id': 'mce_recent_move_atr_3', 'label': 'Recent move ATR 3'},
    {'id': 'mce_cost_to_atr_14', 'label': 'Cost to ATR 14'},
    {'id': 'mce_cost_to_range', 'label': 'Cost to range'},
]

NETWORK_REGISTRY['micro_cost_edge_cnn_v2'] = deepcopy(NETWORK_REGISTRY['micro_cost_edge_cnn_v1'])
NETWORK_REGISTRY['micro_cost_edge_cnn_v2'].update({
    'id': 'micro_cost_edge_cnn_v2',
    'label': 'Micro Cost Edge CNN v2',
    'description': (
        'Temporal CNN that mirrors every event into long-side and short-side canonical views so the model can learn '
        'edge-for-side symmetrically before reconstructing long-edge, no-edge, and short-edge on the original event timeline.'
    ),
    'signature': (
        'Uses the same execution-aware microstructure inputs as v1, but changes the supervised contract materially.\n\n'
        'Shared event target:\n'
        'entry_ref = open[t+1]\n'
        'edge_hurdle_pips = round_trip_cost_pips * target_cost_edge_multiple\n'
        'long_target = entry_ref + edge_hurdle_pips * pip_size\n'
        'short_target = entry_ref - edge_hurdle_pips * pip_size\n'
        'scan bars from t+1 to t+H in order\n'
        'event target = long_edge, short_edge, or no_edge by first-touch, with same-bar double-touch folded into no_edge\n\n'
        'Canonical side-view contract:\n'
        '- each event is duplicated into a long-view and a short-view sample\n'
        '- short-view directional features are mirrored, wick geometry is swapped, and price-location features are inverted\n'
        '- the CNN trains on edge_for_side vs not_edge_for_side\n'
        '- validation then reconstructs event-level long/no-edge/short decisions from paired side scores with a searched threshold\n\n'
        'Rows without full future horizon or a full observation window are discarded.'
    ),
    'runner_id': 'micro_cost_edge_cnn_v2',
    'score_metric': 'directional_edge_macro_f1',
    'score_label': 'Directional edge F1',
    'task_label': 'Mirrored execution-aware micro cost-edge CNN',
    'feature_set': [
        'mirrored_side_views',
        'execution_microstructure',
        'cost_to_volatility_ratio',
        'paired_directional_reconstruction',
    ],
    'snapshot_cards': [
        {
            'id': 'best_validation_directional_edge_f1',
            'label': 'Best validation directional edge F1',
            'source': 'best_model',
            'metric_path': 'validation.directional_edge_macro_f1',
            'format': 'percent',
            'hint': 'Best promoted mirrored model measured by the mean F1 of long-edge and short-edge event classes.',
        },
        {
            'id': 'latest_test_directional_edge_f1',
            'label': 'Latest test directional edge F1',
            'source': 'latest_test',
            'metric_path': 'directional_edge_macro_f1',
            'format': 'percent',
            'hint': 'Latest chronological holdout evaluation after reconstructing event-level long/no-edge/short predictions.',
        },
        {
            'id': 'latest_event_rows',
            'label': 'Event rows',
            'source': 'latest_train',
            'metric_path': 'candidate_summary.event_rows',
            'format': 'integer',
            'hint': 'How many chronological event rows survived feature generation and horizon alignment.',
        },
    ],
    'metric_sections': [
        {
            'id': 'validation_metrics',
            'label': 'Validation metrics',
            'source': 'latest_train',
            'metric_root': 'validation',
            'metrics': [
                {'key': 'directional_edge_macro_f1', 'label': 'Directional edge F1', 'format': 'percent'},
                {'key': 'tradability_f1', 'label': 'Tradability F1', 'format': 'percent'},
                {'key': 'class_long_edge_f1', 'label': 'Long-edge F1', 'format': 'percent'},
                {'key': 'class_short_edge_f1', 'label': 'Short-edge F1', 'format': 'percent'},
                {'key': 'class_no_edge_recall', 'label': 'No-edge recall', 'format': 'percent'},
                {'key': 'side_class_edge_for_side_f1', 'label': 'Side-edge F1', 'format': 'percent'},
                {'key': 'side_macro_f1', 'label': 'Side macro F1', 'format': 'percent'},
                {'key': 'threshold', 'label': 'Threshold', 'format': 'score'},
            ],
        },
        {
            'id': 'test_metrics',
            'label': 'Test metrics',
            'source': 'latest_test',
            'metrics': [
                {'key': 'directional_edge_macro_f1', 'label': 'Directional edge F1', 'format': 'percent'},
                {'key': 'tradability_f1', 'label': 'Tradability F1', 'format': 'percent'},
                {'key': 'class_long_edge_f1', 'label': 'Long-edge F1', 'format': 'percent'},
                {'key': 'class_short_edge_f1', 'label': 'Short-edge F1', 'format': 'percent'},
                {'key': 'class_no_edge_recall', 'label': 'No-edge recall', 'format': 'percent'},
                {'key': 'side_class_edge_for_side_f1', 'label': 'Side-edge F1', 'format': 'percent'},
                {'key': 'side_macro_f1', 'label': 'Side macro F1', 'format': 'percent'},
                {'key': 'threshold', 'label': 'Threshold', 'format': 'score'},
            ],
        },
    ],
})
NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['defaults'] = dict(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v1']['defaults']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['parameter_schema'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v1']['parameter_schema']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['normalization_targets'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v1']['normalization_targets']
)

NETWORK_REGISTRY['micro_cost_edge_cnn_v3'] = deepcopy(NETWORK_REGISTRY['micro_cost_edge_cnn_v2'])
NETWORK_REGISTRY['micro_cost_edge_cnn_v3'].update({
    'id': 'micro_cost_edge_cnn_v3',
    'label': 'Micro Cost Edge CNN v3',
    'description': (
        'Hierarchical temporal CNN that separates tradable-edge detection from directional side scoring before '
        'reconstructing long-edge, no-edge, and short-edge on the event timeline.'
    ),
    'signature': (
        'Uses the same mirrored execution-aware microstructure inputs as v2, but changes the supervised contract materially.\n\n'
        'Shared event target:\n'
        'entry_ref = open[t+1]\n'
        'edge_hurdle_pips = round_trip_cost_pips * target_cost_edge_multiple\n'
        'long_target = entry_ref + edge_hurdle_pips * pip_size\n'
        'short_target = entry_ref - edge_hurdle_pips * pip_size\n'
        'scan bars from t+1 to t+H in order\n'
        'event target = long_edge, short_edge, or no_edge by first-touch, with same-bar double-touch folded into no_edge\n\n'
        'Hierarchical contract:\n'
        '- stage 1 sees both long-view and short-view canonical samples and learns tradable_edge vs no_edge\n'
        '- stage 2 sees only tradable events through the same canonical side views and learns edge_for_side vs not_edge_for_side\n'
        '- validation reconstructs event-level long/no-edge/short decisions by combining the tradability gate with paired long/short side scores and searching the gate threshold on balanced event quality\n\n'
        'Rows without full future horizon or a full observation window are discarded.'
    ),
    'runner_id': 'micro_cost_edge_cnn_v3',
    'score_metric': 'directional_edge_macro_f1',
    'score_label': 'Directional edge F1',
    'task_label': 'Hierarchical execution-aware micro cost-edge CNN',
    'feature_set': [
        'mirrored_side_views',
        'execution_microstructure',
        'tradability_gate',
        'conditional_directional_head',
    ],
    'snapshot_cards': [
        {
            'id': 'best_validation_directional_edge_f1',
            'label': 'Best validation directional edge F1',
            'source': 'best_model',
            'metric_path': 'validation.directional_edge_macro_f1',
            'format': 'percent',
            'hint': 'Best promoted hierarchical model measured by the mean F1 of long-edge and short-edge event classes.',
        },
        {
            'id': 'latest_test_directional_edge_f1',
            'label': 'Latest test directional edge F1',
            'source': 'latest_test',
            'metric_path': 'directional_edge_macro_f1',
            'format': 'percent',
            'hint': 'Latest chronological holdout evaluation after combining the tradability gate with paired side scores.',
        },
        {
            'id': 'latest_event_rows',
            'label': 'Event rows',
            'source': 'latest_train',
            'metric_path': 'candidate_summary.event_rows',
            'format': 'integer',
            'hint': 'How many chronological event rows survived feature generation and horizon alignment.',
        },
    ],
    'metric_sections': [
        {
            'id': 'validation_metrics',
            'label': 'Validation metrics',
            'source': 'latest_train',
            'metric_root': 'validation',
            'metrics': [
                {'key': 'directional_edge_macro_f1', 'label': 'Directional edge F1', 'format': 'percent'},
                {'key': 'tradability_f1', 'label': 'Tradability F1', 'format': 'percent'},
                {'key': 'class_long_edge_f1', 'label': 'Long-edge F1', 'format': 'percent'},
                {'key': 'class_short_edge_f1', 'label': 'Short-edge F1', 'format': 'percent'},
                {'key': 'class_no_edge_recall', 'label': 'No-edge recall', 'format': 'percent'},
                {'key': 'gate_class_tradable_edge_f1', 'label': 'Gate edge F1', 'format': 'percent'},
                {'key': 'side_class_edge_for_side_f1', 'label': 'Side-edge F1', 'format': 'percent'},
                {'key': 'threshold', 'label': 'Threshold', 'format': 'score'},
            ],
        },
        {
            'id': 'test_metrics',
            'label': 'Test metrics',
            'source': 'latest_test',
            'metrics': [
                {'key': 'directional_edge_macro_f1', 'label': 'Directional edge F1', 'format': 'percent'},
                {'key': 'tradability_f1', 'label': 'Tradability F1', 'format': 'percent'},
                {'key': 'class_long_edge_f1', 'label': 'Long-edge F1', 'format': 'percent'},
                {'key': 'class_short_edge_f1', 'label': 'Short-edge F1', 'format': 'percent'},
                {'key': 'class_no_edge_recall', 'label': 'No-edge recall', 'format': 'percent'},
                {'key': 'gate_class_tradable_edge_f1', 'label': 'Gate edge F1', 'format': 'percent'},
                {'key': 'side_class_edge_for_side_f1', 'label': 'Side-edge F1', 'format': 'percent'},
                {'key': 'threshold', 'label': 'Threshold', 'format': 'score'},
            ],
        },
    ],
})
NETWORK_REGISTRY['micro_cost_edge_cnn_v3']['defaults'] = dict(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['defaults']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v3']['parameter_schema'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['parameter_schema']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v3']['normalization_targets'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['normalization_targets']
)

NETWORK_REGISTRY['micro_cost_edge_cnn_v4'] = deepcopy(NETWORK_REGISTRY['micro_cost_edge_cnn_v3'])
NETWORK_REGISTRY['micro_cost_edge_cnn_v4'].update({
    'id': 'micro_cost_edge_cnn_v4',
    'label': 'Micro Cost Edge CNN v4',
    'description': (
        'Hierarchical temporal CNN that keeps the v3 tradability and paired-side contract, but adds '
        'candlestick-pattern reversal and continuation scores as contextual inputs for each mirrored event view.'
    ),
    'signature': (
        'Uses the same hierarchical execution-aware micro-cost-edge contract as v3, including the tradability gate and '
        'paired side-direction reconstruction. The material change is the feature surface: each mirrored sample now adds '
        'CandlestickPatterns(5,14) bullish/bearish reversal and continuation scores, mapped into side-relative context '
        'so the model can learn whether classical candle-pattern pressure changes the probability of beating round-trip cost.'
    ),
    'runner_id': 'micro_cost_edge_cnn_v4',
    'task_label': 'Hierarchical execution-aware micro cost-edge CNN with pattern-score context',
    'feature_set': [
        'mirrored_side_views',
        'execution_microstructure',
        'candlestick_pattern_scores',
        'tradability_gate',
        'conditional_directional_head',
    ],
})
NETWORK_REGISTRY['micro_cost_edge_cnn_v4']['defaults'] = dict(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v3']['defaults']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v4']['parameter_schema'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v3']['parameter_schema']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v4']['normalization_targets'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v3']['normalization_targets']
) + [
    {'id': 'mcep_bullish_reversal_score', 'label': 'Bullish reversal score'},
    {'id': 'mcep_bearish_reversal_score', 'label': 'Bearish reversal score'},
    {'id': 'mcep_bullish_continuation_score', 'label': 'Bullish continuation score'},
    {'id': 'mcep_bearish_continuation_score', 'label': 'Bearish continuation score'},
]

NETWORK_REGISTRY['micro_cost_edge_cnn_v5'] = deepcopy(NETWORK_REGISTRY['micro_cost_edge_cnn_v2'])
NETWORK_REGISTRY['micro_cost_edge_cnn_v5'].update({
    'id': 'micro_cost_edge_cnn_v5',
    'label': 'Micro Cost Edge CNN v5',
    'description': (
        'Mirrored side-view micro cost-edge CNN that keeps the v2 side contract but adds candlestick-pattern '
        'reversal and continuation scores as contextual inputs for each canonical side sample.'
    ),
    'signature': (
        'Uses the same mirrored side-view contract as v2: each event is duplicated into a long-side and short-side '
        'canonical view, and the model learns edge_for_side vs not_edge_for_side before reconstructing '
        'long_edge / no_edge / short_edge event predictions. The material change is the feature surface: '
        'CandlestickPatterns(5,14) bullish/bearish reversal and continuation scores are appended as side-relative '
        'context so the direction head can test whether classical candle-pattern pressure improves side ranking '
        'without the hierarchical gate used by v4.'
    ),
    'runner_id': 'micro_cost_edge_cnn_v5',
    'task_label': 'Mirrored micro cost-edge CNN with pattern-score context',
    'feature_set': [
        'mirrored_side_views',
        'execution_microstructure',
        'candlestick_pattern_scores',
        'side_direction_contract',
    ],
})
NETWORK_REGISTRY['micro_cost_edge_cnn_v5']['defaults'] = dict(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['defaults']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v5']['parameter_schema'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['parameter_schema']
)
NETWORK_REGISTRY['micro_cost_edge_cnn_v5']['normalization_targets'] = list(
    NETWORK_REGISTRY['micro_cost_edge_cnn_v2']['normalization_targets']
) + [
    {'id': 'mcep_bullish_reversal_score', 'label': 'Bullish reversal score'},
    {'id': 'mcep_bearish_reversal_score', 'label': 'Bearish reversal score'},
    {'id': 'mcep_bullish_continuation_score', 'label': 'Bullish continuation score'},
    {'id': 'mcep_bearish_continuation_score', 'label': 'Bearish continuation score'},
]


NETWORK_REGISTRY['market_regime_rl_v3'] = deepcopy(NETWORK_REGISTRY['market_regime_rl_v2'])
NETWORK_REGISTRY['market_regime_rl_v3'].update({
    'id': 'market_regime_rl_v3',
    'label': 'Market Regime RL PPO v3',
    'description': (
        'Third PPO variant over OHLCV plus Market Regime features, with lighter reward shaping meant to '
        'discourage one-sided collapse without pushing the policy into mostly-flat behavior.'
    ),
    'signature': (
        'Same observation space as v1 and v2, but v3 keeps the reward-shaping architecture while using '
        'smaller discipline weights. It still includes holding cost, directional imbalance penalty, and '
        'same-side streak penalty, but removes the incentive to stay flat for its own sake.\n\n'
        'Observation vector per candle:\n'
        'open, high, low, close, volume,\n'
        'market_regime_trend_score,\n'
        'market_regime_volatility_score,\n'
        'market_regime_compression_score,\n'
        'market_regime_direction_score,\n'
        'market_regime_stability_score,\n'
        'market_regime_regime_age,\n'
        'market_regime_regime_code'
    ),
    'runner_id': 'market_regime_rl_v3',
    'task_label': 'Lightly balanced OHLCV + Market Regime PPO policy',
})
NETWORK_REGISTRY['market_regime_rl_v3']['snapshot_cards'][0]['hint'] = 'Best promoted v3 model measured on validation episodes.'
NETWORK_REGISTRY['market_regime_rl_v3']['snapshot_cards'][1]['hint'] = 'Latest completed v3 training run validation result.'
NETWORK_REGISTRY['market_regime_rl_v3']['snapshot_cards'][3]['hint'] = 'Most recent test profitability after the v3 reward shaping.'
NETWORK_REGISTRY['market_regime_rl_v3']['defaults'].update({
    'totalTimesteps': 90000,
    'learningRate': 0.0002,
    'gamma': 0.985,
    'holdingCost': 0.000002,
    'flatReward': 0.0,
    'imbalancePenalty': 0.000012,
    'sameSideStreakPenalty': 0.000004,
})


def _enrich_network(network):
    safe_network = deepcopy(network)
    family = NETWORK_FAMILY_REGISTRY.get(safe_network.get('family')) or {}
    architecture = NETWORK_ARCHITECTURE_REGISTRY.get(safe_network.get('architecture_type')) or {}
    safe_network['family_metadata'] = deepcopy(family)
    safe_network['family_label'] = family.get('label') or safe_network.get('family') or 'Unknown'
    safe_network['architecture_metadata'] = deepcopy(architecture)
    safe_network['architecture_label'] = architecture.get('label') or safe_network.get('architecture_type') or 'Unknown'
    safe_network['parameter_groups'] = deepcopy(safe_network.get('parameter_groups') or [])
    safe_network['test_source_options'] = deepcopy(safe_network.get('test_source_options') or [])
    safe_network['snapshot_cards'] = deepcopy(safe_network.get('snapshot_cards') or [])
    safe_network['metric_sections'] = deepcopy(safe_network.get('metric_sections') or [])
    safe_network['normalization_targets'] = deepcopy(safe_network.get('normalization_targets') or [])
    return safe_network


def list_neural_networks():
    return [_enrich_network(network) for network in NETWORK_REGISTRY.values()]


def get_neural_network(network_id: str):
    network = NETWORK_REGISTRY.get(str(network_id or '').strip())
    return _enrich_network(network) if network else None
