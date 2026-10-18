"""Small local preference: remember the user's BLE device, never a run authorization."""
import json
from pathlib import Path

PATH = Path.home() / 'Library' / 'Application Support' / 'NEJE' / 'settings.json'


def load(path=None):
    try:
        row = json.loads(Path(path or PATH).read_text())
        if isinstance(row, dict) and isinstance(row.get('address'), str) and row['address']:
            return {'address': row['address'], 'name': str(row.get('name') or 'NEJE')}
    except (OSError, ValueError):
        pass
    return None


def save(name, address, path=None):
    target = Path(path or PATH)
    row = {'name': name, 'address': address}
    try:
        previous = json.loads(target.read_text())
        if isinstance(previous, dict):
            if isinstance(previous.get('window'), str):
                row['window'] = previous['window']
            if previous.get('language') in ('zh', 'en'):
                row['language'] = previous['language']
    except (OSError, ValueError):
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, ensure_ascii=False)+'\n')
    temporary.replace(target)


def load_window(path=None):
    """Remembered window geometry, e.g. '720x300+120+80'."""
    try:
        row = json.loads(Path(path or PATH).read_text())
        value = row.get('window') if isinstance(row, dict) else None
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError):
        return None


def save_window(geometry, path=None):
    """Read-modify-write so remembering the window never drops the saved device."""
    target = Path(path or PATH)
    row = {}
    try:
        loaded = json.loads(target.read_text())
        if isinstance(loaded, dict):
            row = loaded
    except (OSError, ValueError):
        pass
    if geometry:
        row['window'] = geometry
    else:
        row.pop('window', None)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, ensure_ascii=False)+'\n')
    temporary.replace(target)


def load_language(path=None):
    """Only an explicit supported preference overrides the system language."""
    try:
        row = json.loads(Path(path or PATH).read_text())
        value = row.get('language') if isinstance(row, dict) else None
        return value if value in ('zh', 'en') else None
    except (OSError, ValueError):
        return None


def save_language(language, path=None):
    if language not in ('zh', 'en'):
        raise ValueError('unsupported UI language')
    target = Path(path or PATH)
    row = {}
    try:
        loaded = json.loads(target.read_text())
        if isinstance(loaded, dict):
            row = loaded
    except (OSError, ValueError):
        pass
    row['language'] = language
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, ensure_ascii=False)+'\n')
    temporary.replace(target)
