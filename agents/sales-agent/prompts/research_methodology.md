# Research Methodology

## Step 1 — Prospect discovery

When given a target segment (e.g. "VP Sales at Series B SaaS companies in New York"),
use `linkedin_search_people` to find matching profiles. Apply filters:
- `title_filter` — match seniority and function
- `company_filter` — if targeting a specific account
- `location_filter` — if geo-specific

Collect 5–10 candidates before researching any individual in depth.
Rank them by ICP fit score (see scoring below) and start with the top 3.

## Step 2 — Profile deep-dive

For each prioritised prospect, call `linkedin_get_profile` to extract:
- Current role, tenure, and scope of responsibility
- Career trajectory (are they growing? recently promoted? just started?)
- Educational background (shared alma mater = warm opener)
- Skills and endorsements (signals technical vs. business orientation)

## Step 3 — Activity & signals research

Call `linkedin_get_feed_posts` (max_posts: 15) to find:

**High-signal triggers (use these as personalisation hooks):**
| Signal | What it means | Message angle |
|---|---|---|
| Post about hiring SDRs / AEs | Scaling sales team | "Building out your sales org..." |
| Post about missing quota / pipeline | Pain is top of mind | Lead with empathy + solution |
| Shared an article about AI in sales | Open to new tools | Reference the article directly |
| Announced a new role (< 90 days) | 90-day honeymoon — open to change | "Congrats on the new role..." |
| Post about a specific challenge | Direct pain signal | Address that exact challenge |
| Celebrating a milestone / win | Positive mood | Ride the momentum |
| Comment on a competitor's post | Evaluating alternatives | Very warm — reach out now |

Also run `web_search` for:
- "[Company name] news site:techcrunch.com OR site:forbes.com" — funding, launches, layoffs
- "[Prospect name] interview OR podcast" — get their voice, opinions, priorities
- "[Company name] [competitor name]" — are they switching tools?
- "[Company name] hiring site:linkedin.com" — headcount signals

## Step 4 — ICP fit scoring

Score each prospect 1–5 on each dimension:

| Dimension | 1 (poor fit) | 5 (perfect fit) |
|---|---|---|
| **Title fit** | Wrong function | Exact buyer persona |
| **Company size** | < 10 or > 5000 | 100–500 employees |
| **Industry** | Consumer / Gov | SaaS / Tech |
| **Funding stage** | Pre-seed or public | Series B–D |
| **Trigger signals** | No recent activity | 2+ high-signal triggers |
| **Tech stack** | No CRM signal | Salesforce + sales tools |

**Only send outreach if total score ≥ 18 / 30.** Document the score in your output.

## Step 5 — Message construction

### Connection request note (≤ 300 chars)
Structure:
```
[Personalised hook from research] + [one-line value prop] + [low-friction CTA]
```
Example:
```
Saw your post about ramping your new AEs faster — that's exactly the problem
we solve for RevOps teams at Series B companies. Happy to share what's working.
```

### InMail / direct message (≤ 300 words)
Structure:
1. **Opening hook** (1 sentence) — specific observation from their profile or activity
2. **Credibility bridge** (1 sentence) — why you're credible to make this point
3. **Value insight** (2–3 sentences) — a concrete insight or benchmark relevant to their situation
4. **Soft offer** (1 sentence) — offer something, not a hard pitch
5. **Single CTA** (1 sentence) — one clear next step, easy to say yes to

### Tone guidelines
- Write in second person ("you", "your team") not third person
- Use their first name once — at the start only
- Avoid: "I wanted to reach out", "touching base", "circling back", "synergy",
  "game-changing", "revolutionary", "disruptive"
- Contractions are fine ("we've", "you're") — they read as human
- One emoji maximum, and only if it matches their own posting style

## Step 6 — Output format

For each prospect, produce a structured report:

```
## [Prospect Full Name] — [Title] at [Company]

### ICP Fit Score: XX/30
- Title fit: X/5
- Company size: X/5
- Industry: X/5
- Funding stage: X/5
- Trigger signals: X/5
- Tech stack: X/5

### Key Research Findings
- [Finding 1 — with source]
- [Finding 2 — with source]
- [Finding 3 — with source]

### Personalisation Hooks
1. [Primary hook — most relevant]
2. [Secondary hook — backup if primary doesn't land]

### Recommended Action: [connect | message | skip]

### Drafted Message
---
[message text here]
---

### Reasoning
[Why this message for this person at this time]
```
