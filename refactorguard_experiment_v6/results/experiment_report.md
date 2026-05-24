# RefactorGuard-SC experiment report

- Experiment scale: **article_scale**
- Candidates: **554**
- Tasks/functions: **46**
- Safe candidates: **222**
- Semantic regression candidates: **332**
- Split mode: **group_by_task**
- Device: **cuda**
- Train time: **2.13 sec**

## Test metrics

```json
{
  "loss_proxy": NaN,
  "accuracy": 0.5542168674698795,
  "precision": 1.0,
  "recall": 0.0975609756097561,
  "f1": 0.17777777777777778,
  "roc_auc": 0.8850174216027875,
  "average_precision": 0.8829915870306059
}
```

## Main outputs

- `data/candidates.csv`
- `results/model_predictions.csv`
- `results/strategy_comparison.csv`
- `results/ablation_comparison.csv`
- `figures/article_residual_hallucination_rate.png`
- `figures/article_autonomous_correct_completion.png`
- `figures/article_verification_effort.png`
- `artifacts/refactorguard_risk_model_v6.pt`
- `artifacts/refactorguard_preprocessing_v6.pkl`

## Interpretation warning

If `experiment_scale = pilot`, the pipeline is technically valid, but the dataset is too small for a strong final claim. Increase `CONFIG['repo_roots']`, `max_files`, and `max_functions`, or run the same notebook on several repositories.
