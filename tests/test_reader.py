import json, tempfile, unittest
from pathlib import Path
from transformers import AutoTokenizer
from rag.reader_prompts import pack, parse_answer, ABSTAIN, messages
from rag.reader import questions_from


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = AutoTokenizer.from_pretrained(
            Path(__file__).resolve().parents[1] / "models/Qwen3-8B",
            local_files_only=True,
        )

    def hit(self, id, text):
        return {
            "chunk_id": id,
            "text": text,
            "source_urls": ["https://example.org"],
            "occurrences": [],
        }

    def test_context_budget_and_whole_chunks(self):
        rows = [
            self.hit("long", "word " * 1000),
            self.hit("short", "The Penguins play hockey."),
        ]
        p = pack("Which sport?", rows, self.t, context_window=350, max_new_tokens=64)
        self.assertLessEqual(p["input_tokens"] + 64, 350)
        self.assertEqual(p["excluded_chunk_ids"], ["long"])
        self.assertEqual(p["context"][0]["chunk_id"], "short")
        self.assertEqual(p["context"][0]["label"], "D1")

    def test_closed_book_has_no_evidence(self):
        p = pack("Question?", [self.hit("x", "SECRET EVIDENCE")], self.t, "closed-book")
        self.assertEqual(p["context"], [])
        self.assertNotIn("SECRET EVIDENCE", str(p["messages"]))

    def test_non_thinking_template(self):
        p = pack("Question?", [], self.t)
        rendered = self.t.decode(p["prompt_tokens"])
        self.assertTrue(rendered.endswith("<think>\n\n</think>\n\n"))

    def test_long_question_not_silently_cut(self):
        with self.assertRaises(ValueError):
            pack("word " * 1000, [], self.t, context_window=350, max_new_tokens=64)

    def test_citations_dates_preserved(self):
        c = [{"label": "D1", **self.hit("x", "fact")}]
        r = parse_answer("October 27, 2026 [D1].", c, "rag")
        self.assertEqual(r["answer"], "October 27, 2026.")
        self.assertEqual(r["citations"][0]["chunk_id"], "x")

    def test_grouped_citation_punctuation(self):
        c = [
            {"label": "D1", **self.hit("x", "fact")},
            {"label": "D2", **self.hit("y", "fact")},
        ]
        self.assertEqual(
            parse_answer("A festival [D1], [D2].", c, "rag")["answer"], "A festival."
        )

    def test_invalid_citation_not_invented(self):
        r = parse_answer("Hockey [D9].", [], "rag")
        self.assertEqual(r["citations"], [])
        self.assertTrue(r["warnings"])

    def test_abstain(self):
        r = parse_answer(ABSTAIN, [], "rag")
        self.assertTrue(r["abstained"])
        self.assertEqual(r["warnings"], [])

    def test_extra_abstention_text_flagged_not_rewritten(self):
        raw = "The evidence names the founders. " + ABSTAIN
        r = parse_answer(raw, [], "rag")
        self.assertTrue(r["abstained"])
        self.assertEqual(r["answer"], raw)
        self.assertIn("abstention_has_extra_text", r["warnings"])

    def test_knowledge_answer_needs_no_citation(self):
        r = parse_answer("Hockey", [], "rag")
        self.assertEqual(r["warnings"], [])
        self.assertFalse(r["abstained"])

    def test_fallback_refusal_detected(self):
        r = parse_answer("I don't know.", [], "rag")
        self.assertTrue(r["abstained"])
        self.assertEqual(r["warnings"], [])

    def test_reference_answers_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.json"
            p.write_text(
                json.dumps([{"id": 3, "question": "Q?", "answer": "PRIVATE REFERENCE"}])
            )
            self.assertEqual(questions_from(p), [{"id": "3", "question": "Q?"}])

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.json"
            p.write_text(
                json.dumps([{"id": 1, "question": "a"}, {"id": "1", "question": "b"}])
            )
            with self.assertRaises(ValueError):
                questions_from(p)


if __name__ == "__main__":
    unittest.main()
