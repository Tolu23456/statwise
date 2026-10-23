#!/usr/bin/env python3
"""Compare attention-augmented model vs production model."""
import os, sys, json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, accuracy_score, brier_score_loss, roc_auc_score
import joblib

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from model.trainer import FootballPredictor

def load_production():
    try:
        p = FootballPredictor.load()
        return p
    except Exception as e:
        print(f"Could not load production: {e}")
        return None

def compare():
    os.makedirs("ai/experimental/attention/reports", exist_ok=True)
    results = {}
    
    # Load attention results
    att_path = Path("ai/experimental/attention/reports/comparison_attn.json")
    if att_path.exists():
        with open(att_path) as f:
            results['attention'] = json.load(f)
    
    # Try to get production metrics from model_info
    info_path = Path("ai/data/model_info.json")
    if info_path.exists():
        with open(info_path) as f:
            results['production'] = json.load(f)
    
    # Generate comparison report
    report = {
        'timestamp': str(pd.Timestamp.now()),
        'attention_metrics': results.get('attention'),
        'production_metrics': results.get('production'),
        'recommendation': 'ATTENTION BETTER' if False else 'NEEDS EVALUATION - run on same holdout set'
    }
    
    out = Path("ai/experimental/attention/reports/comparison.json")
    with open(out, 'w') as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"\nReport saved to {out}")

if __name__ == '__main__':
    compare()
