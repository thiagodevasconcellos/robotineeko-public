import numpy as np

from ...calculator import Calculator


class VWAP(Calculator):
    def __init__(self, symbol, price='hlc3'):
        super().__init__('VWAP', price)

        safe_price = str(price or 'hlc3').strip().lower()
        close = symbol['close']
        high = symbol['high']
        low = symbol['low']
        volume = symbol['volume'].fillna(0)

        if safe_price == 'close':
            source_price = close
        elif safe_price == 'ohlc4':
            source_price = (symbol['open'] + high + low + close) / 4
        else:
            source_price = (high + low + close) / 3

        cumulative_volume = volume.cumsum()
        cumulative_turnover = (source_price * volume).cumsum()
        safe_cumulative_volume = cumulative_volume.replace(0, np.nan)

        vwap = cumulative_turnover / safe_cumulative_volume
        distance = close - vwap
        distance_ratio = distance / vwap.replace(0, np.nan)

        symbol.add_feature(self.name, vwap)
        symbol.add_feature(f'{self.name}_distance', distance)
        symbol.add_feature(f'{self.name}_distance_ratio', distance_ratio)
