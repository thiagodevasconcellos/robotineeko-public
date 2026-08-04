import numpy as np
import pandas as pd

from ...calculator import Calculator


class Supertrend(Calculator):
    def __init__(self, symbol, atr_period=10, multiplier=3):
        super().__init__('Supertrend', atr_period, multiplier)

        safe_atr_period = max(1, int(atr_period))
        safe_multiplier = float(multiplier)

        high = symbol['high']
        low = symbol['low']
        close = symbol['close']
        prev_close = close.shift(1)

        true_range = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.ewm(alpha=1 / safe_atr_period, adjust=False).mean()

        hl2 = (high + low) / 2
        basic_upper = hl2 + (safe_multiplier * atr)
        basic_lower = hl2 - (safe_multiplier * atr)

        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()
        direction = np.ones(len(close), dtype=float)
        supertrend = np.full(len(close), np.nan, dtype=float)

        for index in range(1, len(close)):
            previous_close = close.iloc[index - 1]

            if basic_upper.iloc[index] < final_upper.iloc[index - 1] or previous_close > final_upper.iloc[index - 1]:
                final_upper.iloc[index] = basic_upper.iloc[index]
            else:
                final_upper.iloc[index] = final_upper.iloc[index - 1]

            if basic_lower.iloc[index] > final_lower.iloc[index - 1] or previous_close < final_lower.iloc[index - 1]:
                final_lower.iloc[index] = basic_lower.iloc[index]
            else:
                final_lower.iloc[index] = final_lower.iloc[index - 1]

            if close.iloc[index] > final_upper.iloc[index - 1]:
                direction[index] = 1.0
            elif close.iloc[index] < final_lower.iloc[index - 1]:
                direction[index] = -1.0
            else:
                direction[index] = direction[index - 1]

            supertrend[index] = final_lower.iloc[index] if direction[index] > 0 else final_upper.iloc[index]

        supertrend_series = pd.Series(supertrend, index=close.index)
        direction_series = pd.Series(direction, index=close.index)
        supertrend_series.iloc[0] = np.nan

        symbol.add_feature(self.name, supertrend_series)
        symbol.add_feature(f'{self.name}_direction', direction_series)
        symbol.add_feature(f'{self.name}_upper_band', final_upper)
        symbol.add_feature(f'{self.name}_lower_band', final_lower)
