from lib.indicator import *
from lib.strategy import *
from lib.symbol import Symbol
import time

checkpoint = time.time()
print('Loading symbol...')
symbol = Symbol('EURUSD', 'M1', 10000)
print(f'Done. Elapsed time: {round(time.time() - checkpoint, 2)}s')
checkpoint = time.time()

#print('Calculating indicators...')
#RSI(symbol, 'close', 14)
#print(f'Done. Elapsed time: {round(time.time() - checkpoint, 2)}s')
#checkpoint = time.time()

print('Running strategy...')
strategy = Strategy()
strategy.set_params(
    open_long_condition='RSI_close_14[1] < 25 and RSI_close_14[0] >= 25',
    close_long_condition='RSI_close_14[0] >= 60',
    open_short_condition='RSI_close_14[1] > 75 and RSI_close_14[0] <= 75',
    close_short_condition='RSI_close_14[0] <= 40',

    open_trade_price_long='close[0]',
    open_trade_price_short='close[0]',
    close_trade_price_long='close[0]',
    close_trade_price_short='close[0]',

    stop_gain_long_price='long_open_price[0] + .0012',
    stop_loss_long_price='long_open_price[0] - .0005',
    stop_gain_short_price='short_open_price[0] - .0012',
    stop_loss_short_price='short_open_price[0] + .0005',
    trailing_stop_long_price='long_open_price[0] + opened_order_life[0] * 0.0',
    trailing_stop_short_price='short_open_price[0] - opened_order_life[0] * 0.0',

    allow_invertion=False
)

backtester = Backtester(symbol, strategy)
results = backtester.run()
print(f'Done. Elapsed time: {round(time.time() - checkpoint, 2)}s')

print('Saving results...')
results.to_csv('results.csv')
print('Results saved.\nStrategy statistics:')
for stat, val in backtester.stats.items():
    if type(val) == list:
        print(f'\t{stat}: list()')
    else:    
        print(f'\t{stat}: {val}')
