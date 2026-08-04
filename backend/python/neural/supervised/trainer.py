import json
import time
from pathlib import Path

import numpy as np


class CancellationRequestedError(Exception):
    """Raised when a neural job is cancelled by the user."""


def _softplus(value):
    clipped = np.clip(value, -50, 50)
    return np.log1p(np.exp(clipped))


def _softplus_derivative(value):
    clipped = np.clip(value, -50, 50)
    return 1.0 / (1.0 + np.exp(-clipped))


def _relu(value):
    return np.maximum(value, 0.0)


def _relu_derivative(value):
    return (value > 0.0).astype(float)


def _tanh(value):
    return np.tanh(value)


def _tanh_derivative(value):
    activated = np.tanh(value)
    return 1.0 - np.square(activated)


def _leaky_relu(value):
    return np.where(value > 0.0, value, 0.01 * value)


def _leaky_relu_derivative(value):
    return np.where(value > 0.0, 1.0, 0.01)


def _elu(value):
    return np.where(value > 0.0, value, np.expm1(value))


def _elu_derivative(value):
    return np.where(value > 0.0, 1.0, np.exp(value))


def _sigmoid(value):
    clipped = np.clip(value, -50, 50)
    return 1.0 / (1.0 + np.exp(-clipped))


def _sigmoid_derivative(value):
    activated = _sigmoid(value)
    return activated * (1.0 - activated)


def _linear(value):
    return value


def _linear_derivative(value):
    return np.ones_like(value, dtype=float)


def _tanh_output(value):
    return np.tanh(value)


def _tanh_output_derivative(value):
    activated = np.tanh(value)
    return 1.0 - np.square(activated)


def _softmax(value):
    if value.size == 0:
        return value
    shifted = value - np.max(value, axis=1, keepdims=True)
    exponentiated = np.exp(np.clip(shifted, -50, 50))
    denominator = np.sum(exponentiated, axis=1, keepdims=True)
    denominator[denominator == 0] = 1.0
    return exponentiated / denominator


ACTIVATIONS = {
    'relu': (_relu, _relu_derivative),
    'tanh': (_tanh, _tanh_derivative),
    'leaky_relu': (_leaky_relu, _leaky_relu_derivative),
    'elu': (_elu, _elu_derivative),
    'sigmoid': (_sigmoid, _sigmoid_derivative),
    'linear': (_linear, _linear_derivative),
}


def normalize_hidden_layers(hidden_layers):
    normalized = []
    for index, layer in enumerate(hidden_layers or []):
        if not isinstance(layer, dict):
            continue
        size = max(4, int(layer.get('size', 32)))
        activation = str(layer.get('activation') or 'tanh').strip().lower()
        dropout = max(0.0, min(0.9, float(layer.get('dropout', 0.0) or 0.0)))
        if activation not in ACTIVATIONS:
            activation = 'tanh'
        normalized.append({
            'id': str(layer.get('id') or f'layer_{index + 1}'),
            'size': size,
            'activation': activation,
            'dropout': dropout,
        })
    return normalized or [{'id': 'layer_1', 'size': 32, 'activation': 'tanh', 'dropout': 0.0}]


class BasicFeedForwardRegressor:
    def __init__(self, input_size: int, hidden_layers=None, learning_rate: float = 0.01, seed: int = 42):
        self.input_size = int(input_size)
        self.hidden_layers = normalize_hidden_layers(hidden_layers)
        self.learning_rate = float(learning_rate)
        self.random = np.random.default_rng(int(seed))
        self.weights = []
        self.biases = []
        self.feature_columns = []
        self.feature_mean = None
        self.feature_std = None
        self._initialize_parameters()

    def _initialize_parameters(self):
        self.weights = []
        self.biases = []
        previous_size = self.input_size
        for layer in self.hidden_layers:
            layer_size = int(layer['size'])
            self.weights.append(self.random.normal(0.0, 0.08, size=(previous_size, layer_size)))
            self.biases.append(np.zeros((1, layer_size), dtype=float))
            previous_size = layer_size
        self.output_weight = self.random.normal(0.0, 0.08, size=(previous_size, 1))
        self.output_bias = np.zeros((1, 1), dtype=float)

    def fit_normalizer(self, X):
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        self.feature_mean = mean
        self.feature_std = std

    def transform_features(self, X):
        if self.feature_mean is None or self.feature_std is None:
            raise ValueError('Feature normalizer is not fitted.')
        return (X - self.feature_mean) / self.feature_std

    def _forward(self, X, training: bool = False):
        activations = []
        current = X
        for index, layer in enumerate(self.hidden_layers):
            Z = current @ self.weights[index] + self.biases[index]
            activation_fn, _ = ACTIVATIONS[layer['activation']]
            A = activation_fn(Z)
            dropout_rate = float(layer.get('dropout', 0.0) or 0.0)
            dropout_mask = None
            if training and dropout_rate > 0.0:
                keep_probability = 1.0 - dropout_rate
                dropout_mask = (self.random.random(A.shape) < keep_probability).astype(float)
                A = (A * dropout_mask) / keep_probability
            activations.append({
                'Z': Z,
                'A': A,
                'activation': layer['activation'],
                'dropout': dropout_rate,
                'dropout_mask': dropout_mask,
            })
            current = A

        Z_out = current @ self.output_weight + self.output_bias
        predictions = _tanh_output(Z_out)
        return activations, Z_out, predictions

    def predict_values(self, X):
        _, _, predictions = self._forward(X)
        return predictions

    def evaluate(self, X, y):
        y_true = np.asarray(y, dtype=float).reshape(-1, 1)
        predictions = self.predict_values(X)
        error = predictions - y_true
        signal_mse = float(np.mean(np.square(error))) if len(predictions) else 0.0
        signal_mae = float(np.mean(np.abs(error))) if len(predictions) else 0.0
        signal_rmse = float(np.sqrt(signal_mse))

        actual_signal = y_true[:, 0] if len(y_true) else np.array([], dtype=float)
        predicted_signal = predictions[:, 0] if len(predictions) else np.array([], dtype=float)
        actual_direction = np.sign(actual_signal)
        predicted_direction = np.sign(predicted_signal)
        signal_directional_accuracy = float(np.mean(actual_direction == predicted_direction)) if len(actual_direction) else 0.0

        return {
            'signal_mae': signal_mae,
            'signal_rmse': signal_rmse,
            'signal_directional_accuracy': float(signal_directional_accuracy),
            'mean_predicted_signal': float(np.mean(predicted_signal)) if len(predicted_signal) else 0.0,
            'mean_actual_signal': float(np.mean(actual_signal)) if len(actual_signal) else 0.0,
            'long_bias_rate': float(np.mean(predicted_signal > 0.0)) if len(predicted_signal) else 0.0,
            'short_bias_rate': float(np.mean(predicted_signal < 0.0)) if len(predicted_signal) else 0.0,
        }

    def train(self, X_train, y_train, *, epochs=120, batch_size=64, X_validation=None, y_validation=None, log_callback=None, should_cancel=None):
        sample_count = len(X_train)
        if sample_count == 0:
            raise ValueError('Training dataset is empty.')

        safe_batch_size = max(1, int(batch_size))
        total_epochs = max(1, int(epochs))
        started_at = time.time()

        for epoch_index in range(total_epochs):
            if callable(should_cancel) and should_cancel():
                raise CancellationRequestedError('Neural job cancelled by user.')

            permutation = self.random.permutation(sample_count)
            X_epoch = X_train[permutation]
            y_epoch = y_train[permutation]

            for start in range(0, sample_count, safe_batch_size):
                if callable(should_cancel) and should_cancel():
                    raise CancellationRequestedError('Neural job cancelled by user.')

                end = min(sample_count, start + safe_batch_size)
                X_batch = X_epoch[start:end]
                y_batch = y_epoch[start:end]

                hidden_state, Z_out, predictions = self._forward(X_batch, training=True)
                y_batch = np.asarray(y_batch, dtype=float).reshape(-1, 1)
                dY = (predictions - y_batch) * (2.0 / max(1, len(X_batch)))
                dZ = dY * _tanh_output_derivative(Z_out)

                output_input = hidden_state[-1]['A'] if hidden_state else X_batch
                dW_out = output_input.T @ dZ
                db_out = np.sum(dZ, axis=0, keepdims=True)
                dA = dZ @ self.output_weight.T

                for layer_index in reversed(range(len(self.hidden_layers))):
                    layer_state = hidden_state[layer_index]
                    _, derivative_fn = ACTIVATIONS[layer_state['activation']]
                    dropout_rate = float(layer_state.get('dropout') or 0.0)
                    dropout_mask = layer_state.get('dropout_mask')
                    dA_hidden = dA
                    if dropout_mask is not None and dropout_rate > 0.0:
                        dA_hidden = (dA_hidden * dropout_mask) / max(1e-8, (1.0 - dropout_rate))
                    dZ_hidden = dA_hidden * derivative_fn(layer_state['Z'])
                    previous_output = hidden_state[layer_index - 1]['A'] if layer_index > 0 else X_batch
                    dW = previous_output.T @ dZ_hidden
                    db = np.sum(dZ_hidden, axis=0, keepdims=True)
                    dA = dZ_hidden @ self.weights[layer_index].T
                    self.weights[layer_index] -= self.learning_rate * dW
                    self.biases[layer_index] -= self.learning_rate * db

                self.output_weight -= self.learning_rate * dW_out
                self.output_bias -= self.learning_rate * db_out

            should_log = (
                epoch_index == 0
                or epoch_index == total_epochs - 1
                or ((epoch_index + 1) % max(1, int(total_epochs // 10)) == 0)
            )
            if should_log and callable(log_callback):
                validation_metrics = self.evaluate(X_validation, y_validation) if X_validation is not None and y_validation is not None else {}
                elapsed_seconds = max(0.0, time.time() - started_at)
                epochs_completed = epoch_index + 1
                average_epoch_seconds = elapsed_seconds / max(1, epochs_completed)
                remaining_epochs = max(0, total_epochs - epochs_completed)
                eta_seconds = average_epoch_seconds * remaining_epochs
                log_callback(
                    (
                        f"Epoch {epoch_index + 1}/{total_epochs}"
                        f" · val signal acc {validation_metrics.get('signal_directional_accuracy', 0.0):.4f}"
                        f" · val signal mae {validation_metrics.get('signal_mae', 0.0):.4f}"
                    ),
                    level='info',
                    progress_fraction=epochs_completed / max(1, total_epochs),
                    current_epoch=epochs_completed,
                    total_epochs=total_epochs,
                    elapsed_seconds=elapsed_seconds,
                    eta_seconds=eta_seconds,
                )

    def save(self, path, metadata=None):
        target_path = Path(path).with_suffix('.npz')
        target_path.parent.mkdir(parents=True, exist_ok=True)
        arrays = {
            'feature_mean': self.feature_mean,
            'feature_std': self.feature_std,
            'output_weight': self.output_weight,
            'output_bias': self.output_bias,
            'metadata': json.dumps(metadata or {}, ensure_ascii=True),
        }
        for index, weight in enumerate(self.weights):
            arrays[f'W_hidden_{index}'] = weight
            arrays[f'b_hidden_{index}'] = self.biases[index]
        np.savez(target_path, **arrays)
        return str(target_path)

    @classmethod
    def load(cls, path):
        loaded = np.load(Path(path), allow_pickle=False)
        metadata = json.loads(str(loaded['metadata']))
        feature_mean = loaded['feature_mean']
        hidden_layers = normalize_hidden_layers(metadata.get('hidden_layers') or [])
        model = cls(
            input_size=int(feature_mean.shape[0]),
            hidden_layers=hidden_layers,
        )
        model.feature_mean = feature_mean
        model.feature_std = loaded['feature_std']
        model.output_weight = loaded['output_weight']
        model.output_bias = loaded['output_bias']
        model.weights = []
        model.biases = []
        for index in range(len(hidden_layers)):
            model.weights.append(loaded[f'W_hidden_{index}'])
            model.biases.append(loaded[f'b_hidden_{index}'])
        model.feature_columns = list(metadata.get('feature_columns') or [])
        return model, metadata


class TemporalConvolutionalRegressor:
    def __init__(
        self,
        input_features: int,
        sequence_length: int,
        conv_filters: int = 16,
        kernel_size: int = 3,
        hidden_layers=None,
        learning_rate: float = 0.001,
        seed: int = 42,
    ):
        self.input_features = int(input_features)
        self.sequence_length = int(sequence_length)
        self.conv_filters = max(4, int(conv_filters))
        self.kernel_size = max(2, int(kernel_size))
        if self.kernel_size > self.sequence_length:
            raise ValueError('Kernel size cannot be larger than the observation window.')
        self.hidden_layers = normalize_hidden_layers(hidden_layers)
        self.learning_rate = float(learning_rate)
        self.random = np.random.default_rng(int(seed))
        self.feature_columns = []
        self.feature_mean = None
        self.feature_std = None
        self._initialize_parameters()

    def _initialize_parameters(self):
        self.conv_weight = self.random.normal(
            0.0,
            0.08,
            size=(self.conv_filters, self.kernel_size, self.input_features),
        )
        self.conv_bias = np.zeros((self.conv_filters,), dtype=float)
        self.weights = []
        self.biases = []
        previous_size = self.conv_filters
        for layer in self.hidden_layers:
            layer_size = int(layer['size'])
            self.weights.append(self.random.normal(0.0, 0.08, size=(previous_size, layer_size)))
            self.biases.append(np.zeros((1, layer_size), dtype=float))
            previous_size = layer_size
        self.output_weight = self.random.normal(0.0, 0.08, size=(previous_size, 1))
        self.output_bias = np.zeros((1, 1), dtype=float)

    def fit_normalizer(self, X):
        mean = X.mean(axis=(0, 1))
        std = X.std(axis=(0, 1))
        std[std == 0] = 1.0
        self.feature_mean = mean
        self.feature_std = std

    def transform_features(self, X):
        if self.feature_mean is None or self.feature_std is None:
            raise ValueError('Feature normalizer is not fitted.')
        return (X - self.feature_mean.reshape(1, 1, -1)) / self.feature_std.reshape(1, 1, -1)

    def _forward_conv(self, X):
        sample_count = X.shape[0]
        output_steps = self.sequence_length - self.kernel_size + 1
        conv_linear = np.zeros((sample_count, output_steps, self.conv_filters), dtype=float)

        for step in range(output_steps):
            window = X[:, step:step + self.kernel_size, :]
            for filter_index in range(self.conv_filters):
                conv_linear[:, step, filter_index] = (
                    np.sum(window * self.conv_weight[filter_index], axis=(1, 2))
                    + self.conv_bias[filter_index]
                )

        conv_activated = _relu(conv_linear)
        pooled = conv_activated.mean(axis=1)
        return conv_linear, conv_activated, pooled

    def _forward(self, X, training: bool = False):
        conv_linear, conv_activated, pooled = self._forward_conv(X)
        activations = []
        current = pooled
        for index, layer in enumerate(self.hidden_layers):
            Z = current @ self.weights[index] + self.biases[index]
            activation_fn, _ = ACTIVATIONS[layer['activation']]
            A = activation_fn(Z)
            dropout_rate = float(layer.get('dropout', 0.0) or 0.0)
            dropout_mask = None
            if training and dropout_rate > 0.0:
                keep_probability = 1.0 - dropout_rate
                dropout_mask = (self.random.random(A.shape) < keep_probability).astype(float)
                A = (A * dropout_mask) / keep_probability
            activations.append({
                'Z': Z,
                'A': A,
                'activation': layer['activation'],
                'dropout': dropout_rate,
                'dropout_mask': dropout_mask,
            })
            current = A

        Z_out = current @ self.output_weight + self.output_bias
        predictions = _tanh_output(Z_out)
        return {
            'conv_linear': conv_linear,
            'conv_activated': conv_activated,
            'pooled': pooled,
            'hidden_state': activations,
            'Z_out': Z_out,
            'predictions': predictions,
        }

    def predict_values(self, X):
        return self._forward(X)['predictions']

    def evaluate(self, X, y):
        y_true = np.asarray(y, dtype=float).reshape(-1, 1)
        predictions = self.predict_values(X)
        error = predictions - y_true
        signal_mse = float(np.mean(np.square(error))) if len(predictions) else 0.0
        signal_mae = float(np.mean(np.abs(error))) if len(predictions) else 0.0
        signal_rmse = float(np.sqrt(signal_mse))
        actual_signal = y_true[:, 0] if len(y_true) else np.array([], dtype=float)
        predicted_signal = predictions[:, 0] if len(predictions) else np.array([], dtype=float)
        actual_direction = np.sign(actual_signal)
        predicted_direction = np.sign(predicted_signal)
        signal_directional_accuracy = float(np.mean(actual_direction == predicted_direction)) if len(actual_direction) else 0.0
        return {
            'signal_mae': signal_mae,
            'signal_rmse': signal_rmse,
            'signal_directional_accuracy': float(signal_directional_accuracy),
            'mean_predicted_signal': float(np.mean(predicted_signal)) if len(predicted_signal) else 0.0,
            'mean_actual_signal': float(np.mean(actual_signal)) if len(actual_signal) else 0.0,
            'long_bias_rate': float(np.mean(predicted_signal > 0.0)) if len(predicted_signal) else 0.0,
            'short_bias_rate': float(np.mean(predicted_signal < 0.0)) if len(predicted_signal) else 0.0,
        }

    def train(self, X_train, y_train, *, epochs=120, batch_size=64, X_validation=None, y_validation=None, log_callback=None, should_cancel=None):
        sample_count = len(X_train)
        if sample_count == 0:
            raise ValueError('Training dataset is empty.')

        safe_batch_size = max(1, int(batch_size))
        total_epochs = max(1, int(epochs))
        started_at = time.time()
        output_steps = self.sequence_length - self.kernel_size + 1

        for epoch_index in range(total_epochs):
            if callable(should_cancel) and should_cancel():
                raise CancellationRequestedError('Neural job cancelled by user.')

            permutation = self.random.permutation(sample_count)
            X_epoch = X_train[permutation]
            y_epoch = y_train[permutation]

            for start in range(0, sample_count, safe_batch_size):
                if callable(should_cancel) and should_cancel():
                    raise CancellationRequestedError('Neural job cancelled by user.')

                end = min(sample_count, start + safe_batch_size)
                X_batch = X_epoch[start:end]
                y_batch = np.asarray(y_epoch[start:end], dtype=float).reshape(-1, 1)

                state = self._forward(X_batch, training=True)
                hidden_state = state['hidden_state']
                pooled = state['pooled']
                Z_out = state['Z_out']
                predictions = state['predictions']

                dY = (predictions - y_batch) * (2.0 / max(1, len(X_batch)))
                dZ = dY * _tanh_output_derivative(Z_out)

                output_input = hidden_state[-1]['A'] if hidden_state else pooled
                dW_out = output_input.T @ dZ
                db_out = np.sum(dZ, axis=0, keepdims=True)
                dA = dZ @ self.output_weight.T

                for layer_index in reversed(range(len(self.hidden_layers))):
                    layer_state = hidden_state[layer_index]
                    _, derivative_fn = ACTIVATIONS[layer_state['activation']]
                    dropout_rate = float(layer_state.get('dropout') or 0.0)
                    dropout_mask = layer_state.get('dropout_mask')
                    dA_hidden = dA
                    if dropout_mask is not None and dropout_rate > 0.0:
                        dA_hidden = (dA_hidden * dropout_mask) / max(1e-8, (1.0 - dropout_rate))
                    dZ_hidden = dA_hidden * derivative_fn(layer_state['Z'])
                    previous_output = hidden_state[layer_index - 1]['A'] if layer_index > 0 else pooled
                    dW = previous_output.T @ dZ_hidden
                    db = np.sum(dZ_hidden, axis=0, keepdims=True)
                    dA = dZ_hidden @ self.weights[layer_index].T
                    self.weights[layer_index] -= self.learning_rate * dW
                    self.biases[layer_index] -= self.learning_rate * db

                self.output_weight -= self.learning_rate * dW_out
                self.output_bias -= self.learning_rate * db_out

                dPooled = dA
                dConvActivated = np.repeat((dPooled / output_steps)[:, np.newaxis, :], output_steps, axis=1)
                dConvLinear = dConvActivated * _relu_derivative(state['conv_linear'])
                dConvWeight = np.zeros_like(self.conv_weight)
                dConvBias = np.sum(dConvLinear, axis=(0, 1))

                for step in range(output_steps):
                    window = X_batch[:, step:step + self.kernel_size, :]
                    for filter_index in range(self.conv_filters):
                        dConvWeight[filter_index] += np.sum(
                            window * dConvLinear[:, step, filter_index][:, np.newaxis, np.newaxis],
                            axis=0,
                        )

                self.conv_weight -= self.learning_rate * dConvWeight
                self.conv_bias -= self.learning_rate * dConvBias

            should_log = (
                epoch_index == 0
                or epoch_index == total_epochs - 1
                or ((epoch_index + 1) % max(1, int(total_epochs // 10)) == 0)
            )
            if should_log and callable(log_callback):
                validation_metrics = self.evaluate(X_validation, y_validation) if X_validation is not None and y_validation is not None else {}
                elapsed_seconds = max(0.0, time.time() - started_at)
                epochs_completed = epoch_index + 1
                average_epoch_seconds = elapsed_seconds / max(1, epochs_completed)
                remaining_epochs = max(0, total_epochs - epochs_completed)
                eta_seconds = average_epoch_seconds * remaining_epochs
                log_callback(
                    (
                        f"Epoch {epoch_index + 1}/{total_epochs}"
                        f" · val signal acc {validation_metrics.get('signal_directional_accuracy', 0.0):.4f}"
                        f" · val signal mae {validation_metrics.get('signal_mae', 0.0):.4f}"
                    ),
                    level='info',
                    progress_fraction=epochs_completed / max(1, total_epochs),
                    current_epoch=epochs_completed,
                    total_epochs=total_epochs,
                    elapsed_seconds=elapsed_seconds,
                    eta_seconds=eta_seconds,
                )

    def save(self, path, metadata=None):
        target_path = Path(path).with_suffix('.npz')
        target_path.parent.mkdir(parents=True, exist_ok=True)
        arrays = {
            'feature_mean': self.feature_mean,
            'feature_std': self.feature_std,
            'conv_weight': self.conv_weight,
            'conv_bias': self.conv_bias,
            'output_weight': self.output_weight,
            'output_bias': self.output_bias,
            'metadata': json.dumps(metadata or {}, ensure_ascii=True),
        }
        for index, weight in enumerate(self.weights):
            arrays[f'W_hidden_{index}'] = weight
            arrays[f'b_hidden_{index}'] = self.biases[index]
        np.savez(target_path, **arrays)
        return str(target_path)

    @classmethod
    def load(cls, path):
        loaded = np.load(Path(path), allow_pickle=False)
        metadata = json.loads(str(loaded['metadata']))
        feature_mean = loaded['feature_mean']
        hidden_layers = normalize_hidden_layers(metadata.get('hidden_layers') or [])
        model = cls(
            input_features=int(feature_mean.shape[0]),
            sequence_length=int(metadata.get('observation_window') or 1),
            conv_filters=int(metadata.get('conv_filters') or 16),
            kernel_size=int(metadata.get('kernel_size') or 3),
            hidden_layers=hidden_layers,
        )
        model.feature_mean = feature_mean
        model.feature_std = loaded['feature_std']
        model.conv_weight = loaded['conv_weight']
        model.conv_bias = loaded['conv_bias']
        model.output_weight = loaded['output_weight']
        model.output_bias = loaded['output_bias']
        model.weights = []
        model.biases = []
        for index in range(len(hidden_layers)):
            model.weights.append(loaded[f'W_hidden_{index}'])
            model.biases.append(loaded[f'b_hidden_{index}'])
        model.feature_columns = list(metadata.get('feature_columns') or [])
        return model, metadata


class TemporalConvolutionalClassifier(TemporalConvolutionalRegressor):
    def __init__(
        self,
        input_features: int,
        sequence_length: int,
        class_codes=None,
        class_labels=None,
        conv_filters: int = 16,
        kernel_size: int = 3,
        hidden_layers=None,
        learning_rate: float = 0.001,
        seed: int = 42,
    ):
        normalized_codes = [int(code) for code in (class_codes or [])]
        if not normalized_codes:
            raise ValueError('Temporal CNN classifier requires at least one class code.')
        raw_labels = class_labels or {}
        self.class_codes = normalized_codes
        self.class_labels = {
            int(code): str(raw_labels.get(code) or raw_labels.get(str(code)) or code)
            for code in normalized_codes
        }
        self.num_classes = len(self.class_codes)
        super().__init__(
            input_features=input_features,
            sequence_length=sequence_length,
            conv_filters=conv_filters,
            kernel_size=kernel_size,
            hidden_layers=hidden_layers,
            learning_rate=learning_rate,
            seed=seed,
        )

    def _initialize_parameters(self):
        self.conv_weight = self.random.normal(
            0.0,
            0.08,
            size=(self.conv_filters, self.kernel_size, self.input_features),
        )
        self.conv_bias = np.zeros((self.conv_filters,), dtype=float)
        self.weights = []
        self.biases = []
        previous_size = self.conv_filters
        for layer in self.hidden_layers:
            layer_size = int(layer['size'])
            self.weights.append(self.random.normal(0.0, 0.08, size=(previous_size, layer_size)))
            self.biases.append(np.zeros((1, layer_size), dtype=float))
            previous_size = layer_size
        self.output_weight = self.random.normal(0.0, 0.08, size=(previous_size, self.num_classes))
        self.output_bias = np.zeros((1, self.num_classes), dtype=float)

    def _forward(self, X, training: bool = False):
        conv_linear, conv_activated, pooled = self._forward_conv(X)
        activations = []
        current = pooled
        for index, layer in enumerate(self.hidden_layers):
            Z = current @ self.weights[index] + self.biases[index]
            activation_fn, _ = ACTIVATIONS[layer['activation']]
            A = activation_fn(Z)
            dropout_rate = float(layer.get('dropout', 0.0) or 0.0)
            dropout_mask = None
            if training and dropout_rate > 0.0:
                keep_probability = 1.0 - dropout_rate
                dropout_mask = (self.random.random(A.shape) < keep_probability).astype(float)
                A = (A * dropout_mask) / keep_probability
            activations.append({
                'Z': Z,
                'A': A,
                'activation': layer['activation'],
                'dropout': dropout_rate,
                'dropout_mask': dropout_mask,
            })
            current = A

        logits = current @ self.output_weight + self.output_bias
        probabilities = _softmax(logits)
        return {
            'conv_linear': conv_linear,
            'conv_activated': conv_activated,
            'pooled': pooled,
            'hidden_state': activations,
            'logits': logits,
            'probabilities': probabilities,
        }

    def predict_probabilities(self, X):
        return self._forward(X)['probabilities']

    def predict_classes(self, X):
        probabilities = self.predict_probabilities(X)
        if not len(probabilities):
            return np.array([], dtype=int)
        return np.argmax(probabilities, axis=1)

    def evaluate(self, X, y):
        y_true = np.asarray(y, dtype=int).reshape(-1)
        probabilities = self.predict_probabilities(X)
        if not len(probabilities):
            return {
                'accuracy': 0.0,
                'macro_f1': 0.0,
                'balanced_accuracy': 0.0,
                'directional_accuracy': 0.0,
                'mean_confidence': 0.0,
                'actual_transition_rate': 0.0,
                'predicted_transition_rate': 0.0,
                'class_codes': list(self.class_codes),
                'confusion_matrix': [[0 for _ in self.class_codes] for _ in self.class_codes],
            }

        predicted_indices = np.argmax(probabilities, axis=1)
        confusion = np.zeros((self.num_classes, self.num_classes), dtype=int)
        for actual_index, predicted_index in zip(y_true, predicted_indices):
            if 0 <= int(actual_index) < self.num_classes and 0 <= int(predicted_index) < self.num_classes:
                confusion[int(actual_index), int(predicted_index)] += 1

        actual_codes = np.asarray([self.class_codes[int(index)] for index in y_true], dtype=float)
        predicted_codes = np.asarray([self.class_codes[int(index)] for index in predicted_indices], dtype=float)
        accuracy = float(np.mean(predicted_indices == y_true))
        mean_confidence = float(np.mean(np.max(probabilities, axis=1)))
        directional_accuracy = float(np.mean(np.sign(actual_codes) == np.sign(predicted_codes)))
        actual_transition_rate = float(np.mean(y_true[1:] != y_true[:-1])) if len(y_true) > 1 else 0.0
        predicted_transition_rate = float(np.mean(predicted_indices[1:] != predicted_indices[:-1])) if len(predicted_indices) > 1 else 0.0

        precision_values = []
        recall_values = []
        f1_values = []
        metrics = {
            'accuracy': accuracy,
            'directional_accuracy': directional_accuracy,
            'mean_confidence': mean_confidence,
            'actual_transition_rate': actual_transition_rate,
            'predicted_transition_rate': predicted_transition_rate,
            'class_codes': list(self.class_codes),
            'confusion_matrix': confusion.tolist(),
        }
        for class_index, class_code in enumerate(self.class_codes):
            label_slug = str(self.class_labels.get(int(class_code)) or class_code).strip().lower().replace(' ', '_')
            true_positives = float(confusion[class_index, class_index])
            predicted_positives = float(confusion[:, class_index].sum())
            actual_positives = float(confusion[class_index, :].sum())
            precision = true_positives / predicted_positives if predicted_positives else 0.0
            recall = true_positives / actual_positives if actual_positives else 0.0
            f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
            precision_values.append(precision)
            recall_values.append(recall)
            f1_values.append(f1)
            metrics[f'class_{label_slug}_precision'] = float(precision)
            metrics[f'class_{label_slug}_recall'] = float(recall)
            metrics[f'class_{label_slug}_f1'] = float(f1)
            metrics[f'class_{label_slug}_support'] = int(actual_positives)

        metrics['balanced_accuracy'] = float(np.mean(recall_values)) if recall_values else 0.0
        metrics['macro_f1'] = float(np.mean(f1_values)) if f1_values else 0.0
        return metrics

    def train(self, X_train, y_train, *, epochs=120, batch_size=64, X_validation=None, y_validation=None, class_weights=None, log_callback=None, should_cancel=None):
        sample_count = len(X_train)
        if sample_count == 0:
            raise ValueError('Training dataset is empty.')

        safe_batch_size = max(1, int(batch_size))
        total_epochs = max(1, int(epochs))
        started_at = time.time()
        output_steps = self.sequence_length - self.kernel_size + 1
        class_weight_vector = np.ones((self.num_classes,), dtype=float)
        if class_weights is not None:
            provided = np.asarray(class_weights, dtype=float).reshape(-1)
            if len(provided) != self.num_classes:
                raise ValueError('Temporal CNN classifier class_weights must match the number of classes.')
            provided = np.where(np.isfinite(provided) & (provided > 0.0), provided, 1.0)
            class_weight_vector = provided / max(1e-8, float(np.mean(provided)))

        for epoch_index in range(total_epochs):
            if callable(should_cancel) and should_cancel():
                raise CancellationRequestedError('Neural job cancelled by user.')

            permutation = self.random.permutation(sample_count)
            X_epoch = X_train[permutation]
            y_epoch = np.asarray(y_train, dtype=int)[permutation]

            for start in range(0, sample_count, safe_batch_size):
                if callable(should_cancel) and should_cancel():
                    raise CancellationRequestedError('Neural job cancelled by user.')

                end = min(sample_count, start + safe_batch_size)
                X_batch = X_epoch[start:end]
                y_batch = y_epoch[start:end]

                state = self._forward(X_batch, training=True)
                hidden_state = state['hidden_state']
                pooled = state['pooled']
                probabilities = state['probabilities']

                y_one_hot = np.zeros((len(X_batch), self.num_classes), dtype=float)
                y_one_hot[np.arange(len(X_batch)), y_batch] = 1.0
                sample_weights = class_weight_vector[y_batch].reshape(-1, 1)
                dZ = ((probabilities - y_one_hot) * sample_weights) / max(1, len(X_batch))

                output_input = hidden_state[-1]['A'] if hidden_state else pooled
                dW_out = output_input.T @ dZ
                db_out = np.sum(dZ, axis=0, keepdims=True)
                dA = dZ @ self.output_weight.T

                for layer_index in reversed(range(len(self.hidden_layers))):
                    layer_state = hidden_state[layer_index]
                    _, derivative_fn = ACTIVATIONS[layer_state['activation']]
                    dropout_rate = float(layer_state.get('dropout') or 0.0)
                    dropout_mask = layer_state.get('dropout_mask')
                    dA_hidden = dA
                    if dropout_mask is not None and dropout_rate > 0.0:
                        dA_hidden = (dA_hidden * dropout_mask) / max(1e-8, (1.0 - dropout_rate))
                    dZ_hidden = dA_hidden * derivative_fn(layer_state['Z'])
                    previous_output = hidden_state[layer_index - 1]['A'] if layer_index > 0 else pooled
                    dW = previous_output.T @ dZ_hidden
                    db = np.sum(dZ_hidden, axis=0, keepdims=True)
                    dA = dZ_hidden @ self.weights[layer_index].T
                    self.weights[layer_index] -= self.learning_rate * dW
                    self.biases[layer_index] -= self.learning_rate * db

                self.output_weight -= self.learning_rate * dW_out
                self.output_bias -= self.learning_rate * db_out

                dPooled = dA
                dConvActivated = np.repeat((dPooled / output_steps)[:, np.newaxis, :], output_steps, axis=1)
                dConvLinear = dConvActivated * _relu_derivative(state['conv_linear'])
                dConvWeight = np.zeros_like(self.conv_weight)
                dConvBias = np.sum(dConvLinear, axis=(0, 1))

                for step in range(output_steps):
                    window = X_batch[:, step:step + self.kernel_size, :]
                    for filter_index in range(self.conv_filters):
                        dConvWeight[filter_index] += np.sum(
                            window * dConvLinear[:, step, filter_index][:, np.newaxis, np.newaxis],
                            axis=0,
                        )

                self.conv_weight -= self.learning_rate * dConvWeight
                self.conv_bias -= self.learning_rate * dConvBias

            should_log = (
                epoch_index == 0
                or epoch_index == total_epochs - 1
                or ((epoch_index + 1) % max(1, int(total_epochs // 10)) == 0)
            )
            if should_log and callable(log_callback):
                validation_metrics = self.evaluate(X_validation, y_validation) if X_validation is not None and y_validation is not None else {}
                elapsed_seconds = max(0.0, time.time() - started_at)
                epochs_completed = epoch_index + 1
                average_epoch_seconds = elapsed_seconds / max(1, epochs_completed)
                remaining_epochs = max(0, total_epochs - epochs_completed)
                eta_seconds = average_epoch_seconds * remaining_epochs
                log_callback(
                    (
                        f"Epoch {epoch_index + 1}/{total_epochs}"
                        f" · val macro F1 {validation_metrics.get('macro_f1', 0.0):.4f}"
                        f" · val acc {validation_metrics.get('accuracy', 0.0):.4f}"
                    ),
                    level='info',
                    progress_fraction=epochs_completed / max(1, total_epochs),
                    current_epoch=epochs_completed,
                    total_epochs=total_epochs,
                    elapsed_seconds=elapsed_seconds,
                    eta_seconds=eta_seconds,
                )

    def save(self, path, metadata=None):
        target_path = Path(path).with_suffix('.npz')
        target_path.parent.mkdir(parents=True, exist_ok=True)
        arrays = {
            'feature_mean': self.feature_mean,
            'feature_std': self.feature_std,
            'conv_weight': self.conv_weight,
            'conv_bias': self.conv_bias,
            'output_weight': self.output_weight,
            'output_bias': self.output_bias,
            'metadata': json.dumps({
                **(metadata or {}),
                'class_codes': list(self.class_codes),
                'class_labels': self.class_labels,
            }, ensure_ascii=True),
        }
        for index, weight in enumerate(self.weights):
            arrays[f'W_hidden_{index}'] = weight
            arrays[f'b_hidden_{index}'] = self.biases[index]
        np.savez(target_path, **arrays)
        return str(target_path)

    @classmethod
    def load(cls, path):
        loaded = np.load(Path(path), allow_pickle=False)
        metadata = json.loads(str(loaded['metadata']))
        feature_mean = loaded['feature_mean']
        hidden_layers = normalize_hidden_layers(metadata.get('hidden_layers') or [])
        model = cls(
            input_features=int(feature_mean.shape[0]),
            sequence_length=int(metadata.get('observation_window') or 1),
            class_codes=metadata.get('class_codes') or [],
            class_labels=metadata.get('class_labels') or {},
            conv_filters=int(metadata.get('conv_filters') or 16),
            kernel_size=int(metadata.get('kernel_size') or 3),
            hidden_layers=hidden_layers,
        )
        model.feature_mean = feature_mean
        model.feature_std = loaded['feature_std']
        model.conv_weight = loaded['conv_weight']
        model.conv_bias = loaded['conv_bias']
        model.output_weight = loaded['output_weight']
        model.output_bias = loaded['output_bias']
        model.weights = []
        model.biases = []
        for index in range(len(hidden_layers)):
            model.weights.append(loaded[f'W_hidden_{index}'])
            model.biases.append(loaded[f'b_hidden_{index}'])
        model.feature_columns = list(metadata.get('feature_columns') or [])
        return model, metadata
