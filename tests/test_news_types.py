"""T-421：新聞類型——類別清單、提示詞、回應解析、權重輸出與分類公式（離線、決定性）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from twquant.data import linear_probe as lp
from twquant.data import news_types as nt
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parent.parent
DOC = yaml.safe_load((_ROOT / "configs" / "news.yaml").read_text(encoding="utf-8"))


def test_classes_from_repo_config_and_validation() -> None:
    classes = nt.load_classes(DOC)
    assert classes[-1].id == "other" and len(classes) >= 8 and all(c.label for c in classes)
    with pytest.raises(DataError, match="other"):
        nt.load_classes({"types": {"classes": [{"id": "a", "label": "A"}]}})
    with pytest.raises(DataError, match="唯一"):
        nt.load_classes(
            {
                "types": {
                    "classes": [
                        {"id": "a", "label": "A"},
                        {"id": "a", "label": "B"},
                        {"id": "other", "label": "O"},
                    ]
                }
            }
        )
    with pytest.raises(DataError):
        nt.load_classes({})


def test_prompt_lists_classes_and_parse_is_strict() -> None:
    classes = nt.load_classes(DOC)
    p = nt.build_prompt(classes, "台積電上調財測", "內文" * 500, max_body=6)
    assert "- earnings：財報營收" in p and p.endswith("內文內文內文")
    ids = [c.id for c in classes]
    assert nt.parse_type('{"type": "capex"}', ids) == "capex"
    assert nt.parse_type('<think>x</think>{"type":"macro"}', ids) == "macro"
    assert nt.parse_type("order", ids) == "order"
    with pytest.raises(DataError):
        nt.parse_type('{"type": "banana"}', ids)


def test_export_and_classify_match_training_probe() -> None:
    rng = np.random.default_rng(1)
    k, d = 4, 16
    centers = rng.normal(0, 1, (k, d))
    yi = rng.integers(0, k, 400)
    x = centers[yi] + rng.normal(0, 0.3, (400, d))
    probe = lp.train_softmax(x, yi, k, epochs=200)
    classes = [
        nt.TypeClass("a", "A", ""),
        nt.TypeClass("b", "B", ""),
        nt.TypeClass("c", "C", ""),
        nt.TypeClass("other", "其他", ""),
    ]
    w = nt.export_weights(probe, classes, version="v", min_confidence=0.5, metrics={"n": 400})
    assert len(w["w"]) == k and len(w["w"][0]) == d and w["classes"][-1] == "other"
    # 輸出的權重算出來的類別，跟探針本身一致（四捨五入 4 位不影響）
    agree = sum(
        nt.classify(w, x[i])[0] == classes[int(probe.proba(x[i : i + 1]).argmax())].id
        for i in range(400)
    )
    assert agree >= 396
    # 信心不到門檻 → other
    assert nt.classify({**w, "min_confidence": 1.01}, x[0]) == ("other", nt.classify(w, x[0])[1])


def test_per_class_recall_counts_hits_per_true_class() -> None:
    import numpy as np

    from twquant.data.linear_probe import per_class_recall

    y = np.array([0, 0, 1, 1, 1, 2])
    p = np.array([0, 1, 1, 1, 0, 0])
    assert per_class_recall(y, p, 4) == [0.5, 2 / 3, 0.0, 0.0]
