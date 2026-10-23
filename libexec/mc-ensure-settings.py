#!/usr/bin/env python3
"""Keep MC's Tab completion enabled after older sessions save cached settings."""
from pathlib import Path
import re

path = Path.home() / '.config/mc/ini'
if path.is_file():
    before = path.read_text()
    text = before
    for section, key, value in [
        ('Midnight-Commander', 'show_all_if_ambiguous', 'true'),
        ('terminal:general', 'complete', '^i'),
    ]:
        pattern = rf'(?ms)^\[{re.escape(section)}\]\n(.*?)(?=^\[|\Z)'
        match = re.search(pattern, text)
        setting = f'{key}={value}\n'
        if match:
            body = match.group(1)
            key_pattern = rf'(?m)^{re.escape(key)}=.*(?:\n|$)'
            body = re.sub(key_pattern, lambda _: setting, body) if re.search(key_pattern, body) else setting + body
            text = text[:match.start(1)] + body + text[match.end(1):]
        else:
            text += f'\n[{section}]\n{setting}'
    if text != before:
        path.write_text(text)
