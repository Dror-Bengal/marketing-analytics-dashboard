"""Import a JSON export or analyse one post's comments using an explicitly supplied key."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from meta import sync_page
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import MongoClient

class PostInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    fb_post_id: str = Field(min_length=1)
    page_name: str = Field(min_length=1)
    created_time: datetime
    media_type: str
    message: str
    metrics: dict[str, float | str | dict]
    analysis: dict = Field(default_factory=dict)
    comments: list[str] = Field(default_factory=list)

    @field_validator("created_time")
    @classmethod
    def check_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("created_time needs a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("metrics")
    @classmethod
    def check_metrics(cls, metrics):
        for key, value in metrics.items():
            if key == "distribution" and isinstance(value, str):
                continue
            if key == "reactions_breakdown" and isinstance(value, dict):
                continue
            if not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be numeric")
        return metrics

def import_posts(collection, path):
    records = [PostInput.model_validate(row).model_dump() for row in json.loads(path.read_text())]
    if len({r["fb_post_id"] for r in records}) != len(records):
        raise ValueError("Duplicate post IDs in the file")
    for record in records:
        previous = collection.find_one({"fb_post_id": record["fb_post_id"]}, {"comments": 1})
        update = {"$set": record}
        if previous and previous.get("comments") != record["comments"]:
            update["$unset"] = {"comment_analysis": ""}
        collection.update_one({"fb_post_id": record["fb_post_id"]}, update, upsert=True)
    print(f"Imported {len(records)} posts")

def analyze(collection, post_id):
    post = collection.find_one({"fb_post_id": post_id})
    if not post:
        raise ValueError("Unknown post")
    comments = post.get("comments", [])
    model, key = os.environ["GEMINI_MODEL"], os.environ["GEMINI_API_KEY"]
    url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(model, safe="") + ":generateContent"
    prompt = Path(__file__).with_name("comment-analysis.txt").read_text()
    payload = {"systemInstruction": {"parts": [{"text": prompt}]},
               "contents": [{"parts": [{"text": json.dumps({"post": post.get("message", ""), "comments": comments}, ensure_ascii=False)}]}],
               "generationConfig": {"responseMimeType": "application/json"}}
    request = Request(url, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urlopen(request, timeout=90) as response:
        candidate = json.load(response)["candidates"][0]["content"]["parts"]
    result = json.loads("".join(part.get("text", "") for part in candidate if not part.get("thought")))
    fields = ("overall_vibe", "sentiment_breakdown", "dominant_emotion", "controversial_points")
    if not all(isinstance(result.get(k), str) for k in fields):
        raise ValueError("Incomplete analysis")
    arguments = result.get("top_3_arguments")
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise ValueError("Invalid top arguments")
    saved = {k: result[k] for k in fields}
    saved.update(top_3_arguments=arguments, comments_analyzed=len(comments))
    category = result.get("meta_category")
    title = result.get("title_english")
    if category not in {"Positive", "Negative", "Activation", "Agenda Setting"} or not isinstance(title, str):
        raise ValueError("Invalid post classification")
    changes = {"analysis.meta_category": category, "analysis.title_english": title, "analysis_status": "complete"}
    if comments:
        changes["comment_analysis"] = saved
    match = collection.update_one({"_id": post["_id"], "message": post.get("message"), "comments": post.get("comments")},
                                  {"$set": changes})
    if not match.matched_count:
        raise RuntimeError("Comments changed during analysis. Run again with the current comments.")
    print("Saved comment analysis")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import").add_argument("path", type=Path)
    commands.add_parser("sync-page").add_argument("page_id")
    commands.add_parser("analyze").add_argument("post_id")
    args = parser.parse_args()
    with MongoClient(os.environ["MONGO_URL"]) as client:
        posts = client[os.getenv("MONGO_DATABASE", "marketing_analytics")].posts
        if args.command == "import":
            import_posts(posts, args.path)
        elif args.command == "sync-page":
            sync_page(posts, args.page_id)
        else:
            analyze(posts, args.post_id)
