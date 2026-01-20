import hashlib
import json
import pathlib
import re

data_dir = pathlib.Path("data")
pattern = re.compile(r"^K[A-Z]*_K[A-Z]*\.txt$")

files = {}
for p in sorted(data_dir.glob("*.txt")):
    if not pattern.match(p.name):
        continue
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    files[p.name] = h.hexdigest()

manifest = {"files": files}

out = data_dir / "manifest.json"
out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(f"written {out} with {len(files)} entries")
