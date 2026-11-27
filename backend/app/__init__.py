import logging
import os

# configure logging before any module logs at import time (e.g. generated credentials in security.py)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
