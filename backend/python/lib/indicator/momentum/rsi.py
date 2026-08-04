from ...calculator import Calculator


class RSI(Calculator):
    def __init__(self, symbol, price, period=14):
        super().__init__('RSI', price, period)

        delta = symbol[price].diff()

        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        symbol.add_feature(self.name, rsi)