import pandas as pd


def _normalize_name_fragment(value):
    safe_value = str(value).strip()
    if safe_value == '':
        return ''

    try:
        numeric = float(safe_value)
    except (TypeError, ValueError):
        return safe_value

    if numeric.is_integer():
        return str(int(numeric))

    return str(numeric)


class Calculator():
    def __init__(self, *args):
        self.name = '_'.join(
            _normalize_name_fragment(arg)
            for arg in args
            if str(arg).strip() != ''
        )
