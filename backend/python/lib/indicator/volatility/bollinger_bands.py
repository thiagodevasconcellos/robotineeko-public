from ...calculator import Calculator


class BollingerBands(Calculator):
    def __init__(self, symbol, price, period=20, std_dev=2):
        super().__init__('BollingerBands', price, period, std_dev)

        middle = symbol[price].rolling(window=period).mean()
        rolling_std = symbol[price].rolling(window=period).std()

        upper = middle + (rolling_std * std_dev)
        lower = middle - (rolling_std * std_dev)

        symbol.add_feature(f'{self.name}_middle', middle)
        symbol.add_feature(f'{self.name}_upper', upper)
        symbol.add_feature(f'{self.name}_lower', lower)
        symbol.add_feature(f'{self.name}_width', upper - lower)
