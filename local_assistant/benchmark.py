"""Convenience entry point for the current 20-question local model benchmark."""
import runpy
from pathlib import Path

if __name__=='__main__':
    runpy.run_path(str(Path(__file__).parent/'evaluate_model.py'),run_name='__main__')
