import numpy as np
import pandas as pd

from ...calculator import Calculator


class ChoppinessIndex(Calculator):
    def __init__(self, symbol, period=14):
        super().__init__('ChoppinessIndex', period)

        safe_period = max(2, int(period))
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

        tr_sum = true_range.rolling(window=safe_period, min_periods=safe_period).sum()
        highest_high = high.rolling(window=safe_period, min_periods=safe_period).max()
        lowest_low = low.rolling(window=safe_period, min_periods=safe_period).min()
        safe_denominator = (highest_high - lowest_low).replace(0, np.nan)

        choppiness = 100 * (np.log10(tr_sum / safe_denominator) / np.log10(safe_period))
        trendiness = 100 - choppiness

        symbol.add_feature(self.name, choppiness)
        symbol.add_feature(f'{self.name}_trendiness', trendiness)
