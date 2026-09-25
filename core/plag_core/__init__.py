"""PLAG core: the local brain behind the PLAG desktop assistant."""

import os

# Memory, measured 2026-09-24 on this laptop (22 logical CPUs): numpy's OpenBLAS reserves a work buffer per CPU at
# import, ~676 MB of committed memory for nothing (PLAG does no big matrix maths in numpy). One thread: 726 -> 50 MB.
# Intel MKL's own memory cache (used by the speech model) is turned off too. Both must be set before numpy loads.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_DISABLE_FAST_MM", "1")
# The wake-word model (faster-whisper on CTranslate2) with Intel MKL committed ~735 MB just to load "tiny"
# (measured 2026-09-25: 814 MB, whatever the thread count); without MKL, 161 MB, and 2 s of audio still takes ~0.5 s.
os.environ.setdefault("CT2_USE_MKL", "0")

__version__ = "0.2.0"
