from dataclasses import dataclass
import os
import requests
import time
import pandas as pd


BASE_MARKET_COLUMNS = ('time', 'open', 'high', 'low', 'close', 'volume')
BRIDGE_BASE_URL = os.getenv('ROBOTINEEKO_BRIDGE_BASE_URL', 'http://127.0.0.1:8010')


@dataclass(frozen=True, slots=True)
class SymbolSnapshot:
    name: str
    timeframe: str
    bars: int
    total_columns: tuple[str, ...]
    market_columns: tuple[str, ...]
    derived_columns: tuple[str, ...]
    row_count: int


class Symbol():
    def __init__(self, name, timeframe, bars, candles=None, copy_candles=True):
        self.name = name
        self.timeframe = timeframe
        self.bars = bars

        if candles is None:
            self._fetch()
        else:
            if isinstance(candles, pd.DataFrame):
                self.candles = candles.copy() if copy_candles else candles
            else:
                frame = pd.DataFrame(candles)
                self.candles = frame.copy() if copy_candles else frame

    def _fetch(self):
        request = {
            'symbol': self.name,
            'timeframe': self.timeframe,
            'bars': self.bars,
        }

        requests.post(f'{BRIDGE_BASE_URL}/bridge/set-request', json=request)

        while True:
            ready = requests.get(f'{BRIDGE_BASE_URL}/bridge/ready').json()
            if ready['ready']:
                break
            time.sleep(.1)

        candles = requests.get(f'{BRIDGE_BASE_URL}/candles').json()
        self.candles = pd.DataFrame(candles)

    def add_feature(self, name, data):
        self.candles[name] = data

    @property
    def features(self):
        return list(self.candles.columns)

    @property
    def market_columns(self):
        return [column for column in self.candles.columns if column in BASE_MARKET_COLUMNS]

    @property
    def derived_columns(self):
        return [column for column in self.candles.columns if column not in BASE_MARKET_COLUMNS]

    @property
    def indicators(self):
        return list(self.derived_columns)

    def market_frame(self, copy: bool = True):
        columns = [column for column in BASE_MARKET_COLUMNS if column in self.candles.columns]
        frame = self.candles.loc[:, columns]
        return frame.copy() if copy else frame

    def derived_frame(self, copy: bool = True):
        columns = self.derived_columns
        frame = self.candles.loc[:, columns]
        return frame.copy() if copy else frame

    def snapshot(self):
        return SymbolSnapshot(
            name=str(self.name),
            timeframe=str(self.timeframe),
            bars=int(self.bars),
            total_columns=tuple(str(column) for column in self.candles.columns),
            market_columns=tuple(str(column) for column in self.market_columns),
            derived_columns=tuple(str(column) for column in self.derived_columns),
            row_count=int(len(self.candles.index)),
        )

    def __getattr__(self, name):
        return self.candles[name]

    def __getitem__(self, name):
        return self.candles[name]
