import pandas as pd

from ...calculator import Calculator


class KeltnerChannels(Calculator):
    def __init__(self, symbol, price='close', ema_period=20, atr_period=10, multiplier=2):
        super().__init__('KeltnerChannels', price, ema_period, atr_period, multiplier)

        safe_price = str(price or 'close').strip().lower()
        safe_ema_period = max(1, int(ema_period))
        safe_atr_period = max(1, int(atr_period))
        safe_multiplier = float(multiplier)

        price_series = symbol[safe_price]
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
        middle = price_series.ewm(span=safe_ema_period, adjust=False).mean()
        band_distance = atr * safe_multiplier
        upper = middle + band_distance
        lower = middle - band_distance
        width = upper - lower

        symbol.add_feature(f'{self.name}_middle', middle)
        symbol.add_feature(f'{self.name}_upper', upper)
        symbol.add_feature(f'{self.name}_lower', lower)
        symbol.add_feature(f'{self.name}_width', width)
