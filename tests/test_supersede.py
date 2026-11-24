import unittest

from scraper.run import _title_key, drop_superseded

ARAIC = {"id": "asus-araic", "supersedes": {"company": "asus", "marker": "AI應用|Robotics|ARAIC|機器人|Autonomous"}}


class SupersedeTest(unittest.TestCase):
    def test_title_key_ignores_codes_and_tags(self):
        self.assertEqual(_title_key("RD20167 Senior Machine Learning Engineer (AI應用)"), _title_key("Senior Machine Learning Engineer"))
        self.assertEqual(_title_key("Engineering Manager - Autonomous Systems(AI應用)"), _title_key("Engineering Manager - Autonomous Systems"))

    def test_keeps_preferred_copy_and_unrelated_jobs(self):
        jobs = [
            {"company_id": "asus-araic", "title": "Machine Learning Engineer"},
            {"company_id": "asus", "title": "Machine Learning Engineer (AI應用)"},
            {"company_id": "asus", "title": "Machine Learning Engineer (Medical AI)", "summary": "醫療影像模型"},
            {"company_id": "asus", "title": "Robotics Software Development Engineer (AI應用)"},
        ]
        kept = [(j["company_id"], j["title"]) for j in drop_superseded(jobs, [ARAIC])]
        self.assertEqual(kept, [
            ("asus-araic", "Machine Learning Engineer"),
            ("asus", "Machine Learning Engineer (Medical AI)"),
            ("asus", "Robotics Software Development Engineer (AI應用)"),
        ])


if __name__ == "__main__":
    unittest.main()
