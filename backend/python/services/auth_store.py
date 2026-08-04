import hashlib
import hmac
import secrets
import sqlite3
import time

from .workspace_store import DB_PATH, ensure_workspace_store


SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
PBKDF2_ITERATIONS = 390000


def ensure_auth_store():
    ensure_workspace_store()

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS auth_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_login_at REAL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES auth_users(id)
            )
            '''
        )
        connection.commit()


def normalize_email(email: str):
    return str(email or '').strip().lower()


def hash_password(password: str, salt_hex: str | None = None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        str(password or '').encode('utf-8'),
        salt,
        PBKDF2_ITERATIONS,
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, password_hash: str):
    _, calculated = hash_password(password, salt_hex)
    return hmac.compare_digest(calculated, password_hash)


def build_workspace_user_id(user_id: int):
    return f'auth-user:{int(user_id)}'


def create_user(email: str, password: str):
    ensure_auth_store()
    normalized_email = normalize_email(email)
    safe_password = str(password or '')

    if not normalized_email:
        raise ValueError('Email is required.')

    if len(safe_password) < 8:
        raise ValueError('Password must have at least 8 characters.')

    salt_hex, password_hash = hash_password(safe_password)
    now = time.time()

    try:
        with sqlite3.connect(DB_PATH) as connection:
            cursor = connection.execute(
                '''
                INSERT INTO auth_users (email, password_salt, password_hash, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (normalized_email, salt_hex, password_hash, now, None),
            )
            connection.commit()
            user_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError as error:
        raise ValueError('This email is already registered.') from error

    return {
        'id': user_id,
        'email': normalized_email,
        'workspace_user_id': build_workspace_user_id(user_id),
        'created_at': now,
        'last_login_at': None,
    }


def get_user_by_email(email: str):
    ensure_auth_store()
    normalized_email = normalize_email(email)

    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT id, email, created_at, last_login_at, password_salt, password_hash
            FROM auth_users
            WHERE email = ?
            ''',
            (normalized_email,),
        ).fetchone()

    if not row:
        return None

    return {
        'id': int(row[0]),
        'email': str(row[1]),
        'created_at': float(row[2]),
        'last_login_at': float(row[3]) if row[3] is not None else None,
        'password_salt': str(row[4]),
        'password_hash': str(row[5]),
        'workspace_user_id': build_workspace_user_id(int(row[0])),
    }


def get_user_by_id(user_id: int):
    ensure_auth_store()

    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT id, email, created_at, last_login_at
            FROM auth_users
            WHERE id = ?
            ''',
            (int(user_id),),
        ).fetchone()

    if not row:
        return None

    return {
        'id': int(row[0]),
        'email': str(row[1]),
        'created_at': float(row[2]),
        'last_login_at': float(row[3]) if row[3] is not None else None,
        'workspace_user_id': build_workspace_user_id(int(row[0])),
    }


def create_session(user_id: int):
    ensure_auth_store()
    now = time.time()
    token = secrets.token_urlsafe(32)
    expires_at = now + SESSION_TTL_SECONDS

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            INSERT INTO auth_sessions (token, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            ''',
            (token, int(user_id), now, expires_at),
        )
        connection.execute(
            '''
            UPDATE auth_users
            SET last_login_at = ?
            WHERE id = ?
            ''',
            (now, int(user_id)),
        )
        connection.commit()

    return {
        'token': token,
        'created_at': now,
        'expires_at': expires_at,
    }


def get_session(token: str):
    ensure_auth_store()
    safe_token = str(token or '').strip()

    if not safe_token:
        return None

    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            '''
            SELECT token, user_id, created_at, expires_at
            FROM auth_sessions
            WHERE token = ?
            ''',
            (safe_token,),
        ).fetchone()

    if not row:
        return None

    session = {
        'token': str(row[0]),
        'user_id': int(row[1]),
        'created_at': float(row[2]),
        'expires_at': float(row[3]),
    }

    if session['expires_at'] <= time.time():
        delete_session(session['token'])
        return None

    return session


def delete_session(token: str):
    ensure_auth_store()

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            '''
            DELETE FROM auth_sessions
            WHERE token = ?
            ''',
            (str(token or '').strip(),),
        )
        connection.commit()


def authenticate_user(email: str, password: str):
    user = get_user_by_email(email)

    if not user:
        raise ValueError('Invalid email or password.')

    if not verify_password(password, user['password_salt'], user['password_hash']):
        raise ValueError('Invalid email or password.')

    session = create_session(user['id'])
    safe_user = get_user_by_id(user['id'])

    return {
        'user': safe_user,
        'session': session,
    }
