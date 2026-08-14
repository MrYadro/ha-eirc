"""Second sanitization pass: neuter live JWTs and residual ids. Throwaway."""
import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
TOKENS = {
    "285296017": "900000001",
    "285287292": "900000002",
    "285287291": "900000003",
    "10285296017": "900000001",
    "00000392ZU": "00000999ZU",
}


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


RENAMES = {
    "bill_260771000000001.json": "bill_26071000000001.json",
    "payment_285296017.json": "payment_a.json",
    "payment_285287292.json": "payment_b.json",
    "payment_285287291.json": "payment_c.json",
}

for path in sorted(FIXTURES.glob("*.json")):
    data = json.loads(path.read_text())
    path.write_text(json.dumps(scrub(data), ensure_ascii=False, indent=2))

for old, new in RENAMES.items():
    src, dst = FIXTURES / old, FIXTURES / new
    if src.exists():
        src.rename(dst)
        print(f"renamed {old} -> {new}")

print("done")
