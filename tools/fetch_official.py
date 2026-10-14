"""Archive public modules referenced by the official page; never execute them."""
import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / 'evidence/web'
BASE = 'https://app.neje99.com/js/'

def fetch(name):
    url = BASE + name
    data = urllib.request.urlopen(url, timeout=20).read()
    (ROOT / name).write_bytes(data)
    return name, data, {'url': url, 'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

def main():
    seen, pending, records = set(), {'script.js'}, []
    while pending:
        batch = sorted(pending - seen)
        if not batch:
            break
        seen.update(batch)
        pending = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for name, data, record in pool.map(fetch, batch):
                records.append(record)
                pending.update(re.findall(r"from ['\"]\./([A-Za-z0-9_-]+\.js)['\"]", data.decode()))
                print(name, len(data), flush=True)
    (ROOT / 'modules-manifest.json').write_text(json.dumps(records, indent=2) + '\n')

if __name__ == '__main__':
    main()
