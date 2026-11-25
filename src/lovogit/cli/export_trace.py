import argparse
import asyncio
from pathlib import Path

from lovogit.app.project_update.trace_export import export_trace
from lovogit.cfg.project_update import TraceExportSettings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a saved trace without running analysis"
    )
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(export_trace(args.run_dir, TraceExportSettings()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Trace export failed: {type(error).__name__}")
        return 1
    print(f"Trace export: {result['status']}")
    return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
