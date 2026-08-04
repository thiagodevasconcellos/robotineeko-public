from ...calculator import Calculator
import pandas as pd

class ADX(Calculator):
    def __init__(self, symbol, period=14):
        super().__init__('ADX', period)

        high = symbol['high']
        low = symbol['low']
        close = symbol['close']

        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        plus_dm = high - prev_high
        minus_dm = prev_low - low

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = true_range.ewm(alpha=1 / period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr

        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
        adx = dx.ewm(alpha=1 / period, adjust=False).mean()

        symbol.add_feature(f'{self.name}', adx)
        symbol.add_feature(f'{self.name}_plus_di', plus_di)
        symbol.add_feature(f'{self.name}_minus_di', minus_di)
