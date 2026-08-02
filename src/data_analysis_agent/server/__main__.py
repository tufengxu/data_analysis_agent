"""uvicorn entry: ``python -m data_analysis_agent.server``.

Binds 127.0.0.1 ONLY (roadmap §P1-3.2). Public LAN exposure requires the
explicit ``--unsafe`` flag + prominent warning (shared policy in bind.py).
"""

from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="DataAnalysisAgent workbench (localhost-only)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (127.0.0.1 default)")
    parser.add_argument("--port", type=int, default=8000, help="port (8000 default)")
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="explicitly allow a non-loopback bind (LAN exposure — dangerous)",
    )
    args = parser.parse_args()

    from .bind import is_loopback, resolve_bind_host, unsafe_warning

    host = resolve_bind_host(args.host, unsafe=args.unsafe)
    if not is_loopback(host):
        unsafe_warning(host)

    uvicorn.run(
        "data_analysis_agent.server.app:create_app",
        host=host,
        port=args.port,
        factory=True,
    )


if __name__ == "__main__":
    main()
