import time
from math import sqrt

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover - optional dependency
    gym = None
    spaces = None

try:
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
except ImportError:  # pragma: no cover - optional dependency
    PPO = None
    BaseCallback = None

try:
    import torch
except ImportError:  # pragma: no cover - optional dependency
    torch = None

try:
    from .config import RLTrainingConfig
    from .environment import OfflineTradingEnvironment
except ImportError:
    from neural.reinforcement.config import RLTrainingConfig
    from neural.reinforcement.environment import OfflineTradingEnvironment


class StableBaselinesTradingEnvAdapter(gym.Env if gym else object):
    metadata = {'render_modes': []}

    def __init__(self, frame, feature_columns, config: RLTrainingConfig | None = None):
        if gym is None or spaces is None:
            raise RuntimeError('gymnasium is required to build the RL environment adapter.')

        super().__init__()
        self._environment = OfflineTradingEnvironment(frame, feature_columns, config=config)
        self.action_space = spaces.Discrete(self._environment.action_size)
        self.observation_space = spaces.Box(
            low=-float('inf'),
            high=float('inf'),
            shape=(self._environment.observation_size,),
            dtype='float32',
        )

    def reset(self, *, seed=None, options=None):
        observation, info = self._environment.reset()
        return observation, info

    def step(self, action):
        observation, reward, terminated, info = self._environment.step(action)
        truncated = False
        return observation, reward, terminated, truncated, info


class CancellationRequestedError(Exception):
    """Raised when a neural job is cancelled by the user."""


class StableBaselinesCancellationCallback(BaseCallback if BaseCallback else object):
    def __init__(self, total_timesteps: int, should_cancel=None, progress_callback=None):
        if BaseCallback is not None:
            super().__init__()
        self._total_timesteps = max(1, int(total_timesteps))
        self._should_cancel = should_cancel
        self._progress_callback = progress_callback
        self._training_started_at = None
        self._last_reported_timesteps = 0
        self._last_reported_at = 0.0
        self._last_logged_bucket = -1

    def _emit_progress(self, force: bool = False):
        if not callable(self._progress_callback):
            return

        current_timesteps = max(0, int(getattr(self, 'num_timesteps', 0)))
        clamped_timesteps = min(self._total_timesteps, current_timesteps)
        current_time = time.time()
        elapsed_seconds = max(0.0, current_time - float(self._training_started_at or current_time))

        should_report = force
        if not should_report:
            timestep_delta = current_timesteps - self._last_reported_timesteps
            time_delta = current_time - self._last_reported_at
            should_report = timestep_delta >= max(100, self._total_timesteps // 200) or time_delta >= 1.0

        if not should_report:
            return

        progress_fraction = min(1.0, clamped_timesteps / self._total_timesteps)
        eta_seconds = None
        throughput = None
        if clamped_timesteps > 0 and elapsed_seconds > 0:
            throughput = clamped_timesteps / elapsed_seconds
            remaining_timesteps = max(0, self._total_timesteps - clamped_timesteps)
            eta_seconds = remaining_timesteps / throughput if throughput > 0 else None

        progress_bucket = min(100, int(progress_fraction * 100))
        if force or progress_bucket > self._last_logged_bucket:
            self._last_logged_bucket = progress_bucket

        self._progress_callback(
            message=None,
            current_timestep=clamped_timesteps,
            total_timesteps=self._total_timesteps,
            progress_fraction=progress_fraction,
            elapsed_seconds=elapsed_seconds,
            eta_seconds=eta_seconds,
            throughput=throughput,
        )
        self._last_reported_timesteps = current_timesteps
        self._last_reported_at = current_time

    def _on_training_start(self):  # pragma: no cover - exercised through SB3 runtime
        self._training_started_at = time.time()
        self._last_reported_at = self._training_started_at
        self._emit_progress(force=True)

    def _on_step(self):  # pragma: no cover - exercised through SB3 runtime
        if callable(self._should_cancel) and self._should_cancel():
            return False
        self._emit_progress()
        return True

    def _on_training_end(self):  # pragma: no cover - exercised through SB3 runtime
        self._emit_progress(force=True)


class StableBaselinesRLTrainer:
    def __init__(self, frame, feature_columns: list[str], config: RLTrainingConfig | None = None):
        self.frame = frame
        self.feature_columns = list(feature_columns)
        self.config = config or RLTrainingConfig()

    def build_env(self):
        return StableBaselinesTradingEnvAdapter(
            self.frame,
            self.feature_columns,
            config=self.config,
        )

    def _build_algorithm_kwargs(self, total_timesteps: int):
        available_rows = max(2, int(len(self.frame)))
        max_rollout_steps = max(32, min(total_timesteps, available_rows - 1))
        rollout_candidates = [512, 256, 128, 64, 32]
        default_n_steps = next(
            (candidate for candidate in rollout_candidates if candidate <= max_rollout_steps),
            max_rollout_steps,
        )
        default_batch_size = min(64, default_n_steps)

        return {
            'device': 'cpu',
            'n_steps': default_n_steps,
            'batch_size': max(2, default_batch_size),
            **(self.config.algorithm_kwargs or {}),
        }

    def train(self, should_cancel=None, progress_callback=None):
        env = self.build_env()
        model_class = get_algorithm_class(self.config.algorithm)
        total_timesteps = max(1, int(self.config.total_timesteps))
        algorithm_kwargs = self._build_algorithm_kwargs(total_timesteps)

        if torch is not None:
            # Keep the desktop responsive while training on the same machine as the UI.
            torch.set_num_threads(1)
            if hasattr(torch, 'set_num_interop_threads'):
                try:
                    torch.set_num_interop_threads(1)
                except RuntimeError:
                    pass

        model = model_class(
            'MlpPolicy',
            env,
            learning_rate=self.config.learning_rate,
            gamma=self.config.gamma,
            seed=self.config.seed,
            verbose=1,
            **algorithm_kwargs,
        )
        if callable(progress_callback):
            n_steps = int(algorithm_kwargs.get('n_steps') or 0)
            batch_size = int(algorithm_kwargs.get('batch_size') or 1)
            progress_callback(
                message=(
                    f'PPO initialized on cpu with n_steps={n_steps} '
                    f'and batch_size={batch_size}.'
                ),
                current_timestep=0,
                total_timesteps=total_timesteps,
                progress_fraction=0.0,
                elapsed_seconds=0.0,
                eta_seconds=None,
                throughput=None,
            )
        callback = (
            StableBaselinesCancellationCallback(
                total_timesteps=total_timesteps,
                should_cancel=should_cancel,
                progress_callback=progress_callback,
            )
            if BaseCallback is not None
            else None
        )
        model.learn(total_timesteps=total_timesteps, callback=callback)
        if callable(should_cancel) and should_cancel():
            raise CancellationRequestedError('Neural job cancelled by user.')
        return model


def get_algorithm_class(algorithm_name: str):
    safe_name = str(algorithm_name or 'PPO').strip().upper()

    if safe_name != 'PPO' or PPO is None:
        if PPO is None:
            raise RuntimeError('stable-baselines3 is required to train the reinforcement learning model.')
        raise ValueError(f'Unsupported RL algorithm for now: {algorithm_name}')

    return PPO


def load_trained_model(path: str, algorithm_name: str = 'PPO'):
    model_class = get_algorithm_class(algorithm_name)
    return model_class.load(path)


def _safe_mean(values):
    return sum(values) / len(values) if values else 0.0


def _safe_std(values):
    if len(values) < 2:
        return 0.0
    mean_value = _safe_mean(values)
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return sqrt(max(0.0, variance))


def _build_rl_step_metrics(step_records: list[dict]):
    if not step_records:
        return {}

    rewards = [float(record.get('reward') or 0.0) for record in step_records]
    positive_rewards = [reward for reward in rewards if reward > 0]
    negative_rewards = [reward for reward in rewards if reward < 0]
    absolute_negative_reward = abs(sum(negative_rewards))
    cumulative_rewards = []
    running_reward = 0.0
    peak_reward = 0.0
    max_drawdown = 0.0

    flat_steps = 0
    long_steps = 0
    short_steps = 0
    position_changes = 0
    directional_decisions = 0
    correct_directional_decisions = 0

    for record in step_records:
        reward = float(record.get('reward') or 0.0)
        running_reward += reward
        cumulative_rewards.append(running_reward)
        peak_reward = max(peak_reward, running_reward)
        max_drawdown = max(max_drawdown, peak_reward - running_reward)

        position = int(record.get('position') or 0)
        if position > 0:
            long_steps += 1
        elif position < 0:
            short_steps += 1
        else:
            flat_steps += 1

        if bool(record.get('position_changed')):
            position_changes += 1

        price_return = float(record.get('price_return') or 0.0)
        if position != 0 and price_return != 0.0:
            directional_decisions += 1
            if (position > 0 and price_return > 0.0) or (position < 0 and price_return < 0.0):
                correct_directional_decisions += 1

    step_count = len(step_records)
    reward_mean = _safe_mean(rewards)
    reward_std = _safe_std(rewards)
    gross_profit = float(sum(positive_rewards))
    gross_loss = float(absolute_negative_reward)

    return {
        'step_count': int(step_count),
        'win_rate': float(len(positive_rewards) / step_count) if step_count else 0.0,
        'loss_rate': float(len(negative_rewards) / step_count) if step_count else 0.0,
        'flat_rate': float(flat_steps / step_count) if step_count else 0.0,
        'long_rate': float(long_steps / step_count) if step_count else 0.0,
        'short_rate': float(short_steps / step_count) if step_count else 0.0,
        'trade_count': int(position_changes),
        'trade_rate': float(position_changes / step_count) if step_count else 0.0,
        'directional_accuracy': float(correct_directional_decisions / directional_decisions) if directional_decisions else 0.0,
        'directional_decisions': int(directional_decisions),
        'gross_profit': gross_profit,
        'gross_loss': float(-gross_loss),
        'profit_factor': float(gross_profit / gross_loss) if gross_loss > 0 else None,
        'average_reward': float(reward_mean),
        'reward_std': float(reward_std),
        'sharpe_like': float(reward_mean / reward_std) if reward_std > 0 else 0.0,
        'max_drawdown': float(max_drawdown),
    }


def evaluate_trained_model(model, frame, feature_columns: list[str], config: RLTrainingConfig | None = None, episodes: int = 1, should_cancel=None, progress_callback=None):
    environment = OfflineTradingEnvironment(frame, feature_columns, config=config)
    episode_rewards = []
    episode_steps = []
    all_step_records = []
    evaluation_started_at = time.time()

    safe_episodes = max(1, int(episodes))

    for episode_index in range(safe_episodes):
        if callable(should_cancel) and should_cancel():
            raise CancellationRequestedError('Neural job cancelled by user.')
        if callable(progress_callback):
            elapsed_seconds = max(0.0, time.time() - evaluation_started_at)
            eta_seconds = None
            if episode_index > 0 and elapsed_seconds > 0:
                throughput = episode_index / elapsed_seconds
                eta_seconds = (safe_episodes - episode_index) / throughput if throughput > 0 else None
            progress_callback(
                current_episode=episode_index,
                total_episodes=safe_episodes,
                elapsed_seconds=elapsed_seconds,
                eta_seconds=eta_seconds,
            )
        observation, _ = environment.reset()
        done = False
        total_reward = 0.0
        total_steps = 0

        while not done:
            if callable(should_cancel) and should_cancel():
                raise CancellationRequestedError('Neural job cancelled by user.')
            action, _ = model.predict(observation, deterministic=True)
            observation, reward, done, info = environment.step(action)
            total_reward += float(reward)
            total_steps += 1
            all_step_records.append({
                'reward': float(reward),
                'position': int(info.get('position') or 0),
                'position_changed': bool(info.get('position_changed')),
                'price_return': float(info.get('price_return') or 0.0),
            })

        episode_rewards.append(total_reward)
        episode_steps.append(total_steps)
        if callable(progress_callback):
            elapsed_seconds = max(0.0, time.time() - evaluation_started_at)
            throughput = (episode_index + 1) / elapsed_seconds if elapsed_seconds > 0 else None
            eta_seconds = (
                (safe_episodes - (episode_index + 1)) / throughput
                if throughput and throughput > 0
                else None
            )
            progress_callback(
                current_episode=episode_index + 1,
                total_episodes=safe_episodes,
                elapsed_seconds=elapsed_seconds,
                eta_seconds=eta_seconds,
                last_episode_reward=float(total_reward),
                last_episode_steps=int(total_steps),
            )

    mean_reward = sum(episode_rewards) / len(episode_rewards) if episode_rewards else 0.0
    total_reward = sum(episode_rewards)
    total_steps = sum(episode_steps)
    step_metrics = _build_rl_step_metrics(all_step_records)

    return {
        'episodes': int(safe_episodes),
        'mean_reward': float(mean_reward),
        'total_reward': float(total_reward),
        'mean_reward_per_step': float(mean_reward / total_steps) if total_steps > 0 else 0.0,
        'total_steps': int(total_steps),
        **step_metrics,
    }
