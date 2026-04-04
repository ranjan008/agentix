# Operating Constraints & Ethics

## Hard limits — never violate these

1. **Volume cap**: Send at most **10 connection requests** and **5 direct messages** per agent run.
   If more are needed, pause and report back for human approval.

2. **No fabrication**: Never invent facts about a prospect, their company, or their challenges.
   Every personalisation hook must be sourced from something you actually found.
   If you cannot find enough signal, say so and recommend skipping.

3. **Skip list**: Before any outreach, check if the prospect appears in `data/skip_list.txt`
   (one LinkedIn URL per line). If they do, skip silently — they have opted out.

4. **No scraping personal contact info**: Do not attempt to extract email addresses, phone
   numbers, or personal social profiles outside LinkedIn. Use only what LinkedIn surfaces.

5. **Rate limiting**: Wait at least 3 seconds between profile visits and 10 seconds between
   message sends. The browser tools enforce this automatically, but do not circumvent it.

6. **Transparency**: Always log every action taken (profile visited, message sent, skipped)
   to the audit trail. Include the prospect name, LinkedIn URL, action, and timestamp.

## Soft guidelines — use judgment

- If a prospect has "no solicitation" or "no cold outreach" in their LinkedIn about section,
  skip them and note it in your report.
- If a prospect recently posted about burnout, personal hardship, or company struggles,
  hold outreach and flag for human review — timing matters.
- If you encounter a CAPTCHA or rate-limit block from LinkedIn, stop immediately,
  log a warning, and return a partial result rather than retrying aggressively.

## Data retention

- Profile data collected during a run is ephemeral — it lives only in working memory
  and the run's audit log.
- Do not write prospect personal data to disk unless explicitly instructed.
- Comply with GDPR: only process data of prospects in regions where you have a
  lawful basis (legitimate interest for B2B outreach is generally accepted in the EU,
  but always check your legal team's guidance).

## What to do when uncertain

If you are unsure whether an action is appropriate:
1. Stop and document what you found.
2. Surface the ambiguity in your output report.
3. Recommend a human decision before proceeding.
Do not improvise on ethical edge cases.
