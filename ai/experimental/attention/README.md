# Attention-Augmented Model (Experimental)

This folder contains an experimental attention-based approach to augmenting the existing LightGBM model. 

## Goal
Apply self-attention over team match history sequences and feed learned embeddings into the existing feature stack. Compare against the current production model before replacement.

## Approach
1. Build per-sample sequences of recent matches for home/away teams (chronologically, no leakage)
2. Encode sequences with transformer self-attention (PyTorch) to get team embeddings
3. Concatenate embeddings to existing 161-dim features
4. Train LightGBM on augmented features
5. Compare via validation holdout (same split as production)

## Files
- `config.py` - Experiment configuration
- `data_utils.py` - Sequence building (no leakage)
- `encoder.py` - PyTorch transformer encoder
- `train_experimental.py` - Training script (runs in GitHub Actions)
- `compare_models.py` - A/B comparison against production model
- `requirements.txt` - Extra deps for experiment

## Training
Training will run on GitHub Actions (as requested). Use `train-model.yml` or a separate workflow.

## Rules
- No changes to production model code until comparison shows improvement
- All experiments isolated in this folder
- Must not break existing inference pipeline
