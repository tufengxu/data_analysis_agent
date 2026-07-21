"""uvicorn entry: ``python -m data_analysis_agent.server``.

Binds 127.0.0.1 ONLY (roadmap §P1-3.2). Public LAN exposure requires an explicit
unsafe flag + warning, intentionally not provided in Slice 1.
"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run(
        "data_analysis_agent.server.app:create_app",
        host="127.0.0.1",
        port=8000,
        factory=True,
    )


if __name__ == "__main__":
    main()
