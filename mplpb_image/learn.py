"""Learning the difference, within limits it states.

A small logistic regression, standard library only, trained on the seed's
own pictures to tell generated from captured.

Three rules keep it from becoming a laundering channel:

1. Labels come only from attested pages: generations the studio signed
   at ingest (kind=generated, basis=signed-ingest) and captures a named
   person attested (kind=captured, basis=attested). Declared metadata,
   perceptual matches and the learner's own earlier scores are never
   training labels. A model trained on its own guesses grades itself.

2. Features are recomputed from the bytes at training time, after the
   bytes are checked against the signed hash. Nothing is read from a
   field someone could have edited.

3. The output is advisory. It is stored as a score with the model hash
   next to it. It never changes a page's kind, origin or depth.

Declared generation signals (IPTC, C2PA, generator tags) are deliberately
not features: the rules in classify.py already act on them, and including
them would let the model score well by re-learning a rule.

bytes_per_pixel counts as content: how well a picture compresses depends
mostly on how much detail and noise it holds.

Cross-validation is reported three ways: all features, format+metadata
only, content only. If format+metadata alone does nearly as well as all
features, the model has learned "API-sized PNG with no EXIF", which
stripping metadata or re-encoding defeats. The report says so rather than
letting a high accuracy stand alone.
"""

from __future__ import annotations

import math
import random
import time

from . import evidence as E
from . import pixels as P
from .keys import canonical

FORMAT = ["is_png", "is_jpeg", "is_webp", "api_size", "square", "log_pixels"]
META = ["has_exif", "has_camera", "has_exposure", "has_gps", "has_datetime_original", "has_software",
        "has_icc", "has_xmp", "png_text", "jpeg_quality", "progressive", "subsampled"]
CONTENT = ["content_available", "bytes_per_pixel", "lum_std", "lap_mean", "lap_cv", "lap_over_grad", "clip_frac", "sat_mean", "uniq_ratio"]
GROUPS = {"all": FORMAT + META + CONTENT, "format_metadata": FORMAT + META, "content": CONTENT}
MIN_PER_CLASS = 5
MODEL_VERSION = 1


def features(ev: dict, px: dict, nbytes: int) -> dict:
    exif = ev.get("exif") or {}
    w, h = ev.get("width") or 0, ev.get("height") or 0
    jq = ev.get("jpeg") or {}
    f = {
        "is_png": float(ev.get("format") == "png"),
        "is_jpeg": float(ev.get("format") == "jpg"),
        "is_webp": float(ev.get("format") == "webp"),
        "api_size": float((w, h) in E.API_SIZES),
        "square": float(w == h and w > 0),
        "log_pixels": math.log10(w * h) if w and h else 0.0,
        "bytes_per_pixel": nbytes / (w * h) if w and h else 0.0,
        "has_exif": float(bool(exif)),
        "has_camera": float(bool(exif.get("make") or exif.get("model"))),
        "has_exposure": float(bool(exif.get("exposure_time") or exif.get("f_number") or exif.get("iso"))),
        "has_gps": float(bool(exif.get("gps"))),
        "has_datetime_original": float(bool(exif.get("datetime_original"))),
        "has_software": float(bool(exif.get("software"))),
        "has_icc": float(bool(ev.get("icc"))),
        "has_xmp": float(bool(ev.get("xmp"))),
        "png_text": float(len(ev.get("png_text_keys") or [])),
        "jpeg_quality": (jq.get("quality_estimate") or 0) / 100.0,
        "progressive": float(bool(jq.get("progressive"))),
        "subsampled": float(jq.get("subsampling") not in (None, "1x1")),
        "content_available": float(bool(px.get("features"))),
    }
    for k in CONTENT[2:]:
        f[k] = float((px.get("features") or {}).get(k, 0.0))
    return f


# --- logistic regression ---------------------------------------------------------

def _fit(X, y, names, iters=600, lr=0.3, l2=0.01):
    n, d = len(X), len(names)
    mean = [sum(r[j] for r in X) / n for j in range(d)]
    std = [max(1e-6, (sum((r[j] - mean[j]) ** 2 for r in X) / n) ** 0.5) for j in range(d)]
    Z = [[(r[j] - mean[j]) / std[j] for j in range(d)] for r in X]
    pos = sum(y)
    wpos, wneg = n / (2 * max(pos, 1)), n / (2 * max(n - pos, 1))  # class balance
    w, b = [0.0] * d, 0.0
    for _ in range(iters):
        gw, gb = [0.0] * d, 0.0
        for z, t in zip(Z, y):
            p = _sig(b + sum(wi * zi for wi, zi in zip(w, z)))
            e = (p - t) * (wpos if t else wneg)
            gb += e
            for j in range(d):
                gw[j] += e * z[j]
        b -= lr * gb / n
        w = [wj - lr * (gj / n + l2 * wj) for wj, gj in zip(w, gw)]
    return {"features": names, "mean": mean, "std": std, "weights": w, "bias": b}


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -60 else 0.0


def _predict(m: dict, row: dict) -> float:
    z = m["bias"]
    for name, mu, sd, wj in zip(m["features"], m["mean"], m["std"], m["weights"]):
        z += wj * (row.get(name, 0.0) - mu) / sd
    return _sig(z)


def _cv(rows: list, labels: list, names: list, k: int, seed: int) -> dict:
    idx_pos = [i for i, t in enumerate(labels) if t]
    idx_neg = [i for i, t in enumerate(labels) if not t]
    rnd = random.Random(seed)
    rnd.shuffle(idx_pos)
    rnd.shuffle(idx_neg)
    folds = [idx_pos[i::k] + idx_neg[i::k] for i in range(k)]
    tp = tn = fp = fn = 0
    for f in folds:
        test = set(f)
        tr = [i for i in range(len(rows)) if i not in test]
        m = _fit([[rows[i][n] for n in names] for i in tr], [labels[i] for i in tr], names, iters=300)
        for i in f:
            guess = _predict(m, rows[i]) >= 0.5
            if labels[i]:
                tp += guess; fn += not guess
            else:
                tn += not guess; fp += guess
    tot = tp + tn + fp + fn
    return {
        "accuracy": round((tp + tn) / tot, 3),
        "balanced_accuracy": round(0.5 * (tp / max(tp + fn, 1) + tn / max(tn + fp, 1)), 3),
        "generated_recall": round(tp / max(tp + fn, 1), 3),
        "captured_recall": round(tn / max(tn + fp, 1), 3),
    }


def training_label(rec: dict):
    if rec.get("kind") == "generated" and rec.get("kind_basis") == "signed-ingest":
        return 1
    if rec.get("kind") == "captured" and rec.get("kind_basis") == "attested":
        return 0
    return None


class TrainingError(ValueError):
    pass


def train(examples: list, k: int = 5, seed: int = 20260928) -> dict:
    """examples: [(record, feature_dict)] for attested records only."""
    labels = [training_label(r) for r, _ in examples]
    if any(l is None for l in labels):
        raise TrainingError("only attested pages may be training labels")
    npos, nneg = sum(labels), len(labels) - sum(labels)
    if npos < MIN_PER_CLASS or nneg < MIN_PER_CLASS:
        raise TrainingError(f"need at least {MIN_PER_CLASS} attested pages of each kind; "
                            f"have {npos} generated, {nneg} captured")
    rows = [f for _, f in examples]
    kk = max(2, min(k, npos, nneg))
    cv = {g: _cv(rows, labels, names, kk, seed) for g, names in GROUPS.items()}
    model = _fit([[r[n] for n in GROUPS["all"]] for r in rows], labels, GROUPS["all"])
    warnings = []
    if npos + nneg < 60:
        warnings.append(f"{npos + nneg} labelled pictures is too few for these numbers to mean much")
    fm, ct, al = (cv[g]["balanced_accuracy"] for g in ("format_metadata", "content", "all"))
    if fm >= al - 0.03 and fm >= 0.8:
        warnings.append("format and metadata alone score about as well as all features: the model is "
                        "mostly reading file type, size and EXIF, which stripping or re-encoding defeats")
    if ct < 0.65:
        warnings.append("content features alone are near chance: the model cannot see generation in the pixels")
    if not P.pillow_available():
        warnings.append("no Pillow: JPEG and WebP pictures have no content features")
    model.update(
        version=MODEL_VERSION,
        trained_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        counts={"generated": npos, "captured": nneg},
        folds=kk, seed=seed,
        cv=cv, warnings=warnings,
        training=[{"document_id": r["document_id"], "file_sha256": r["file_sha256"], "label": l}
                  for (r, _), l in zip(examples, labels)],
    )
    return model


def model_hash(model: dict) -> str:
    import hashlib
    body = {k: v for k, v in model.items() if k not in ("sha256", "signer", "sig")}
    return hashlib.sha256(canonical(body)).hexdigest()


def score(model: dict, feats: dict) -> dict:
    p = _predict(model, feats)
    contrib = sorted(
        ((wj * (feats.get(n, 0.0) - mu) / sd, n) for n, mu, sd, wj in
         zip(model["features"], model["mean"], model["std"], model["weights"])),
        key=lambda t: -abs(t[0]))[:4]
    return {
        "p_generated": round(p, 3),
        "model_sha256": model.get("sha256"),
        "top_features": [{"feature": n, "push": "generated" if c > 0 else "captured", "weight": round(c, 2)}
                         for c, n in contrib],
        "advisory": True,
        "note": "A score, not a label. It never changes this picture's kind.",
    }


# --- seed I/O -----------------------------------------------------------------------

def examples_from_seed(root) -> tuple:
    """Attested pages whose bytes still match their signed hash, with fresh features."""
    from .corpus import catalog, corpus_root, sha256_bytes
    root = corpus_root(root)
    out, skipped = [], []
    for rec in catalog(root):
        if training_label(rec) is None:
            continue
        blob = root / rec["file"]
        raw = blob.read_bytes() if blob.is_file() else b""
        if sha256_bytes(raw) != rec.get("file_sha256"):
            skipped.append(f"{rec['document_id']}: bytes missing or changed")
            continue
        out.append((rec, features(E.read(raw), P.analyse(raw), len(raw))))
    return out, skipped


def train_seed(root, key=None) -> dict:
    from .corpus import NET, WRITE_LOCK, append_lineage, corpus_root, dump_json, refresh
    from .keys import load_or_create
    root = corpus_root(root)
    key = key or load_or_create()
    with WRITE_LOCK:
        examples, skipped = examples_from_seed(root)
        model = train(examples)
        model["skipped"] = skipped
        model["signer"] = key.node_id
        model["sha256"] = model_hash(model)
        model["sig"] = key.sign({k: v for k, v in model.items() if k != "sig"})
        dump_json(root / NET / "learner.json", model)
        append_lineage(root, {"op": "learn", "model_sha256": model["sha256"],
                              "counts": model["counts"]}, key)
        refresh(root, key)
    return model


def load_model(root):
    from .corpus import NET, corpus_root, load_json
    model = load_json(corpus_root(root) / NET / "learner.json", None)
    if model is None or model.get("sha256") != model_hash(model):
        return None
    return model


def advise(root, raw: bytes, ev: dict | None = None, px: dict | None = None):
    model = load_model(root)
    if model is None:
        return None
    ev = ev or E.read(raw)
    px = px or P.analyse(raw)
    return score(model, features(ev, px, len(raw)))
