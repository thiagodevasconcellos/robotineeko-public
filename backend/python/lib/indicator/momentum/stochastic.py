from ...calculator import Calculator
import pandas as pd

class Stochastic(Calculator):
    def __init__(self, symbol, period=14, smooth_k=3, smooth_d=3):
        super().__init__('Stochastic', period, smooth_k, smooth_d)

        low_min = symbol['low'].rolling(window=period).min()
        high_max = symbol['high'].rolling(window=period).max()

        raw_k = 100 * (symbol['close'] - low_min) / (high_max - low_min)
        k = raw_k.rolling(window=smooth_k).mean()
        d = k.rolling(window=smooth_d).mean()

        symbol.add_feature(f'{self.name}_k', k)
        symbol.add_feature(f'{self.name}_d', d)