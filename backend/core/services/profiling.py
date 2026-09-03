import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("AIPipeline")


@contextmanager
def profile_stage(stage: str, **context):
    """
    Times a named pipeline stage and logs it as a single, greppable line:
    [PROFILE] stage=download job=<id> duration=12.34s

    Purely observational -- never changes behavior, only measures it. Used
    to find real bottlenecks instead of guessing where time goes.
    """
    start = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - start
        ctx = " ".join(f"{k}={v}" for k, v in context.items())
        logger.info(f"[PROFILE] stage={stage} duration={duration:.2f}s {ctx}".rstrip())
