"""
HSN matching evaluation harness.

Treats `synthetic_hsn_data_verified.xlsx` as a labeled corpus of
(Description of Goods -> Correct HSN) and measures, with honest k-fold
(query rows are NEVER in the reference fold), how accurately we can predict
the HSN for a free-text line-item description by matching it against a
reference "DB" of descriptions.

Run:  python3 scripts/hsn_match_eval.py
"""
from __future__ import annotations

import re
import sysxxx
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics.pairwise import linear_kernel

XLSX = Path(__file__).resolve().parent.parent / "synthetic_hsn_data_verified.xlsx"
N_SPLITS = 5
SEED = 42

# ── normalization (mirrors backend_ocr/app/main.py _sap_norm, abbrev-expanded) ──
_ABBREV = {
    r"\bc\.?i\b": "cast iron", r"\bm\.?s\b": "mild steel", r"\bs\.?s\b": "stainless steel",
    r"\bnb\b": "nominal bore", r"\bsch\b": "schedule", r"\bod\b": "outer diameter",
    r"\bid\b": "inner diameter", r"\bcs\b": "carbon steel", r"\bsdr\b": "ratio",
}
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def norm(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r'(?<=\d)"', " inch ", t)
    for pat, rep in _ABBREV.items():
        t = re.sub(pat, rep, t)
    t = re.sub(r"[^a-z0-9. ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def nums(text: str) -> set:
    return {float(x) for x in _NUM_RE.findall(text or "")}


def load():
    df = pd.read_excel(XLSX)
    df = df.rename(columns={"Description of Goods": "desc", "Correct HSN": "hsn",
                            "HSN/SAC": "given_hsn", "Label": "label",
                            "Verify Status": "status"})
    df["hsn"] = df["hsn"].astype(str).str.strip()
    df["given_hsn"] = df["given_hsn"].astype(str).str.strip()
    df["ndesc"] = df["desc"].map(norm)
    df["nums"] = df["desc"].map(nums)
    return df.reset_index(drop=True)


# ── matchers: each takes (train_df, test_df) -> array of predicted HSN ──────────
def m_given(train, test):
    """Trivial baseline: trust the HSN already on the invoice."""
    return test["given_hsn"].values


def m_fuzzy(train, test):
    """rapidfuzz token_set_ratio NN over reference descriptions."""
    ref = train["ndesc"].tolist()
    ref_hsn = train["hsn"].tolist()
    out = []
    for q in test["ndesc"]:
        m = process.extractOne(q, ref, scorer=fuzz.token_set_ratio)
        out.append(ref_hsn[m[2]])
    return np.array(out)


def m_tfidf(train, test, k=5):
    """Char n-gram TF-IDF + cosine, majority vote over top-k neighbours."""
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
    X = vec.fit_transform(train["ndesc"])
    Q = vec.transform(test["ndesc"])
    sims = linear_kernel(Q, X)
    ref_hsn = train["hsn"].values
    out = []
    for row in sims:
        idx = np.argpartition(row, -k)[-k:]
        votes = {}
        for i in idx:
            votes[ref_hsn[i]] = votes.get(ref_hsn[i], 0.0) + row[i]
        out.append(max(votes, key=votes.get))
    return np.array(out)


def m_hybrid(train, test, k=7):
    """TF-IDF cosine candidate vote, blended with a numeric-overlap bonus so the
    right product TYPE wins and same-spec rows are preferred. Confidence = best
    blended weight; returned alongside the prediction for thresholding."""
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
    X = vec.fit_transform(train["ndesc"])
    Q = vec.transform(test["ndesc"])
    sims = linear_kernel(Q, X)
    ref_hsn = train["hsn"].values
    ref_nums = train["nums"].tolist()
    preds, confs = [], []
    for row, qn in zip(sims, test["nums"]):
        idx = np.argpartition(row, -k)[-k:]
        votes = {}
        for i in idx:
            overlap = (len(qn & ref_nums[i]) / len(qn)) if qn else 0.0
            w = row[i] * (1.0 + 0.30 * overlap)
            votes[ref_hsn[i]] = votes.get(ref_hsn[i], 0.0) + w
        best = max(votes, key=votes.get)
        total = sum(votes.values()) or 1.0
        preds.append(best)
        confs.append(votes[best] / total)
    return np.array(preds), np.array(confs)


def evaluate(df):
    skf = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=SEED)
    methods = {
        "given_hsn (baseline)": lambda tr, te: m_given(tr, te),
        "fuzzy NN": lambda tr, te: m_fuzzy(tr, te),
        "tfidf char k=5": lambda tr, te: m_tfidf(tr, te),
        "hybrid tfidf+num": lambda tr, te: m_hybrid(tr, te)[0],
    }
    y = df["hsn"].values
    results = {name: np.zeros(len(df), dtype=object) for name in methods}
    conf_all = np.zeros(len(df))

    for tr_idx, te_idx in skf.split(df, df["label"].astype(str) + df["hsn"]):
        train, test = df.iloc[tr_idx], df.iloc[te_idx]
        for name, fn in methods.items():
            results[name][te_idx] = fn(train, test)
        conf_all[te_idx] = m_hybrid(train, test)[1]

    print(f"\n{'='*64}\nHSN match accuracy — {N_SPLITS}-fold, {len(df)} rows, "
          f"{df['hsn'].nunique()} HSN classes\n{'='*64}")
    print(f"{'method':<26}{'overall':>9}{'CLEAN':>9}{'NOISE':>9}")
    clean = df["label"] == "CLEAN"
    for name in methods:
        acc = (results[name] == y).mean()
        ac = (results[name][clean] == y[clean]).mean()
        an = (results[name][~clean] == y[~clean]).mean()
        print(f"{name:<26}{acc:>8.1%}{ac:>9.1%}{an:>9.1%}")

    print(f"\nBy Verify Status (hybrid tfidf+num):")
    hy = results["hybrid tfidf+num"]
    for st in df["status"].unique():
        mask = df["status"] == st
        print(f"  {st:<16}{(hy[mask]==y[mask]).mean():>7.1%}  (n={mask.sum()})")

    # auto-accept simulation: high-confidence rows only
    print(f"\nConfidence-gated auto-accept (hybrid):")
    for thr in (0.0, 0.5, 0.6, 0.7, 0.8):
        sel = conf_all >= thr
        if sel.sum() == 0:
            continue
        cov = sel.mean()
        prec = (hy[sel] == y[sel]).mean()
        print(f"  thr>={thr:.1f}  coverage={cov:>6.1%}  accuracy={prec:>6.1%}")

    # worst confusions
    wrong = df[hy != y].copy()
    wrong["pred"] = hy[hy != y]
    print(f"\nTop misclassifications (hybrid), {len(wrong)} wrong:")
    cc = wrong.groupby(["hsn", "pred"]).size().sort_values(ascending=False).head(8)
    for (true, pred), n in cc.items():
        print(f"  true {true} -> pred {pred}: {n}")
    return results, conf_all


if __name__ == "__main__":
    df = load()
    evaluate(df)
