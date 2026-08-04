import argparse
import getpass
import sys

try:
    from .services.auth_store import create_user
except ImportError:
    from services.auth_store import create_user


def main():
    parser = argparse.ArgumentParser(
        description='Create an initial Robotineeko user account.'
    )
    parser.add_argument('email', help='User email address')
    args = parser.parse_args()

    email = str(args.email or '').strip()
    if not email:
        print('Email is required.', file=sys.stderr)
        return 1

    password = getpass.getpass('Password: ')
    password_confirm = getpass.getpass('Confirm password: ')

    if password != password_confirm:
        print('Passwords do not match.', file=sys.stderr)
        return 1

    try:
        user = create_user(email, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(f'Created user {user["email"]} ({user["workspace_user_id"]}).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
