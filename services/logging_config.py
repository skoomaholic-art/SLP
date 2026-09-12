from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level_name = str(os.getenv("SLP_LOG_LEVEL", "INFO")).strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format=(
            "%(asctime)s %(levelname)s %(name)s "
            "%(message)s"
        ),
    )
