import unittest

from scraper.models import city_of, is_taiwan
from scraper.relevance import CANDIDATE_THRESHOLD, DISPLAY_THRESHOLD, rule_score


class TestRelevance(unittest.TestCase):
    def test_core_ml_roles_score_high(self):
        for title in [
            "Senior Machine Learning Engineer",
            "LLM Inference Optimization Engineer",
            "AI 演算法工程師（深度學習）",
            "Applied Scientist, Generative AI",
            "2026校園徵才-AI軟體開發工程師",
        ]:
            self.assertGreaterEqual(rule_score(title, "").score, DISPLAY_THRESHOLD, title)

    def test_description_only_match_is_candidate(self):
        s = rule_score("Software Engineer", "Build RAG pipelines on large language models with PyTorch and vLLM.")
        self.assertGreaterEqual(s.score, CANDIDATE_THRESHOLD)
        self.assertEqual(s.category, "llm_genai")

    def test_non_ai_roles_penalised(self):
        self.assertLess(rule_score("Senior Account Manager, AI Solutions", "").score, DISPLAY_THRESHOLD)
        self.assertLess(rule_score("Mechanical Engineer, AI Server", "").score, DISPLAY_THRESHOLD)
        self.assertLess(rule_score("AI Server_測試工程師/主任", "").score, DISPLAY_THRESHOLD)
        self.assertLess(rule_score("Senior Java Engineer", "Use AI agents and LLM tools daily").score, DISPLAY_THRESHOLD)
        self.assertEqual(rule_score("Accountant", "General ledger").score, 0)

    def test_taiwan_locations(self):
        self.assertTrue(is_taiwan("TW, TPE, Taipei"))
        self.assertTrue(is_taiwan("新竹科學園區"))
        self.assertTrue(is_taiwan("Zhubei, Hsinchu County, Taiwan"))
        self.assertFalse(is_taiwan("Santa Clara, CA, US"))
        self.assertFalse(is_taiwan("Singapore"))
        self.assertEqual(city_of("New Taipei, Banqiao District"), "新北")
        self.assertEqual(city_of("Taiwan, Hsinchu"), "新竹")


if __name__ == "__main__":
    unittest.main()
