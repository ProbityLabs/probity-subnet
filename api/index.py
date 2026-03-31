"""
Vercel serverless entry point for the Probity Dashboard.

This re-uses the exact same FastAPI app from scripts/dashboard.py,
just pointing DATA_DIR to the bundled demo data in data/.
"""

import os
import sys

# Add project root to path so we can import scripts.dashboard
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Set DATA_DIR before importing the app (module-level global)
import scripts.dashboard as dashboard
dashboard.DATA_DIR = os.path.join(ROOT, "data")

# Vercel expects an `app` variable
app = dashboard.app
