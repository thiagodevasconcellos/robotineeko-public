from ...calculator import Calculator


class ROC(Calculator):
    def __init__(self, symbol, price, period=10):
        super().__init__('ROC', price, period)

        roc = ((symbol[price] / symbol[price].shift(period)) - 1) * 100

        symbol.add_feature(self.name, roc)