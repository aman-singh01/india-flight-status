import sys
from pathlib import Path

# make `import app.*` work when pytest runs from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
