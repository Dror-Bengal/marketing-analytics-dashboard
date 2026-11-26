"""Read a recent batch from a connected Facebook Page with a supplied Page token."""
import json
import os
import re
from datetime import datetime
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

def read_graph(path, params):
    version = os.environ["META_GRAPH_VERSION"]
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("Use a Graph API version such as v24.0")
    url = f"https://graph.facebook.com/{version}/{path}?" + urlencode(params)
    req = Request(url, headers={"Authorization": "Bearer " + os.environ["META_PAGE_TOKEN"]})
    with urlopen(req, timeout=45) as response:
        return json.load(response)

def sync_page(collection, page_id):
    page = read_graph(quote(page_id, safe=""), {"fields": "id,name"})
    fields = "id,created_time,message,permalink_url,full_picture,comments.limit(0).summary(true),reactions.limit(0).summary(true),shares"
    batch = read_graph(quote(page["id"], safe="") + "/posts", {"fields": fields, "limit": 25})
    for post in batch["data"]:
        key = post["id"]
        link = post.get("permalink_url", "")
        media = "status"
        if "/reel/" in link:
            media = "reel"
        elif "/videos/" in link:
            media = "video"
        elif post.get("full_picture"):
            media = "photo"
        raw_comments = read_graph(quote(key, safe="")+"/comments", {"fields": "message,is_hidden", "limit": 100})
        comments = [c["message"] for c in raw_comments["data"] if c.get("message") and not c.get("is_hidden")]
        values = {"fb_post_id": key, "page_name": page["name"], "media_type": media,
                  "message": post.get("message", ""), "permalink_url": link,
                  "created_time": datetime.fromisoformat(post["created_time"]), "comments": comments}
        for metric in ("comments", "reactions"):
            count = post.get(metric, {}).get("summary", {}).get("total_count")
            if count is not None:
                values["metrics."+metric] = count
        if "shares" in post:
            values["metrics.shares"] = post["shares"].get("count", 0)
        if post.get("full_picture"):
            values["thumbnail_url"] = post["full_picture"]
        previous = collection.find_one({"fb_post_id": key}, {"comments": 1, "message": 1})
        update = {"$set": values}
        if previous and (previous.get("comments") != comments or previous.get("message") != values["message"]):
            update["$unset"] = {"comment_analysis": "", "analysis": ""}
        collection.update_one({"fb_post_id": key}, update, upsert=True)
    print(f"Synced {len(batch['data'])} recent posts")
