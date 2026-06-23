#!/usr/bin/env python3
"""Launch the Decima heartbeat shell.

    python3 run.py            # warm start (reuses weft.db)
    python3 run.py --fresh    # start from genesis
"""
from decima.shell import main

if __name__ == "__main__":
    main()
