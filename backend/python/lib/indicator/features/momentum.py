from ...calculator import Calculator


class Momentum(Calculator):
    def __init__(self, symbol, price, period=10):
        super().__init__('Momentum', price, period)

        momentum = symbol[price] - symbol[price].shift(period)

        symbol.add_feature(self.name, momentum)