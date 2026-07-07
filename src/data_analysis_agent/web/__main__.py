"""Launch the web workbench: python -m data_analysis_agent.web [port]。"""

from __future__ import annotations

import contextlib
import sys


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print("fastapi/uvicorn not installed. Install with: pip install -e '.[web]'")
        sys.exit(1)
    from .app import create_app

    port = 8000
    if len(sys.argv) > 1:
        with contextlib.suppress(ValueError):
            port = int(sys.argv[1])
    app = create_app()
    print(f"Report Workbench → http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
