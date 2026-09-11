"""SLP executable entrypoint.

Telegram handlers and runtime orchestration live in ``bot_app``. The parsers,
status logic, persistence, schedule merge and notification services remain
separate reusable modules.
"""

import asyncio

from bot_app import main


if __name__ == "__main__":
    asyncio.run(main())
