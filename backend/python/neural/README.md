# Neural Package

Este pacote isola utilitários para criação de features e datasets voltados a redes neurais.

Objetivo atual:
- reutilizar `Symbol`
- reutilizar as classes de indicadores já existentes
- produzir dataframes de features e targets sem acoplamento com o restante do backend

Fluxo sugerido:
1. criar um `NeuralFeatureBuilder`
2. aplicar indicadores e features derivadas
3. transformar em dataset supervisionado com `NeuralDatasetBuilder`

Exemplo rápido:

```python
from neural import NeuralFeatureBuilder, NeuralDatasetBuilder

builder = NeuralFeatureBuilder.from_candles(
    symbol_name='EURUSD',
    timeframe='M5',
    candles=candles,
)

builder.apply_indicator('EMA', ['close', 21])
builder.apply_indicator('RSI', ['close', 14])
builder.add_price_returns(periods=(1, 5, 10))
builder.add_candle_geometry_features()

dataset = NeuralDatasetBuilder(builder.symbol)
frame = dataset.build(
    feature_columns=builder.get_feature_columns(),
    target_source='close',
    horizon=1,
    target_mode='direction',
)
```
