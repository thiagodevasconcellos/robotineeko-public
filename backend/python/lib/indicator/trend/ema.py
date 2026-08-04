from ...calculator import Calculator


class EMA(Calculator):
    def __init__(self, symbol, price, period=21):
        super().__init__('EMA', price, period)

        ema = symbol[price].ewm(span=period, adjust=False).mean()

        symbol.add_feature(self.name, ema)