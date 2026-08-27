r"""Doctor CLI.

Windows (PowerShell):  .\.venv\Scripts\python.exe -m imaginarium_forge.doctor
Linux / macOS:         python3 -m imaginarium_forge.doctor
JSON output:           ... -m imaginarium_forge.doctor --json
Exit code: 0 when no check FAILED (warn/skip are acceptable), 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from imaginarium_forge import __version__
from imaginarium_forge.config.settings import load_settings
from imaginarium_forge.doctor.checks import has_failure, run_all
from imaginarium_forge.logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imaginarium-forge-doctor")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    results = run_all(settings)

    if args.json:
        print(
            json.dumps(
                {"app_version": __version__, "checks": [asdict(r) for r in results]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"imaginarium-forge doctor (app {__version__})")
        width = max(len(r.name) for r in results)
        for r in results:
            print(f"  {r.name.ljust(width)}  [{r.status.upper():4}]  {r.detail}")
    return 1 if has_failure(results) else 0


if __name__ == "__main__":
    sys.exit(main())
