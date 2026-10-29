import unittest, tempfile, json
from pathlib import Path
import numpy as np
from rag.retrieval import (
    tokenize,
    normalize,
    combine,
    prepare,
    Retriever,
    faiss_build,
    faiss_search,
)


class RetrievalTests(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(
            tokenize("CMU, $1,234.50 on 2026 https://bad.org/ads"),
            ["cmu", "1,234.50", "on", "2026"],
        )

    def test_constant(self):
        self.assertTrue(np.array_equal(normalize([3, 3]), [0, 0]))

    def test_rrf_known_ranks(self):
        ids, s = combine([3, 1, 2], [1, 3, 2], "rrf", 2, 3, rrf_k=60)
        self.assertAlmostEqual(s[0], 1 / 61 + 1 / 63)
        self.assertAlmostEqual(s[1], s[0])
        self.assertEqual(list(ids), [0, 1])

    def test_rrf_no_lexical_evidence(self):
        ids, s = combine([0, 0, 0], [1, 2, 3], "rrf", 1, 2)
        self.assertEqual(ids[0], 2)
        self.assertEqual(s[0], 0)

    def test_weighted_endpoints(self):
        for alpha, expected in [(0, 0), (1, 2)]:
            ids, _ = combine([5, 2, 1], [1, 2, 3], "weighted", 1, 3, alpha)
            self.assertEqual(ids[0], expected)

    def test_faiss_cosine_and_row_mapping(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            x = np.array([[1, 0], [0, 1], [-1, 0]], dtype="float32")
            np.save(p / "x.npy", x)
            faiss_build(p / "x.npy", p / "index")
            q = np.array([[0.6, 0.8]], dtype="float32")
            s, i = faiss_search(p / "index", q, 3, 3)
            self.assertTrue(np.allclose(s[0], (x @ q[0])[i[0]]))
            self.assertEqual(i[0, 0], 1)

    def test_sparse_roundtrip_and_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            source = p / "source.jsonl"
            source.write_text(
                "\n".join(
                    json.dumps({"chunk_id": str(i), "text": s, "source_urls": []})
                    for i, s in enumerate(
                        [
                            "penguins hockey ice",
                            "steelers football",
                            "pirates baseball",
                            "museum art",
                        ]
                    )
                )
            )
            prepare(source, p / "index")
            r = Retriever(p / "index")
            self.assertEqual(r.search("hockey", "bm25", 1)[0]["chunk_id"], "0")
            source.write_text(source.read_text() + "\n")
            with self.assertRaises(ValueError):
                prepare(source, p / "index")

    def test_bad_fusion(self):
        with self.assertRaises(ValueError):
            combine([1], [1], "weighted", alpha=2)


if __name__ == "__main__":
    unittest.main()
