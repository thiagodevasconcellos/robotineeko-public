import re


SAFE_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z_]\w*$')


def build_expression_safe_identifier(value):
    safe_value = str(value or '').strip()

    if not safe_value:
        return ''

    if SAFE_IDENTIFIER_PATTERN.match(safe_value):
        return safe_value

    encoded_parts = []
    for char in safe_value:
        if char.isalnum() or char == '_':
            encoded_parts.append(char)
            continue
        encoded_parts.append(f'__x{ord(char):02x}__')

    encoded = ''.join(encoded_parts)
    if not encoded:
        return ''

    if encoded[0].isdigit():
        encoded = f'v__{encoded}'

    return f'col__{encoded}'
