"""
Generate a combined evaluation report across all four questions.

Reads results from:
  - eval_functional_results.json (Q1 + treatment for Q3/Q4)
  - eval_ablation_direct_results.json (Q3)
  - eval_ablation_examples_results.json (Q4)
  - eval_alignment_results.json (Q2)

Usage:
    python scripts/eval_report.py
    python scripts/eval_report.py --output scripts/eval_report.txt
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_OUTPUT = SCRIPTS_DIR / "eval_report.txt"


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _section(title: str) -> str:
    return f"\n{'='*70}\n{title}\n{'='*70}\n"


def generate_report(output_path: Path):
    lines = []
    lines.append("AUTOMOD AGENT V2 — EVALUATION REPORT")
    lines.append("=" * 70)

    # Q1: Compilation accuracy
    functional = _load_json(SCRIPTS_DIR / "eval_functional_results.json")
    if functional:
        m = functional.get("metrics", {})
        ci = functional.get("bootstrap_ci", {})
        lines.append(_section("Q1: Compilation Accuracy (Functional)"))
        lines.append(f"Pairs evaluated: {m.get('n_total', 0)}")
        lines.append(f"Overall accuracy: {m.get('accuracy', 0):.1%}")
        if ci:
            lines.append(f"  95% CI: [{ci.get('accuracy_ci_lower', 0):.1%}, {ci.get('accuracy_ci_upper', 0):.1%}]")
        lines.append(f"Per-subreddit accuracy: {m.get('mean_per_subreddit_accuracy', m.get('mean_per_rule_accuracy', 0)):.1%}")
        lines.append("")
        for v in ["approve", "remove", "review"]:
            lines.append(f"  {v:>8}: P={m.get(f'{v}_precision', 0):.3f}  R={m.get(f'{v}_recall', 0):.3f}  F1={m.get(f'{v}_f1', 0):.3f}")
        lines.append("")
        for d in ["easy", "medium", "hard"]:
            n = m.get(f"n_{d}", 0)
            a = m.get(f"accuracy_{d}", 0)
            if n:
                lines.append(f"  {d:>8}: {a:.1%} (n={n})")

        cm = m.get("confusion_matrix", {})
        if cm:
            lines.append("\n  Confusion Matrix (rows=truth, cols=predicted):")
            verdicts = ["approve", "remove", "review"]
            lines.append(f"  {'':>8}  {'approve':>8} {'remove':>8} {'review':>8}")
            for gt in verdicts:
                row = cm.get(gt, {})
                lines.append(f"  {gt:>8}  {row.get('approve', 0):>8} {row.get('remove', 0):>8} {row.get('review', 0):>8}")
    else:
        lines.append(_section("Q1: Compilation Accuracy"))
        lines.append("  [Not yet evaluated — run eval_functional.py]")

    # Q1 Layer B: Compilation quality (LLM-judge)
    quality = _load_json(SCRIPTS_DIR / "eval_quality_results.json")
    if quality:
        agg = quality.get("aggregate", {})
        llm_scores = agg.get("llm_scores", {})
        struct = agg.get("structural", {})
        lines.append("\n  Compilation Quality (LLM-judge, 1-5 scale):")
        for dim in ["coverage", "logic_specificity", "item_type_fit", "example_quality", "anchor_accuracy"]:
            lines.append(f"    {dim:<22} {llm_scores.get(dim, 0):.2f}")
        lines.append(f"    {'mean':<22} {llm_scores.get('mean', 0):.2f}")
        lines.append("\n  Structural checks (pass rate):")
        for k in ["anchor_in_rule", "non_leaf_action", "regex_compiles", "tree_depth_ok", "example_count_ok", "rubric_nonempty"]:
            lines.append(f"    {k:<22} {struct.get(k, 0):.0%}")

    # Q2: Alignment accuracy
    alignment = _load_json(SCRIPTS_DIR / "eval_alignment_results.json")
    if alignment:
        lines.append(_section("Q2: Alignment/Suggestion Accuracy"))

        # RQ2A: suggest_from_examples (accuracy before/after)
        sfe = alignment.get("suggest-from-examples")
        if sfe:
            comp = sfe.get("comparison", {})
            mc = sfe.get("mcnemar", {})
            cfg = sfe.get("config", {})
            lines.append(f"\n  RQ2A: suggest_from_examples")
            lines.append(f"  Set 1 errors: {cfg.get('total_fn', 0)} FN + {cfg.get('total_fp', 0)} FP")
            lines.append(f"  Baseline accuracy (set 2): {comp.get('baseline_accuracy', 0):.1%}")
            lines.append(f"  Updated accuracy (set 2):  {comp.get('updated_accuracy', 0):.1%}")
            delta = comp.get('accuracy_delta', 0)
            lines.append(f"  Delta:                     {'+' if delta >= 0 else ''}{delta:.1%}")
            lines.append(f"  Improved: {comp.get('n_improved', 0)}, "
                         f"Degraded: {comp.get('n_degraded', 0)}, "
                         f"Unchanged: {comp.get('n_unchanged', 0)}")
            if mc:
                lines.append(f"  McNemar: chi2={mc.get('chi2', 0):.2f} "
                             f"({'sig' if mc.get('significant_p05') else 'n.s.'} at p<.05)")

        # RQ2C: recompile_with_diff (diff vs fresh)
        rcd = alignment.get("recompile-diff")
        if rcd:
            s = rcd.get("summary", {})
            lines.append(f"\n  RQ2C: recompile_with_diff")
            lines.append(f"  Mean diff accuracy:  {s.get('mean_diff_accuracy', 0):.1%}")
            lines.append(f"  Mean fresh accuracy: {s.get('mean_fresh_accuracy', 0):.1%}")
            delta = s.get('mean_accuracy_delta', 0)
            lines.append(f"  Mean delta:          {'+' if delta >= 0 else ''}{delta:.1%}")
            lines.append(f"  Mean agreement rate: {s.get('mean_agreement_rate', 0):.1%}")

            by_type = rcd.get("by_edit_type", {})
            if by_type:
                lines.append(f"\n  By edit type:")
                for et, info in by_type.items():
                    lines.append(
                        f"    {et}: diff={info['diff_accuracy']:.1%} "
                        f"fresh={info['fresh_accuracy']:.1%} "
                        f"agree={info['agreement_rate']:.1%} (n={info['n']})"
                    )
    else:
        lines.append(_section("Q2: Alignment/Suggestion Accuracy"))
        lines.append("  [Not yet evaluated — run eval_alignment.py]")

    # Q3: Checklist vs. direct prompting
    direct = _load_json(SCRIPTS_DIR / "eval_ablation_direct_results.json")
    if functional and direct:
        fm = functional.get("metrics", {})
        dm = direct.get("metrics", {})
        lines.append(_section("Q3: Checklist Pipeline vs. Direct Prompting"))
        lines.append(f"{'Metric':<28} {'Checklist':>10} {'Direct':>10} {'Delta':>10}")
        lines.append("-" * 60)
        for key, label in [
            ("accuracy", "Accuracy"),
            ("mean_per_subreddit_accuracy", "Per-subreddit accuracy"),  # also check mean_per_rule_accuracy for older results
            ("remove_f1", "Remove F1"),
            ("approve_f1", "Approve F1"),
        ]:
            fv = fm.get(key, fm.get("mean_per_rule_accuracy", 0) if key == "mean_per_subreddit_accuracy" else 0)
            dv = dm.get(key, dm.get("mean_per_rule_accuracy", 0) if key == "mean_per_subreddit_accuracy" else 0)
            delta = fv - dv
            sign = "+" if delta >= 0 else ""
            lines.append(f"  {label:<26} {fv:>9.1%} {dv:>9.1%} {sign}{delta:>9.1%}")

        # McNemar
        fr = {r["id"]: r["correct"] for r in functional.get("results", []) if r["predicted"] != "error"}
        dr = {r["id"]: r["correct"] for r in direct.get("results", []) if r["predicted"] != "error"}
        common = set(fr.keys()) & set(dr.keys())
        if common:
            b = sum(1 for i in common if fr[i] and not dr[i])
            c = sum(1 for i in common if not fr[i] and dr[i])
            if (b + c) > 0:
                chi2 = (abs(b - c) - 1) ** 2 / (b + c)
                lines.append(f"\n  McNemar: b={b}, c={c}, chi2={chi2:.2f} ({'sig' if chi2 > 3.84 else 'n.s.'} at p<.05)")
    elif direct:
        dm = direct.get("metrics", {})
        lines.append(_section("Q3: Direct Prompting Baseline"))
        lines.append(f"  Accuracy: {dm.get('accuracy', 0):.1%}")
        lines.append("  [Run eval_functional.py for comparison]")
    else:
        lines.append(_section("Q3: Checklist Pipeline vs. Direct Prompting"))
        lines.append("  [Not yet evaluated — run eval_ablation_direct.py]")

    # Q4: Effect of examples
    examples_abl = _load_json(SCRIPTS_DIR / "eval_ablation_examples_results.json")
    if examples_abl:
        s = examples_abl.get("summary", {})
        lines.append(_section("Q4: Effect of Examples on Moderation"))
        lines.append(f"  With examples:    {s.get('with_examples_accuracy', 0):.1%}")
        lines.append(f"  Without examples: {s.get('without_examples_accuracy', 0):.1%}")
        lines.append(f"  Delta:            {'+' if s.get('accuracy_delta', 0) >= 0 else ''}{s.get('accuracy_delta', 0):.1%}")
        lines.append(f"  Changed verdicts: {s.get('n_changed_verdicts', 0)}")
        lines.append(f"  Improved: {s.get('n_improved_by_examples', 0)}, Degraded: {s.get('n_degraded_by_examples', 0)}")

        mc = examples_abl.get("mcnemar", {})
        if mc:
            lines.append(f"\n  McNemar: chi2={mc.get('chi2', 0):.2f} ({'sig' if mc.get('significant_p05') else 'n.s.'} at p<.05)")

        by_subj = examples_abl.get("by_subjective_count", {})
        if by_subj:
            lines.append("\n  By subjective item count:")
            for k, v in by_subj.items():
                lines.append(f"    {k:<18} n={v['n']:>4}  delta={'+' if v['delta'] >= 0 else ''}{v['delta']:.1%}")
    else:
        lines.append(_section("Q4: Effect of Examples on Moderation"))
        lines.append("  [Not yet evaluated — run eval_ablation_examples.py]")

    # Q5a: Cross-LLM compilation comparison
    cross_llm_compile = _load_json(SCRIPTS_DIR / "eval_cross_llm_compile_results.json")
    if cross_llm_compile:
        lines.append(_section("Q5a: Cross-LLM Compilation Comparison"))
        model_names = list(cross_llm_compile.keys())
        header = f"  {'Model':<28} {'Accuracy':>10} {'Remove F1':>10} {'Per-sub':>10}"
        lines.append(header)
        lines.append("  " + "-" * 60)
        for model_name in model_names:
            m = cross_llm_compile[model_name].get("metrics", {})
            lines.append(
                f"  {model_name:<28} {m.get('accuracy', 0):>9.1%} "
                f"{m.get('remove_f1', 0):>9.3f} "
                f"{m.get('mean_per_subreddit_accuracy', 0):>9.1%}"
            )

        # Quality scores
        has_quality = any(cross_llm_compile[m].get("quality") for m in model_names)
        if has_quality:
            lines.append("\n  Compilation Quality (LLM-judge, 1-5 scale):")
            dims = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality", "mean"]
            q_header = f"  {'Dimension':<22}" + "".join(f"{m:>14}" for m in model_names)
            lines.append(q_header)
            lines.append("  " + "-" * (22 + 14 * len(model_names)))
            for dim in dims:
                row = f"  {dim:<22}"
                for mn in model_names:
                    q = cross_llm_compile[mn].get("quality", {}).get("llm_scores", {})
                    val = q.get(dim, 0)
                    row += f"{val:>14.2f}"
                lines.append(row)
    else:
        lines.append(_section("Q5a: Cross-LLM Compilation Comparison"))
        lines.append("  [Not yet evaluated — run eval_cross_llm.py compile]")

    # Q5b: Cross-LLM evaluation comparison
    cross_llm = _load_json(SCRIPTS_DIR / "eval_cross_llm_results.json")
    if cross_llm:
        lines.append(_section("Q5b: Cross-LLM Evaluation Comparison"))
        model_names = list(cross_llm.keys())
        header = f"  {'Model':<28} {'Accuracy':>10} {'Remove F1':>10} {'Per-sub':>10}"
        lines.append(header)
        lines.append("  " + "-" * 60)
        for model_name in model_names:
            m = cross_llm[model_name].get("metrics", {})
            lines.append(
                f"  {model_name:<28} {m.get('accuracy', 0):>9.1%} "
                f"{m.get('remove_f1', 0):>9.3f} "
                f"{m.get('mean_per_subreddit_accuracy', 0):>9.1%}"
            )

        # Quality scores (if available)
        has_quality = any(cross_llm[m].get("quality") for m in model_names)
        if has_quality:
            lines.append("\n  Compilation Quality (LLM-judge, 1-5 scale):")
            dims = ["coverage", "logical_correctness", "minimality", "clarity", "anchor_accuracy", "example_quality", "mean"]
            q_header = f"  {'Dimension':<22}" + "".join(f"{m:>14}" for m in model_names)
            lines.append(q_header)
            lines.append("  " + "-" * (22 + 14 * len(model_names)))
            for dim in dims:
                row = f"  {dim:<22}"
                for mn in model_names:
                    q = cross_llm[mn].get("quality", {}).get("llm_scores", {})
                    val = q.get(dim, 0)
                    row += f"{val:>14.2f}"
                lines.append(row)

        # Pairwise McNemar
        for i, m1 in enumerate(model_names):
            for m2 in model_names[i+1:]:
                r1 = {r["id"]: r["correct"] for r in cross_llm[m1].get("results", []) if r["predicted"] != "error"}
                r2 = {r["id"]: r["correct"] for r in cross_llm[m2].get("results", []) if r["predicted"] != "error"}
                common = set(r1.keys()) & set(r2.keys())
                if common:
                    b = sum(1 for i in common if r1[i] and not r2[i])
                    c = sum(1 for i in common if not r1[i] and r2[i])
                    if (b + c) > 0:
                        chi2 = (abs(b - c) - 1) ** 2 / (b + c)
                        lines.append(f"\n  McNemar {m1} vs {m2}: b={b}, c={c}, chi2={chi2:.2f} ({'sig' if chi2 > 3.84 else 'n.s.'} at p<.05)")
    else:
        lines.append(_section("Q5b: Cross-LLM Evaluation Comparison"))
        lines.append("  [Not yet evaluated — run eval_cross_llm.py evaluate]")

    # Q6: Community atmosphere ablation
    atmosphere = _load_json(SCRIPTS_DIR / "eval_ablation_atmosphere_results.json")
    if atmosphere:
        s = atmosphere.get("summary", {})
        lines.append(_section("Q6: Effect of Community Atmosphere on Compilation"))
        lines.append(f"  With atmosphere:    {s.get('with_atmosphere_accuracy', 0):.1%}")
        lines.append(f"  Without atmosphere: {s.get('without_atmosphere_accuracy', 0):.1%}")
        lines.append(f"  Delta:              {'+' if s.get('accuracy_delta', 0) >= 0 else ''}{s.get('accuracy_delta', 0):.1%}")
        lines.append(f"  Changed verdicts:   {s.get('n_changed_verdicts', 0)}")
        lines.append(f"  Improved: {s.get('n_improved_by_atmosphere', 0)}, Degraded: {s.get('n_degraded_by_atmosphere', 0)}")

        mc = atmosphere.get("mcnemar", {})
        if mc:
            lines.append(f"\n  McNemar: chi2={mc.get('chi2', 0):.2f} ({'sig' if mc.get('significant_p05') else 'n.s.'} at p<.05)")

        per_sub = atmosphere.get("per_subreddit", {})
        if per_sub:
            lines.append("\n  Per subreddit:")
            for sub, info in sorted(per_sub.items(), key=lambda x: -abs(x[1].get("delta", 0))):
                delta = info.get("delta", 0)
                lines.append(f"    {sub:<25} delta={'+' if delta >= 0 else ''}{delta:.1%} (n={info.get('n', 0)})")

        by_subj = atmosphere.get("by_subjectivity", {})
        if by_subj:
            lines.append("\n  By rule subjectivity:")
            for k, v in by_subj.items():
                lines.append(f"    {k:<25} n={v['n']:>4}  delta={'+' if v['delta'] >= 0 else ''}{v['delta']:.1%}")

        atm_inf = atmosphere.get("atmosphere_influence", {})
        if atm_inf:
            lines.append("\n  Atmosphere influence on checklist items:")
            for sub, info in atm_inf.items():
                lines.append(f"    {sub:<25} {info.get('atmosphere_influenced', 0)}/{info.get('total_items', 0)} ({info.get('pct_influenced', 0):.0%})")

        # Quality scores (if available)
        wq = atmosphere.get("with_atmosphere_quality", {})
        woq = atmosphere.get("without_atmosphere_quality", {})
        if wq and woq:
            wq_llm = wq.get("llm_scores", {})
            woq_llm = woq.get("llm_scores", {})
            lines.append("\n  Compilation Quality (LLM-judge, 1-5 scale):")
            lines.append(f"  {'Dimension':<22} {'With':>8} {'Without':>8} {'Delta':>8}")
            lines.append(f"  {'-'*46}")
            for dim in ["coverage", "logic_specificity", "item_type_fit", "example_quality", "anchor_accuracy", "mean"]:
                wv = wq_llm.get(dim, 0)
                wov = woq_llm.get(dim, 0)
                delta = wv - wov
                sign = "+" if delta >= 0 else ""
                lines.append(f"  {dim:<22} {wv:>8.2f} {wov:>8.2f} {sign}{delta:>7.2f}")
    else:
        lines.append(_section("Q6: Effect of Community Atmosphere on Compilation"))
        lines.append("  [Not yet evaluated — run eval_ablation_atmosphere.py]")

    report = "\n".join(lines) + "\n"
    with open(output_path, "w") as f:
        f.write(report)
    print(report)
    print(f"Report written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate combined evaluation report")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate_report(args.output)


if __name__ == "__main__":
    main()
