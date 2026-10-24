"""Housing coverage for the landing page, from the newsletter's own tags.

Writes src/data/blogPosts.json: every public post on
citythatworks.substack.com tagged "Housing", newest first. The tag on Substack
is the source of truth; to add or drop a post from the site, tag or untag it
there, re-run this and commit the result.

Run it by hand: Substack refuses requests from GitHub's servers (403 on every
URL, checked 2026-10-24), so it can't run as a scheduled workflow.

Reads Substack's archive API, which pages 50 posts at a time. Fails loudly and
writes nothing if the API errors, changes shape, or returns far fewer posts
than are already committed.

    python3 scripts/import_posts.py
"""

import json
import os
import ssl
import sys
import time
import urllib.request

BLOG = "https://citythatworks.substack.com"
TAG = "Housing"
OUT = "src/data/blogPosts.json"
PAGE = 50

try:
    import certifi

    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # the system store works on most machines
    CTX = None


def archive():
    """Every post in the archive, newest first."""
    posts, offset = [], 0
    while True:
        url = f"{BLOG}/api/v1/archive?sort=new&offset={offset}&limit={PAGE}"
        req = urllib.request.Request(url, headers={"User-Agent": "chicago-housing-tracker"})
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            batch = json.load(r)
        if not isinstance(batch, list):
            sys.exit(f"Unexpected archive response at offset {offset}: {type(batch).__name__}")
        if not batch:
            return posts
        posts += batch
        offset += len(batch)
        time.sleep(0.5)


def main():
    posts = archive()
    print(f"  archive: {len(posts)} posts")
    out = []
    for p in posts:
        tags = [t.get("name") for t in p.get("postTags") or []]
        if TAG not in tags or p.get("audience") != "everyone" or p.get("type") != "newsletter":
            continue
        row = {
            "title": (p.get("title") or "").strip(),
            "slug": p.get("slug") or "",
            "date": (p.get("post_date") or "")[:10],
            "subtitle": (p.get("subtitle") or "").strip(),
            "author": ", ".join(b["name"] for b in p.get("publishedBylines") or []),
        }
        if not (row["title"] and row["slug"] and len(row["date"]) == 10):
            sys.exit(f"Post missing a title, slug or date: {p.get('id')}")
        out.append(row)
    out.sort(key=lambda r: r["date"], reverse=True)
    print(f"  tagged {TAG!r}: {len(out)}")

    # Untagging a post is a legitimate way to drop it, so a small shrink is
    # fine; losing half the list means the API or the tag name changed.
    if os.path.exists(OUT):
        before = len(json.load(open(OUT)))
        if len(out) < before / 2:
            sys.exit(f"Refusing to write: {len(out)} posts against {before} committed.")
    if not out:
        sys.exit("Refusing to write an empty list.")

    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
