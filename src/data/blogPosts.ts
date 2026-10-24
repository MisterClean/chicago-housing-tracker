/**
 * Housing coverage from the newsletter, surfaced at the bottom of the landing page.
 *
 * Every public post tagged "Housing" on Substack, newest first, written to
 * blogPosts.json by scripts/import_posts.py. The tag is the source of truth:
 * tag or untag a post on Substack to add or drop it here, then re-run the
 * import by hand (Substack blocks GitHub's servers, so it can't be scheduled).
 */
import posts from "./blogPosts.json";

export interface BlogPost {
  title: string;
  /** Substack slug; the full URL is derived from it. */
  slug: string;
  /** ISO date (YYYY-MM-DD), used for ordering and display. */
  date: string;
  /** The post's own subtitle, as published. */
  subtitle: string;
  /** Byline. Comma-separated where a post has more than one author. */
  author: string;
}

export const BLOG_URL = "https://citythatworks.substack.com";

export const postUrl = (slug: string) => `${BLOG_URL}/p/${slug}`;

/** Newest first. */
export const BLOG_POSTS: BlogPost[] = posts;

/** How many show before the reader asks for the rest. */
export const POSTS_VISIBLE = 6;

/** Group posts by publication year, newest year first. */
export function groupByYear(posts: BlogPost[]): { year: string; posts: BlogPost[] }[] {
  const groups = new Map<string, BlogPost[]>();
  for (const p of posts) {
    const y = p.date.slice(0, 4);
    if (!groups.has(y)) groups.set(y, []);
    groups.get(y)!.push(p);
  }
  return [...groups.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([year, posts]) => ({ year, posts }));
}
