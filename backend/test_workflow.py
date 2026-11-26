import io
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from pymongo import MongoClient

import manage
import meta

class DataWorkflow(unittest.TestCase):
    def setUp(self):
        self.client = MongoClient(os.environ["MONGO_URL"])
        self.name = "workflow_test_" + uuid4().hex
        self.posts = self.client[self.name].posts

    def tearDown(self):
        self.client.drop_database(self.name)
        self.client.close()

    def test_import_analysis_and_refresh(self):
        source = Path(__file__).parent / "examples/post.json"
        manage.import_posts(self.posts, source)
        manage.import_posts(self.posts, source)
        self.assertEqual(self.posts.count_documents({}), 1)
        result = dict(overall_vibe="Interested", sentiment_breakdown="Two practical questions",
                      dominant_emotion="Interest", controversial_points="", top_3_arguments=["Ask about prints"],
                      title_english="Open studio", meta_category="Positive")
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]}
        with patch.dict(os.environ, GEMINI_MODEL="example", GEMINI_API_KEY="test"), patch.object(
            manage, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())
        ) as transport:
            manage.analyze(self.posts, "our-page-001")
        request = transport.call_args.args[0]
        self.assertIn(b"Can we bring our own prints?", request.data)
        self.assertEqual(self.posts.find_one()["comment_analysis"]["comments_analyzed"], 2)
        with TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.json"
            records = json.loads(source.read_text())
            records[0]["comments"] = ["Is there a waiting list?"]
            changed.write_text(json.dumps(records))
            manage.import_posts(self.posts, changed)
        self.assertNotIn("comment_analysis", self.posts.find_one())

    def test_meta_import_keeps_metrics_and_discards_hidden_comments(self):
        def response(path, params):
            if path == "42":
                return {"id": "42", "name": "Open Studio"}
            if path.endswith("/posts"):
                return {"data": [{"id": "42_7", "message": "Open studio",
                     "created_time": datetime.now(timezone.utc).isoformat(),
                     "reactions": {"summary": {"total_count": 6}}}]}
            self.assertEqual(path, "42_7/comments")
            return {"data": [{"message": "Hello"}, {"message": "Hidden", "is_hidden": True}]}

        self.posts.insert_one({"fb_post_id": "42_7", "metrics": {"impressions": 200}})
        with patch.object(meta, "read_graph", side_effect=response):
            meta.sync_page(self.posts, "42")
        post = self.posts.find_one()
        self.assertEqual(post["comments"], ["Hello"])
        self.assertEqual(post["metrics"], {"impressions": 200, "reactions": 6})

if __name__ == "__main__":
    unittest.main()
