from ...calculator import Calculator
import pandas as pd

class ATR(Calculator):
    def __init__(self, symbol, period=14):
        super().__init__('ATR', period)

        high = symbol['high']
        low = symbol['low']
        close = symbol['close']

        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(alpha=1 / period, adjust=False).mean()

        symbol.add_feature(self.name, atr)