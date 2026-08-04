from ...calculator import Calculator


class DonchianChannels(Calculator):
    def __init__(self, symbol, period=20):
        super().__init__('DonchianChannels', period)

        safe_period = max(1, int(period))
        high = symbol['high']
        low = symbol['low']

        upper = high.rolling(window=safe_period, min_periods=safe_period).max()
        lower = low.rolling(window=safe_period, min_periods=safe_period).min()
        middle = (upper + lower) / 2
        width = upper - lower

        symbol.add_feature(f'{self.name}_upper', upper)
        symbol.add_feature(f'{self.name}_middle', middle)
        symbol.add_feature(f'{self.name}_lower', lower)
        symbol.add_feature(f'{self.name}_width', width)
