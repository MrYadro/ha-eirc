"""Second sanitization pass: neuter live JWTs and residual ids. Throwaway."""
import json
import os
import re
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
TOKENS: dict[str, str] = json.loads(os.environ.get("NEUTER_TOKENS", "{}"))


def scrub(value):
    if isinstance(value, str):
        value = JWT_RE.sub("fake.jwt.token", value)
        for real, fake in TOKENS.items():
            value = value.replace(real, fake)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


RENAMES = [
    ("payment_*.json", ("payment_a.json", "payment_b.json", "payment_c.json")),
    ("bill_*.json", ("bill_26071000000001.json",)),
]

for path in sorted(FIXTURES.glob("*.json")):
    data = json.loads(path.read_text())
    path.write_text(json.dumps(scrub(data), ensure_ascii=False, indent=2))

for pattern, targets in RENAMES:
    sources = sorted(p for p in FIXTURES.glob(pattern) if p.name not in targets)
    for src, name in zip(sources, targets):
        dst = FIXTURES / name
        if not dst.exists():
            src.rename(dst)
            print(f"renamed {src.name} -> {name}")

print("done")
