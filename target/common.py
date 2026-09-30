import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
QUOTA = int(os.getenv("DATA_QUOTA_BYTES", "8000000"))


def usage_bytes() -> int:
    if not DATA_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in DATA_DIR.rglob("*") if p.is_file())


def check_data() -> None:
    used = usage_bytes()
    if used >= QUOTA:
        raise OSError(f"data quota exceeded: {used} of {QUOTA} bytes used in {DATA_DIR}")


def append_line(name: str, line: str) -> None:
    check_data()
    with open(DATA_DIR / name, "a") as f:
        f.write(line + "\n")
