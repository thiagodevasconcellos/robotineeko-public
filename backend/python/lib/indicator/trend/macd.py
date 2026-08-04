from ...calculator import Calculator


class MACD(Calculator):
    def __init__(self, symbol, price, fast_period=12, slow_period=26, signal_period=9):
        super().__init__('MACD', price, fast_period, slow_period, signal_period)

        fast_ema = symbol[price].ewm(span=fast_period, adjust=False).mean()
        slow_ema = symbol[price].ewm(span=slow_period, adjust=False).mean()

        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line

        symbol.add_feature(f'{self.name}_line', macd_line)
        symbol.add_feature(f'{self.name}_signal', signal_line)
        symbol.add_feature(f'{self.name}_histogram', histogram)
