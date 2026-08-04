import numpy as np
import pandas as pd

try:
    from .config import RLTrainingConfig
except ImportError:
    from neural.reinforcement.config import RLTrainingConfig


class OfflineTradingEnvironment:
    """
    Simple offline trading environment for RL experiments.

    Actions:
    - 0: flat
    - 1: long
    - 2: short
    """

    def __init__(self, frame: pd.DataFrame, feature_columns: list[str], config: RLTrainingConfig | None = None):
        self.frame = frame.reset_index(drop=True).copy()
        self.feature_columns = list(feature_columns)
        self.config = config or RLTrainingConfig()
        self.position = 0
        self.long_steps = 0
        self.short_steps = 0
        self.same_side_streak = 0
        self.index = 0
        self.done = False

        if 'close' not in self.frame.columns:
            raise ValueError("OfflineTradingEnvironment requires a 'close' column in the frame.")

        self._features = self.frame[self.feature_columns].astype(float).to_numpy(dtype=np.float32)
        self._close = pd.to_numeric(self.frame['close'], errors='coerce').to_numpy(dtype=float)

    @property
    def observation_size(self):
        return len(self.feature_columns) * max(1, int(self.config.observation_window))

    @property
    def action_size(self):
        return 3 if self.config.allow_short else 2

    def reset(self):
        self.position = 0
        self.long_steps = 0
        self.short_steps = 0
        self.same_side_streak = 0
        self.index = max(0, int(self.config.observation_window) - 1)
        self.done = len(self.frame) <= self.index + 1
        return self._get_observation(), self._build_info()

    def step(self, action: int):
        if self.done:
            return self._get_observation(), 0.0, True, self._build_info()

        target_position = self._action_to_position(action)
        current_price = float(self._close[self.index])
        next_price = float(self._close[self.index + 1])

        position_changed = target_position != self.position
        transaction_penalty = self.config.transaction_cost if position_changed else 0.0
        price_return = next_price - current_price
        price_return_ratio = (price_return / current_price) if current_price not in (0.0, -0.0) else 0.0

        if target_position > 0:
            self.long_steps += 1
        elif target_position < 0:
            self.short_steps += 1

        if target_position != 0 and target_position == self.position:
            self.same_side_streak += 1
        elif target_position != 0:
            self.same_side_streak = 1
        else:
            self.same_side_streak = 0

        directional_steps = self.long_steps + self.short_steps
        imbalance_ratio = (
            abs(self.long_steps - self.short_steps) / directional_steps
            if directional_steps > 0
            else 0.0
        )
        same_side_penalty = (
            self.config.same_side_streak_penalty * max(0, self.same_side_streak - 3)
            if target_position != 0
            else 0.0
        )
        imbalance_penalty = (
            self.config.imbalance_penalty * imbalance_ratio
            if target_position != 0
            else 0.0
        )
        holding_penalty = self.config.holding_cost if target_position != 0 else 0.0
        flat_reward = self.config.flat_reward if target_position == 0 else 0.0

        reward = ((target_position * price_return_ratio) * self.config.position_size)
        reward += flat_reward
        reward -= transaction_penalty
        reward -= holding_penalty
        reward -= imbalance_penalty
        reward -= same_side_penalty
        reward *= self.config.reward_scale

        self.position = target_position
        self.index += 1
        self.done = self.index >= len(self.frame) - 1

        info = self._build_info()
        info.update({
            'position_changed': position_changed,
            'reward': reward,
            'price_return': price_return,
            'price_return_ratio': price_return_ratio,
            'imbalance_ratio': imbalance_ratio,
            'same_side_streak': self.same_side_streak,
        })

        return self._get_observation(), float(reward), self.done, info

    def _action_to_position(self, action: int):
        safe_action = int(action)
        if not self.config.allow_short:
            return 0 if safe_action == 0 else 1

        return {
            0: 0,
            1: 1,
            2: -1,
        }.get(safe_action, 0)

    def _get_observation(self):
        window = max(1, int(self.config.observation_window))
        start = max(0, self.index - window + 1)
        slice_ = self._features[start:self.index + 1]

        if len(slice_) < window:
            pad = np.zeros((window - len(slice_), len(self.feature_columns)), dtype=np.float32)
            slice_ = np.vstack([pad, slice_])

        return slice_.reshape(-1).astype(np.float32)

    def _build_info(self):
        return {
            'index': self.index,
            'position': self.position,
            'time': self.frame.iloc[self.index]['time'] if len(self.frame) else None,
        }
