import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
from seed import seed_demo

client = AsyncIOMotorClient(os.environ["MONGO_URL"], tz_aware=True)
db = client[os.getenv("MONGO_DATABASE", "marketing_analytics")]

@asynccontextmanager
async def lifespan(app):
    await db.posts.create_index("created_time")
    await db.posts.create_index("fb_post_id", unique=True)
    if os.getenv("SEED_DEMO", "true").lower() == "true":
        await seed_demo(db)
    yield
    client.close()

app = FastAPI(title="Marketing analytics", lifespan=lifespan)

def date_range(start, end):
    try:
        first = datetime.combine(date.fromisoformat(start), time.min, timezone.utc)
        last = datetime.combine(date.fromisoformat(end), time.min, timezone.utc)
        if first > last:
            raise ValueError
        return {"$gte": first, "$lt": last + timedelta(days=1)}
    except ValueError:
        raise HTTPException(422, "Choose a valid date range")

@app.get("/api/pages")
async def get_pages():
    names = sorted(await db.posts.distinct("page_name"))
    return {"pages": [{"id": "all", "name": "All Pages"}, *[{"id": n, "name": n} for n in names]]}

def get_time_filter_date(time_filter: str) -> Optional[datetime]:
    """Convert time filter string to datetime cutoff."""
    if not time_filter or time_filter == "all":
        return None

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    if time_filter == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_filter == "7d":
        return now - timedelta(days=7)
    elif time_filter == "30d":
        return now - timedelta(days=30)
    elif time_filter == "90d":
        return now - timedelta(days=90)

    return None


@app.get("/api/dashboard/stats")
async def get_dashboard_stats(
    page_id: Optional[str] = Query(None),
    time_filter: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get aggregated stats for dashboard"""

    # Build match filter
    match_filter = {}
    if page_id and page_id != "all":
        match_filter["page_name"] = page_id

    # Add time filter (custom date range or preset)
    if start_date and end_date:
        # Custom date range
        match_filter["created_time"] = date_range(start_date, end_date)
    else:
        cutoff_date = get_time_filter_date(time_filter)
        if cutoff_date:
            match_filter["created_time"] = {"$gte": cutoff_date}

    # Aggregate stats from posts
    pipeline = [
        {"$match": match_filter} if match_filter else {"$match": {}},
        {
            "$group": {
                "_id": None,
                "total_posts": {"$sum": 1},
                "impressions": {"$sum": {"$ifNull": ["$metrics.impressions", 0]}},
                "viewers": {"$sum": {"$ifNull": ["$metrics.reach", 0]}},
                "reactions": {"$sum": {"$ifNull": ["$metrics.reactions", 0]}},
                "comments": {"$sum": {"$ifNull": ["$metrics.comments", 0]}},
                "shares": {"$sum": {"$ifNull": ["$metrics.shares", 0]}},
                "saves": {"$sum": {"$ifNull": ["$metrics.saves", 0]}},
                "net_follows": {"$sum": {"$ifNull": ["$metrics.net_follows", 0]}},
                "video_views": {"$sum": {"$ifNull": ["$metrics.video_views", 0]}},
                "watch_seconds": {"$sum": {"$ifNull": ["$metrics.total_watch_time", {"$multiply": ["$metrics.video_views", "$metrics.avg_watch_time"]}]}},
            }
        }
    ]

    result = await db.posts.aggregate(pipeline).to_list(1)

    if not result:
        return dict(
            total_posts=0,
            impressions=0,
            viewers=0,
            reactions=0,
            comments=0,
            shares=0,
            saves=0,
            net_follows=0,
            watch_time_hrs=0,
            avg_engagement=0
        )

    stats = result[0]

    # Calculate engagement rate
    total_engagement = stats["reactions"] + stats["comments"] + stats["shares"]
    impressions = stats["impressions"] or 1
    avg_engagement = round((total_engagement / impressions) * 100, 2)

    watch_time_hrs = round(stats["watch_seconds"] / 3600, 1)

    return dict(
        total_posts=stats["total_posts"],
        impressions=stats["impressions"],
        viewers=stats["viewers"],
        reactions=stats["reactions"],
        comments=stats["comments"],
        shares=stats["shares"],
        saves=stats["saves"],
        net_follows=stats["net_follows"],
        watch_time_hrs=watch_time_hrs,
        avg_engagement=avg_engagement
    )


@app.get("/api/dashboard/kpi-matrix")
async def get_kpi_matrix(
    page_id: Optional[str] = Query(None),
):
    """
    Get KPI benchmarks grouped by media type and content category.
    Returns 28-day rolling averages for 12 cells (3 media types × 4 categories).
    Posts with 'Unknown' category are excluded.
    """
    from datetime import timedelta

    # Fixed 28-day rolling window
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=28)

    # Valid categories (exclude Unknown)
    valid_categories = ["Positive", "Negative", "Activation", "Agenda Setting"]

    # Build match filter
    match_filter = {
        "created_time": {"$gte": cutoff_date},
        "analysis.meta_category": {"$in": valid_categories}
    }
    if page_id and page_id != "all":
        match_filter["page_name"] = page_id

    # Aggregate by media_type AND category
    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": {
                    "media_type": "$media_type",
                    "category": "$analysis.meta_category"
                },
                "count": {"$sum": 1},
                "avg_impressions": {"$avg": {"$ifNull": ["$metrics.impressions", 0]}},
                "avg_reach": {"$avg": {"$ifNull": ["$metrics.reach", 0]}},
                "avg_clicks": {"$avg": {"$ifNull": ["$metrics.clicks", 0]}},
                "avg_comments": {"$avg": {"$ifNull": ["$metrics.comments", 0]}},
                "avg_reactions": {"$avg": {"$ifNull": ["$metrics.reactions", 0]}},
                "avg_shares": {"$avg": {"$ifNull": ["$metrics.shares", 0]}},
                "avg_saves": {"$avg": {"$ifNull": ["$metrics.saves", 0]}},
                "total_impressions": {"$sum": {"$ifNull": ["$metrics.impressions", 0]}},
                "total_reactions": {"$sum": {"$ifNull": ["$metrics.reactions", 0]}},
                "total_comments": {"$sum": {"$ifNull": ["$metrics.comments", 0]}},
                "total_shares": {"$sum": {"$ifNull": ["$metrics.shares", 0]}},
                # Distribution is reels-only metric (avg_watch_time as proxy)
                "avg_distribution": {"$avg": {"$ifNull": ["$metrics.avg_watch_time", 0]}},
            }
        }
    ]

    results = await db.posts.aggregate(pipeline).to_list(100)

    # Initialize KPI matrix structure
    media_types = ["reel", "photo", "status"]
    kpi_matrix = {
        media_type: {
            category: {
                "count": 0,
                "has_data": False,
                "avg_impressions": None,
                "avg_reach": None,
                "avg_clicks": None,
                "avg_comments": None,
                "avg_engagement_rate": None,
                "avg_distribution": None,
            }
            for category in valid_categories
        }
        for media_type in media_types
    }

    # Populate matrix from aggregation results
    for result in results:
        media_type = result["_id"]["media_type"]
        category = result["_id"]["category"]

        if media_type not in media_types or category not in valid_categories:
            continue

        # Calculate engagement rate
        total_engagement = result["total_reactions"] + result["total_comments"] + result["total_shares"]
        total_impressions = result["total_impressions"] or 1
        avg_engagement_rate = round((total_engagement / total_impressions) * 100, 2)

        kpi_matrix[media_type][category] = {
            "count": result["count"],
            "has_data": result["count"] > 0,
            "avg_impressions": round(result["avg_impressions"]),
            "avg_reach": round(result["avg_reach"]),
            "avg_clicks": round(result["avg_clicks"]),
            "avg_comments": round(result["avg_comments"], 1),
            "avg_engagement_rate": avg_engagement_rate,
            # Distribution only meaningful for reels
            "avg_distribution": round(result["avg_distribution"], 1) if media_type == "reel" else None,
        }

    return {
        "kpi_matrix": kpi_matrix,
        "period_days": 28,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "categories": valid_categories,
    }


@app.get("/api/posts")
async def get_posts(
    page_id: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),  # Filter by analysis_status
    analysis_status: Optional[str] = Query(None),
    scoring_status: Optional[str] = Query(None),
    time_filter: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    sort_by: str = Query("created_time"),
    sort_order: str = Query("desc"),
):
    """Get posts with filtering and pagination"""
    import traceback

    try:
        # Build filter
        filter_query = {}
        if page_id and page_id != "all":
            filter_query["page_name"] = page_id
        if media_type and media_type != "all":
            filter_query["media_type"] = media_type
        # Support both 'status' and 'analysis_status' params
        effective_status = status or analysis_status
        if effective_status and effective_status != "all":
            filter_query["analysis_status"] = effective_status
        if scoring_status and scoring_status != "all":
            filter_query["scoring_status"] = scoring_status

        # Add time filter (custom date range or preset)
        if start_date and end_date:
            filter_query["created_time"] = date_range(start_date, end_date)
        else:
            cutoff_date = get_time_filter_date(time_filter)
            if cutoff_date:
                filter_query["created_time"] = {"$gte": cutoff_date}

        # Sort direction
        sort_dir = -1 if sort_order == "desc" else 1

        # Get total count
        total = await db.posts.count_documents(filter_query)

        # Map frontend sort fields to backend field paths
        sort_field_map = {
            "created_time": "created_time",
            "media_type": "media_type",
            "category": "analysis.meta_category",
            "title": "analysis.title_english",
            "impressions": "metrics.impressions",
            "reach": "metrics.reach",
            "reactions": "metrics.reactions",
            "comments": "metrics.comments",
            "shares": "metrics.shares",
            "saves": "metrics.saves",
            "net_follows": "metrics.net_follows",
            "avg_watch_time": "metrics.avg_watch_time",
            "distribution": "metrics.distribution",
            "score": "scoring.total_score",
        }

        # Get the actual MongoDB field path
        actual_sort_field = sort_field_map.get(sort_by, "created_time")

        # Handle engagement rate sorting (calculated field)
        if sort_by == "engagement":
            # Use aggregation pipeline for calculated field sorting
            pipeline = [
                {"$match": filter_query},
                {"$addFields": {
                    "engagement_rate": {
                        "$cond": {
                            "if": {"$gt": [{"$ifNull": ["$metrics.impressions", 0]}, 0]},
                            "then": {
                                "$divide": [
                                    {"$add": [
                                        {"$ifNull": ["$metrics.reactions", 0]},
                                        {"$ifNull": ["$metrics.comments", 0]},
                                        {"$ifNull": ["$metrics.shares", 0]}
                                    ]},
                                    "$metrics.impressions"
                                ]
                            },
                            "else": 0
                        }
                    }
                }},
                {"$sort": {"engagement_rate": sort_dir, "_id": sort_dir}},
                {"$skip": skip},
                {"$limit": limit}
            ]
            posts = await db.posts.aggregate(pipeline).to_list(limit)
            # Exclude comments array from results (large data, not needed for list view)
            for post in posts:
                post.pop("comments", None)
        else:
            # Standard sort by field path - exclude comments array for performance
            projection = {"comments": 0}  # Exclude large comments array
            cursor = db.posts.find(filter_query, projection).sort([(actual_sort_field, sort_dir), ("_id", sort_dir)]).skip(skip).limit(limit)
            posts = await cursor.to_list(limit)

        # Convert ObjectId and datetime to string (including nested)
        from bson import ObjectId
        from datetime import datetime as dt
        def serialize_doc(doc):
            """Recursively convert ObjectId/datetime to string in document"""
            if isinstance(doc, dict):
                return {k: serialize_doc(v) for k, v in doc.items()}
            elif isinstance(doc, list):
                return [serialize_doc(item) for item in doc]
            elif isinstance(doc, ObjectId):
                return str(doc)
            elif isinstance(doc, dt):
                return doc.isoformat()
            else:
                return doc

        serialized_posts = [serialize_doc(post) for post in posts]


        return {
            "posts": serialized_posts,
            "total": total,
            "page": skip // limit + 1,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        print("Post query failed")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Unable to read posts")



@app.get("/api/posts/{post_id}")
async def post_detail(post_id: str):
    query = {"fb_post_id": post_id}
    if ObjectId.is_valid(post_id):
        query = {"$or": [query, {"_id": ObjectId(post_id)}]}
    post = await db.posts.find_one(query, {"comments": 0})
    if not post:
        raise HTTPException(404, "Post not found")
    post["_id"] = str(post["_id"])
    return post

web = Path(os.environ.get("WEB_DIST", "dist"))
app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

@app.get("/")
@app.get("/dashboard")
async def homepage():
    return FileResponse(web / "index.html")
