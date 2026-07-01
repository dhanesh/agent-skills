#!/usr/bin/env python3
"""wm — friendly entrypoint for the coding-agent world model.

Thin wrapper over world_model.py so you can run `python3 wm.py observe ...`.
All logic lives in world_model.py (imported, not duplicated).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from world_model import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
