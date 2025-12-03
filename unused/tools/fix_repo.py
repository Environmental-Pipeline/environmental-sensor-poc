from __future__ import annotations
import re
from pathlib import Path

REPO = Path(".").resolve()
changed = []

def edit_file(path: Path, transformer) -> None:
    """Load, transform, and write file if changed. Makes a .bak backup."""
    if not path.exists() or not path.is_file():
        return
    original = path.read_text(encoding="utf-8")
    modified = transformer(original)
    if modified is None or modified == original:
        return
    # backup
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(original, encoding="utf-8")
    path.write_text(modified, encoding="utf-8")
    changed.append(str(path))

def fix_bare_except(s: str) -> str:
    # Replace "except:" with "except Exception:" when it's a bare except (not "except Exception" already)
    # conservative: only when followed by newline/colon block
    return re.sub(r"(?m)^\s*except\s*:\s*$", "except Exception:", s)

def fix_envdata_concat(s: str) -> str:
    # Replace only the concat line using normalized_new_readings → new_readings
    s2 = re.sub(
        r"polars\.concat\(\s*normalized_new_readings\s*\)\s*if\s*normalized_new_readings\s*else",
        "polars.concat(new_readings) if new_readings else",
        s,
    )
    return s2

def ensure_new_readings_initialized(s: str) -> str:
    # Ensure 'new_readings = []' exists before use; add near first append/concat if missing
    if "new_readings.append(" in s and "new_readings =" not in s:
        # Insert a safe initializer right above the first append
        s = re.sub(
            r"(\n\s*)new_readings\.append\(",
            r"\1new_readings = []\n\1new_readings.append(",
            s,
            count=1,
        )
    return s

def fix_customer_id_logline(s: str) -> str:
    # Remove undefined {customer_id} variable from the f-string if present
    return re.sub(
        r'self\.logger\.error\(\s*f"Failed to process customer\s*\{customer_id\}:\s*\{e\}"\s*\)',
        'self.logger.error(f"Failed to process customer: {e}")',
        s,
    )

def ensure_ruff_ignore_notebooks(pyproject: Path) -> None:
    def _transform(s: str) -> str:
        if "[tool.ruff]" not in s:
            # add a minimal ruff section
            s += (
                "\n\n[tool.ruff]\n"
                "extend-exclude = [\n"
                '  "data/",\n'
                '  ".venv/",\n'
                '  "__pycache__/",\n'
                '  "*.ipynb",\n'
                '  "**/*.ipynb",\n'
                "]\n"
            )
            return s
        # ensure extend-exclude exists and has notebook patterns
        if "extend-exclude" not in s:
            s = re.sub(
                r"(\[tool\.ruff\][^\[]*)",
                r'\1\nextend-exclude = [\n  "data/",\n  ".venv/",\n  "__pycache__/",\n  "*.ipynb",\n  "**/*.ipynb",\n]\n',
                s,
                count=1,
                flags=re.S,
            )
            return s
        # add notebook patterns if missing
        def add_if_missing(block: str) -> str:
            want = ['"*.ipynb"', '"**/*.ipynb"']
            for w in want:
                if w not in block:
                    # inject before closing bracket
                    block = re.sub(r"\]\s*$", f'  {w},\n]\n', block, flags=re.M)
            return block

        # find the extend-exclude array inside ruff section(s)
        s = re.sub(
            r"(\[tool\.ruff\][^\[]*?extend-exclude\s*=\s*\[.*?\])",
            lambda m: add_if_missing(m.group(1)),
            s,
            flags=re.S,
        )
        return s

    if pyproject.exists():
        edit_file(pyproject, _transform)

def ensure_noqa_e402_top(path: Path) -> None:
    def _transform(s: str) -> str:
        first_line = s.splitlines()[0] if s.splitlines() else ""
        if "ruff: noqa: E402" in first_line:
            return s  # already present
        return f"# ruff: noqa: E402\n{s}"
    edit_file(path, _transform)

def main():
    # 1) EnvironmentData.py: fix bare except and the concat line; ensure initializer
    env = REPO / "EnvironmentData.py"
    edit_file(env, fix_bare_except)
    edit_file(env, fix_envdata_concat)
    edit_file(env, ensure_new_readings_initialized)

    # 2) conserv_client.py: fix bare excepts and undefined var in log line
    conserv = REPO / "conserv_client.py"
    edit_file(conserv, fix_bare_except)
    edit_file(conserv, fix_customer_id_logline)

    # 3) ingest_all_sources.py: fix bare except
    ingest = REPO / "ingest_all_sources.py"
    edit_file(ingest, fix_bare_except)

    # 4) Add ruff ignore for notebooks
    ensure_ruff_ignore_notebooks(REPO / "pyproject.toml")

    # 5) Add file-top noqa for E402 to job scripts and scratch-class.py
    targets = [
        REPO / "jobs" / "0-init.py",
        REPO / "jobs" / "1-pull.py",
        REPO / "jobs" / "2-consolidate.py",
        REPO / "scratch-class.py",
    ]
    for t in targets:
        if t.exists():
            ensure_noqa_e402_top(t)

    # Report
    if changed:
        print("Updated files:")
        for p in changed:
            print(" -", p)
        print("\nBackups written as *.bak next to each changed file.")
    else:
        print("No changes made (files already clean or not found).")

if __name__ == "__main__":
    main()
