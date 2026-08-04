from ...calculator import Calculator

class IchimokoClouds(Calculator):
    def __init__(self, symbol, tenkan_period=9, kijun_period=26, senkou_b_period=52):
        super().__init__('IchimokoClouds', tenkan_period, kijun_period, senkou_b_period)

        high = symbol.high
        low = symbol.low
        close = symbol.close

        tenkan_sen = (
            high.rolling(window=tenkan_period).max() +
            low.rolling(window=tenkan_period).min()
        ) / 2

        kijun_sen = (
            high.rolling(window=kijun_period).max() +
            low.rolling(window=kijun_period).min()
        ) / 2

        senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun_period)

        senkou_span_b = (
            (
                high.rolling(window=senkou_b_period).max() +
                low.rolling(window=senkou_b_period).min()
            ) / 2
        ).shift(kijun_period)

        chikou_span = close.shift(-kijun_period)

        symbol.add_feature(f'{self.name}_tenkan_sen', tenkan_sen)
        symbol.add_feature(f'{self.name}_kijun_sen', kijun_sen)
        symbol.add_feature(f'{self.name}_senkou_span_a', senkou_span_a)
        symbol.add_feature(f'{self.name}_senkou_span_b', senkou_span_b)
        symbol.add_feature(f'{self.name}_chikou_span', chikou_span)