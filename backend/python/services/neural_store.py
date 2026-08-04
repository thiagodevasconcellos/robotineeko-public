import json
import math
import shutil
import sqlite3
import time
import uuid
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / 'data' / 'neural'
DB_PATH = DATA_DIR / 'neural.db'


def get_artifact_file_info(path: str | None):
    safe_path = str(path or '').strip()
    if not safe_path:
        return {
            'path': None,
            'exists': False,
            'size_bytes': None,
            'filename': '',
        }

    file_path = Path(safe_path)
    exists = file_path.exists() and file_path.is_file()
    size_bytes = file_path.stat().st_size if exists else None

    return {
        'path': safe_path,
        'exists': exists,
        'size_bytes': sanitize_json_value(size_bytes),
        'filename': file_path.name,
        'metadata_path': str(file_path.with_name(f'{file_path.stem}.metadata.json')) if safe_path else None,
        'metadata_exists': file_path.with_name(f'{file_path.stem}.metadata.json').exists() if safe_path else False,
    }


def get_artifact_metadata_path(path: str | None):
    safe_path = str(path or '').strip()
    if not safe_path:
        return None
    file_path = Path(safe_path)
    return file_path.with_name(f'{file_path.stem}.metadata.json')


def write_model_artifact_metadata(path: str | None, metadata: dict | None):
    metadata_path = get_artifact_metadata_path(path)
    if metadata_path is None:
        return None
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(sanitize_json_value(metadata or {}), ensure_ascii=True, allow_nan=False, indent=2),
        encoding='utf-8',
    )
    return str(metadata_path)


def sanitize_json_value(value):
    if isinstance(value, dict):
        return {
            str(key): sanitize_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(item) for item in value]

    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None

    return value


def sanitize_path_fragment(value: str):
    safe = ''.join(
        character if character.isalnum() or character in ('-', '_') else '_'
        for character in str(value or '').strip()
    )
    return safe or 'default'


def ensure_neural_store():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS neural_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                run_type TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                metrics_json TEXT,
                artifact_path TEXT,
                source_run_id TEXT,
                score REAL,
                promoted_to_best INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                note TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                is_baseline INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                started_at REAL NOT NULL,
                ended_at REAL,
                duration_seconds REAL
            )
            '''
        )
        existing_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(neural_runs)").fetchall()
        }
        if 'note' not in existing_columns:
            connection.execute("ALTER TABLE neural_runs ADD COLUMN note TEXT")
        if 'is_favorite' not in existing_columns:
            connection.execute("ALTER TABLE neural_runs ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
        if 'is_baseline' not in existing_columns:
            connection.execute("ALTER TABLE neural_runs ADD COLUMN is_baseline INTEGER NOT NULL DEFAULT 0")
        if 'is_archived' not in existing_columns:
            connection.execute("ALTER TABLE neural_runs ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS neural_best_models (
                user_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                score REAL NOT NULL,
                model_path TEXT NOT NULL,
                metrics_json TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, network_id)
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS neural_presets (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                name TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS neural_network_aliases (
                user_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, network_id)
            )
            '''
        )
        alias_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(neural_network_aliases)").fetchall()
        }
        if 'is_favorite' not in alias_columns:
            connection.execute("ALTER TABLE neural_network_aliases ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
        if 'is_deleted' not in alias_columns:
            connection.execute("ALTER TABLE neural_network_aliases ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
        connection.execute('DROP TABLE IF EXISTS neural_network_state')
        connection.commit()


def get_network_storage_paths(user_id: str, network_id: str):
    safe_user = sanitize_path_fragment(user_id)
    safe_network = sanitize_path_fragment(network_id)
    base_dir = DATA_DIR / safe_user / safe_network
    runs_dir = base_dir / 'runs'
    best_dir = base_dir / 'best'
    runs_dir.mkdir(parents=True, exist_ok=True)
    best_dir.mkdir(parents=True, exist_ok=True)
    return {
        'base_dir': base_dir,
        'runs_dir': runs_dir,
        'best_dir': best_dir,
        'best_model_base_path': best_dir / 'model',
    }


def _row_to_run(row):
    if not row:
        return None

    artifact_info = get_artifact_file_info(row[7])

    return {
        'id': row[0],
        'user_id': row[1],
        'network_id': row[2],
        'run_type': row[3],
        'status': row[4],
        'config': sanitize_json_value(json.loads(row[5] or '{}')),
        'metrics': sanitize_json_value(json.loads(row[6] or '{}')) if row[6] else None,
        'artifact_path': row[7],
        'artifact': artifact_info,
        'source_run_id': row[8],
        'score': sanitize_json_value(row[9]),
        'promoted_to_best': bool(row[10]),
        'error': row[11],
        'note': row[12] or '',
        'is_favorite': bool(row[13]),
        'is_baseline': bool(row[14]),
        'is_archived': bool(row[15]),
        'started_at': sanitize_json_value(row[16]),
        'ended_at': sanitize_json_value(row[17]),
        'duration_seconds': sanitize_json_value(row[18]),
    }


def create_neural_run(*, user_id: str, network_id: str, run_type: str, config: dict, source_run_id: str | None = None):
    ensure_neural_store()
    run_id = uuid.uuid4().hex
    started_at = time.time()

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            INSERT INTO neural_runs (
                id, user_id, network_id, run_type, status, config_json,
                metrics_json, artifact_path, source_run_id, score,
                promoted_to_best, error, note, is_favorite, is_baseline, is_archived, started_at, ended_at, duration_seconds
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, 0, NULL, '', 0, 0, 0, ?, NULL, NULL)
            ''',
            (
                run_id,
                user_id,
                network_id,
                run_type,
                'queued',
                json.dumps(sanitize_json_value(config or {}), ensure_ascii=True, allow_nan=False),
                source_run_id,
                started_at,
            ),
        )
        connection.commit()

    return get_neural_run(run_id)


def get_neural_run(run_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT id, user_id, network_id, run_type, status, config_json,
                   metrics_json, artifact_path, source_run_id, score,
                   promoted_to_best, error, note, is_favorite, is_baseline, is_archived, started_at, ended_at, duration_seconds
            FROM neural_runs
            WHERE id = ?
            ''',
            (str(run_id),),
        ).fetchone()

    return _row_to_run(row)


def list_neural_runs(user_id: str, network_id: str, limit: int = 50):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            '''
            SELECT id, user_id, network_id, run_type, status, config_json,
                   metrics_json, artifact_path, source_run_id, score,
                   promoted_to_best, error, note, is_favorite, is_baseline, is_archived, started_at, ended_at, duration_seconds
            FROM neural_runs
            WHERE user_id = ? AND network_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            ''',
            (user_id, network_id, max(1, int(limit))),
        ).fetchall()

    return [_row_to_run(row) for row in rows]


def update_neural_run(run_id: str, **updates):
    ensure_neural_store()
    allowed = {
        'status', 'config_json', 'metrics_json', 'artifact_path', 'source_run_id',
        'score', 'promoted_to_best', 'error', 'note', 'is_favorite', 'is_baseline', 'is_archived', 'ended_at', 'duration_seconds',
    }
    assignments = []
    values = []

    for key, value in updates.items():
        if key not in allowed:
            continue
        assignments.append(f'{key} = ?')
        values.append(value)

    if not assignments:
        return get_neural_run(run_id)

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            f'UPDATE neural_runs SET {", ".join(assignments)} WHERE id = ?',
            (*values, str(run_id)),
        )
        connection.commit()

    return get_neural_run(run_id)


def set_neural_run_annotations(*, user_id: str, network_id: str, run_id: str, note: str | None = None, is_favorite: bool | None = None, is_baseline: bool | None = None, is_archived: bool | None = None):
    ensure_neural_store()
    run = get_neural_run(run_id)
    if not run or run['user_id'] != user_id or run['network_id'] != network_id:
        return None

    with sqlite3.connect(DB_PATH) as connection:
        if is_baseline is True:
            connection.execute(
                '''
                UPDATE neural_runs
                SET is_baseline = 0
                WHERE user_id = ? AND network_id = ?
                ''',
                (user_id, network_id),
            )

        assignments = []
        values = []

        if note is not None:
            assignments.append('note = ?')
            values.append(str(note).strip())
        if is_favorite is not None:
            assignments.append('is_favorite = ?')
            values.append(1 if is_favorite else 0)
        if is_baseline is not None:
            assignments.append('is_baseline = ?')
            values.append(1 if is_baseline else 0)
        if is_archived is not None:
            assignments.append('is_archived = ?')
            values.append(1 if is_archived else 0)

        if assignments:
            connection.execute(
                f'UPDATE neural_runs SET {", ".join(assignments)} WHERE id = ?',
                (*values, str(run_id)),
            )
        connection.commit()

    return get_neural_run(run_id)


def delete_neural_run(*, user_id: str, network_id: str, run_id: str):
    ensure_neural_store()
    run = get_neural_run(run_id)
    if not run or run['user_id'] != user_id or run['network_id'] != network_id:
        return None

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            'DELETE FROM neural_runs WHERE id = ?',
            (str(run_id),),
        )
        connection.commit()

    return run


def get_best_neural_model(user_id: str, network_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT user_id, network_id, run_id, score, model_path, metrics_json, updated_at
            FROM neural_best_models
            WHERE user_id = ? AND network_id = ?
            ''',
            (user_id, network_id),
        ).fetchone()

    if not row:
        return None

    artifact_info = get_artifact_file_info(row[4])

    return {
        'user_id': row[0],
        'network_id': row[1],
        'run_id': row[2],
        'score': sanitize_json_value(row[3]),
        'model_path': row[4],
        'artifact': artifact_info,
        'metrics': sanitize_json_value(json.loads(row[5] or '{}')) if row[5] else None,
        'updated_at': sanitize_json_value(row[6]),
    }


def delete_best_neural_model(user_id: str, network_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            'DELETE FROM neural_best_models WHERE user_id = ? AND network_id = ?',
            (user_id, network_id),
        )
        connection.commit()


def get_neural_network_alias(user_id: str, network_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT alias, updated_at, is_deleted, is_favorite
            FROM neural_network_aliases
            WHERE user_id = ? AND network_id = ?
            ''',
            (user_id, network_id),
        ).fetchone()

    if not row:
        return None

    if bool(row[2]):
        return {
            'alias': '',
            'updated_at': sanitize_json_value(row[1]),
            'is_deleted': True,
            'is_favorite': bool(row[3]) if len(row) > 3 else False,
        }

    alias = str(row[0] or '').strip()
    return {
        'alias': alias,
        'updated_at': sanitize_json_value(row[1]),
        'is_deleted': False,
        'is_favorite': bool(row[3]) if len(row) > 3 else False,
    }


def set_neural_network_alias(*, user_id: str, network_id: str, alias: str | None = None, is_favorite: bool | None = None):
    ensure_neural_store()
    current_payload = get_neural_network_alias(user_id, network_id) or {}
    safe_alias = (
        str(alias or '').strip()
        if alias is not None
        else str(current_payload.get('alias') or '').strip()
    )
    safe_is_favorite = (
        bool(is_favorite)
        if is_favorite is not None
        else bool(current_payload.get('is_favorite'))
    )

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            INSERT INTO neural_network_aliases (user_id, network_id, alias, is_favorite, is_deleted, updated_at)
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(user_id, network_id) DO UPDATE SET
                alias = excluded.alias,
                is_favorite = excluded.is_favorite,
                is_deleted = 0,
                updated_at = excluded.updated_at
            ''',
            (user_id, network_id, safe_alias, 1 if safe_is_favorite else 0, time.time()),
        )
        connection.commit()

    return get_neural_network_alias(user_id, network_id)


def delete_neural_network_user_state(*, user_id: str, network_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            INSERT INTO neural_network_aliases (user_id, network_id, alias, is_favorite, is_deleted, updated_at)
            VALUES (?, ?, '', 0, 1, ?)
            ON CONFLICT(user_id, network_id) DO UPDATE SET
                alias = '',
                is_favorite = 0,
                is_deleted = 1,
                updated_at = excluded.updated_at
            ''',
            (user_id, network_id, time.time()),
        )
        connection.execute(
            'DELETE FROM neural_presets WHERE user_id = ? AND network_id = ?',
            (user_id, network_id),
        )
        connection.execute(
            'DELETE FROM neural_best_models WHERE user_id = ? AND network_id = ?',
            (user_id, network_id),
        )
        connection.execute(
            'DELETE FROM neural_runs WHERE user_id = ? AND network_id = ?',
            (user_id, network_id),
        )
        connection.commit()

    paths = get_network_storage_paths(user_id, network_id)
    if paths['base_dir'].exists():
        shutil.rmtree(paths['base_dir'], ignore_errors=True)

    return {
        'deleted': True,
    }


def _row_to_preset(row):
    if not row:
        return None

    return {
        'id': row[0],
        'user_id': row[1],
        'network_id': row[2],
        'name': row[3],
        'config': sanitize_json_value(json.loads(row[4] or '{}')),
        'created_at': sanitize_json_value(row[5]),
        'updated_at': sanitize_json_value(row[6]),
    }


def list_neural_presets(user_id: str, network_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            '''
            SELECT id, user_id, network_id, name, config_json, created_at, updated_at
            FROM neural_presets
            WHERE user_id = ? AND network_id = ?
            ORDER BY updated_at DESC, name COLLATE NOCASE ASC
            ''',
            (user_id, network_id),
        ).fetchall()

    return [_row_to_preset(row) for row in rows]


def create_neural_preset(*, user_id: str, network_id: str, name: str, config: dict):
    ensure_neural_store()
    preset_id = uuid.uuid4().hex
    now = time.time()

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            INSERT INTO neural_presets (id, user_id, network_id, name, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                preset_id,
                user_id,
                network_id,
                str(name).strip(),
                json.dumps(sanitize_json_value(config or {}), ensure_ascii=True, allow_nan=False),
                now,
                now,
            ),
        )
        connection.commit()

    return get_neural_preset(user_id, network_id, preset_id)


def get_neural_preset(user_id: str, network_id: str, preset_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT id, user_id, network_id, name, config_json, created_at, updated_at
            FROM neural_presets
            WHERE user_id = ? AND network_id = ? AND id = ?
            ''',
            (user_id, network_id, str(preset_id)),
        ).fetchone()

    return _row_to_preset(row)


def delete_neural_preset(user_id: str, network_id: str, preset_id: str):
    ensure_neural_store()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            DELETE FROM neural_presets
            WHERE user_id = ? AND network_id = ? AND id = ?
            ''',
            (user_id, network_id, str(preset_id)),
        )
        connection.commit()


def update_neural_preset(*, user_id: str, network_id: str, preset_id: str, name: str | None = None, config: dict | None = None):
    ensure_neural_store()
    assignments = ['updated_at = ?']
    values = [time.time()]

    if name is not None:
        assignments.append('name = ?')
        values.append(str(name).strip())
    if config is not None:
        assignments.append('config_json = ?')
        values.append(json.dumps(sanitize_json_value(config or {}), ensure_ascii=True, allow_nan=False))

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            f'''
            UPDATE neural_presets
            SET {", ".join(assignments)}
            WHERE user_id = ? AND network_id = ? AND id = ?
            ''',
            (*values, user_id, network_id, str(preset_id)),
        )
        connection.commit()

    return get_neural_preset(user_id, network_id, preset_id)


def promote_neural_model_to_best(*, user_id: str, network_id: str, run_id: str, source_model_path: str, score: float, metrics: dict | None):
    ensure_neural_store()
    paths = get_network_storage_paths(user_id, network_id)
    destination_base = paths['best_model_base_path']
    source_path = Path(str(source_model_path))
    suffix = ''.join(source_path.suffixes) or source_path.suffix or '.bin'
    destination_path = Path(f'{destination_base}{suffix}')
    source_path_str = str(source_path)
    now = time.time()

    if source_path.exists():
        for existing_path in paths['best_dir'].glob('model*'):
            if existing_path != destination_path and existing_path.exists():
                existing_path.unlink()

    if destination_path != source_path and source_path.exists():
        if destination_path.exists():
            destination_path.unlink()
        shutil.copy2(source_path_str, str(destination_path))

    source_metadata_path = get_artifact_metadata_path(source_model_path)
    destination_metadata_path = get_artifact_metadata_path(str(destination_path))
    if destination_metadata_path and destination_metadata_path.exists():
        destination_metadata_path.unlink()
    if source_metadata_path and source_metadata_path.exists() and destination_metadata_path:
        shutil.copy2(str(source_metadata_path), str(destination_metadata_path))

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            INSERT INTO neural_best_models (user_id, network_id, run_id, score, model_path, metrics_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, network_id) DO UPDATE SET
                run_id = excluded.run_id,
                score = excluded.score,
                model_path = excluded.model_path,
                metrics_json = excluded.metrics_json,
                updated_at = excluded.updated_at
            ''',
            (
                user_id,
                network_id,
                run_id,
                float(score),
                str(destination_path),
                json.dumps(sanitize_json_value(metrics or {}), ensure_ascii=True, allow_nan=False),
                now,
            ),
        )
        connection.commit()

    return get_best_neural_model(user_id, network_id)


def remove_model_artifact(path: str | None):
    if not path:
        return

    file_path = Path(path)
    if file_path.exists():
        file_path.unlink()
    metadata_path = get_artifact_metadata_path(path)
    if metadata_path and metadata_path.exists():
        metadata_path.unlink()


def reset_neural_network_history(*, user_id: str, network_id: str):
    ensure_neural_store()
    paths = get_network_storage_paths(user_id, network_id)

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            'DELETE FROM neural_best_models WHERE user_id = ? AND network_id = ?',
            (user_id, network_id),
        )
        connection.execute(
            'DELETE FROM neural_runs WHERE user_id = ? AND network_id = ?',
            (user_id, network_id),
        )
        connection.commit()

    if paths['runs_dir'].exists():
        shutil.rmtree(paths['runs_dir'], ignore_errors=True)
    if paths['best_dir'].exists():
        shutil.rmtree(paths['best_dir'], ignore_errors=True)

    paths['runs_dir'].mkdir(parents=True, exist_ok=True)
    paths['best_dir'].mkdir(parents=True, exist_ok=True)

    return {
        'runs_deleted': True,
        'artifacts_deleted': True,
    }
