from ...calculator import Calculator


class SMA(Calculator):
    def __init__(self, symbol, price, period=20):
        super().__init__('SMA', price, period)

        sma = symbol[price].rolling(window=period).mean()

        symbol.add_feature(self.name, sma)