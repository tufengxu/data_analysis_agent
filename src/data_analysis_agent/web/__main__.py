"""Launch the web workbench: python -m data_analysis_agent.web [port] [--host H] [--unsafe]。

Binds 127.0.0.1 ONLY (roadmap §P1-3.2); non-loopback binds require --unsafe.
Positional port kept for backward compatibility (Slice 1 form).
"""

from __future__ import annotations

import argparse


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print("fastapi/uvicorn not installed. Install with: pip install -e '.[web]'")
        raise SystemExit(1) from None
    from .app import create_app

    parser = argparse.ArgumentParser(description="Report workbench (localhost-only)")
    parser.add_argument("port", nargs="?", type=int, default=8000, help="port (8000 default)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (127.0.0.1 default)")
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="explicitly allow a non-loopback bind (LAN exposure — dangerous)",
    )
    args = parser.parse_args()

    from ..server.bind import is_loopback, resolve_bind_host, unsafe_warning

    host = resolve_bind_host(args.host, unsafe=args.unsafe)
    if not is_loopback(host):
        unsafe_warning(host)

    app = create_app()
    print(f"Report Workbench → http://{host}:{args.port}")
    uvicorn.run(app, host=host, port=args.port)


if __name__ == "__main__":
    main()
