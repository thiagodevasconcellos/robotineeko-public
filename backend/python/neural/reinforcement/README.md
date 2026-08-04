# Reinforcement Learning Package

Base de treino para agentes de reinforcement learning usando candles e features do
`VasconcellosEnvelope`, sem integração ainda com o runtime principal do app.

## Features-base

O pipeline atual prepara:
- `open`
- `high`
- `low`
- `close`
- `volume`
- `VasconcellosEnvelope_*_resistance`
- `VasconcellosEnvelope_*_support`
- `VasconcellosEnvelope_*_last_relevant_resistance`
- `VasconcellosEnvelope_*_last_relevant_support`

Os defaults do `VasconcellosEnvelope` seguem o manifesto da aplicação:
- `reference='wick'`
- `span=2`
- `delta_value=0`
- `delta_unit='std_dev'`
- `relevant_support_left=1`
- `relevant_support_right=3`
- `relevant_resistance_left=1`
- `relevant_resistance_right=3`
- `lines='all'`

## Estrutura

- `config.py`: dataclasses de configuração
- `features.py`: pipeline de features para RL
- `environment.py`: ambiente offline simples de trading
- `trainer.py`: wrapper opcional para `stable-baselines3`

## Exemplo rápido

```python
from neural.reinforcement import RLFeatureConfig, RLTrainingConfig
from neural.reinforcement import VasconcellosRLFeaturePipeline, StableBaselinesRLTrainer

feature_config = RLFeatureConfig(
    symbol_name='EURUSD',
    timeframe='M5',
    bars=3000,
)

pipeline = VasconcellosRLFeaturePipeline.from_bridge(feature_config)
training_frame = pipeline.build_training_frame()

trainer = StableBaselinesRLTrainer(
    training_frame,
    feature_columns=pipeline.observation_columns,
    config=RLTrainingConfig(total_timesteps=100_000),
)

model = trainer.train()
```
