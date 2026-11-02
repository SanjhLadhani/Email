# MailMind AI — Flow & Techniques

A single reference for how the whole system fits together and the specific techniques
used in each part. For setup/usage, see [README.md](README.md); this file is about
*how it works*, not *how to run it*.

## The big picture

```
Colab GPU (or Gemini API) <-> config.py
        ^ announces URL via ntfy.sh

Gmail (IMAP) -> automation/step2_gmail_reader.py -> agent/step1_email_agent.py (the agent)
                                                           |
                                                automation/step3_storage.py (SQLite)
                                                           |
                                    dashboard/step6_api.py (Flask) <-> dashboard/static/index.html
                                                           |
                                   automation/step5_drafts.py (Gmail Drafts) -> you send from Gmail
                                                           |
                                   automation/step4_pipeline.py checks Gmail again (sync_drafts)
```

Everything runs as one scheduled loop (`automation/autopilot.py`), with the dashboard as
the window into it.

---

## 1. Where the "brain" runs - `config.py`

Your PC can't run an LLM without freezing, so the model never runs locally. Two sources,
tried in order (`MODEL_ORDER`):

- **Colab GPU**: you run `colab_ollama_server.ipynb` in Google Colab (free T4 GPU), which
  installs Ollama, pulls a model, opens a Cloudflare tunnel, and **announces its own URL**
  to a private topic on `ntfy.sh` every few minutes. Your local `config.py` polls that
  topic, takes the newest announcement, and only trusts URLs ending in `.trycloudflare.com`
  (an allowlist - anyone who learned your topic name could otherwise point your agent at
  their own server).
- **Hosted API fallback**: if Colab isn't running, `FALLBACK_PROVIDER`/`FALLBACK_API_KEY`
  in `.env` points at a real API (we used Gemini's free tier). `FALLBACK_MODEL` can be a
  **comma-separated list** of models - wrapped in Pydantic AI's `FallbackModel`, so if one
  model's free quota is exhausted (HTTP 429), the next one in the list is tried
  automatically, same request.

Both results are cached briefly (`max_age`) so we're not re-checking on every single email.

---

## 2. Reading Gmail - `automation/step2_gmail_reader.py`

Raw IMAP (`imaplib`), with a few deliberate techniques:

- **`BODY.PEEK`** instead of `BODY` for fetching - this is the one line that guarantees
  reading an email never marks it as read.
- **`X-GM-RAW "category:primary"`** - Gmail's own search syntax over IMAP, used to match
  what the Gmail sidebar actually calls "Primary" (plain `UNSEEN` counts *all* tabs, which
  doesn't match what you see in Gmail).
- **`X-GM-THRID`** (Gmail's thread ID) - used to de-duplicate conversations so the unread
  count counts conversations, not individual messages, matching Gmail's own counter.
- **Date-range fetch**: IMAP's `SINCE`/`BEFORE` only understands whole days in the
  *server's* timezone, so we ask for one extra day of padding on each side, then re-check
  each message's real `Date` header in your local timezone to keep only the exact days you
  asked for (`_numbers_between`).
- **The one mutating function, `set_seen()`**: flips the `\Seen` flag when you press
  Dismiss/Restore in the dashboard. Everything else opens the mailbox `readonly=True`.

---

## 3. The agent - `agent/step1_email_agent.py` + `agent/agent_tools.py`

This is the part that makes it a real **agent**, not a fixed prompt:

- Built with **Pydantic AI**. The model is given an `output_type` that's a Pydantic schema
  (`EmailAnalysis`) - the library forces the model's answer to validate against that
  schema, retrying if it doesn't.
- It's given **tools it can choose to call** before answering: `sender_history`,
  `search_my_emails`, `did_i_already_reply`. Each is read-only and backed by either SQLite
  or a Gmail Sent-folder check. The model decides for itself whether it needs one, calls
  it, reads the result, and only then produces its final structured answer - that
  tool-call loop (model -> tool -> result -> model...) is what separates this from "just
  send one prompt, get one answer."
- Capped with `UsageLimits(request_limit=8, tool_calls_limit=4)` so a confused model can't
  loop forever.
- `describe_steps()` walks the finished conversation's messages and turns the tool calls
  into the "what the agent checked" list you see in the dashboard - filtering out the
  model's own *final answer*, which Pydantic AI also represents internally as a tool call.
- **Dynamic judgment over hardcoded rules**: whether a sender is a real person
  (`sender_is_person`) and what name to greet them by (`reply_name`) are fields the *model
  itself* judges from the email, not a regex list of "company-sounding" names - that was an
  explicit correction partway through, since the regex approach was brittle (it greeted a
  registrar "Hi KFIN," and told them to verify your own shares).
- **Hard rule, not left to the model**: a no-reply address (`is_noreply()`, a regex) never
  gets a drafted reply, full stop - enforced in code after the model runs, not trusted to
  the prompt.
- Model quirk worked around: Gemini rejects combining JSON-mode output with tool-calling
  (HTTP 400), so the hosted-API path uses plain **tool-call-based structured output**
  instead of forced JSON mode; the local Ollama path (which handles JSON mode fine, and has
  no tools) still uses `NativeOutput`.

---

## 4. Personalization - `agent/user_profile.py` + feedback loop

Two different trust levels, by design:
- `profile.json` - written by you, so it's trusted and goes straight into the agent's
  **instructions**.
- Your past corrections (urgency you changed, replies you edited) - shown as **untrusted
  reference examples** in the prompt, not instructions, so a weird past correction can't
  quietly become a new rule the model obeys blindly.

---

## 5. Storage - `automation/step3_storage.py` (SQLite)

- One `emails` table keyed by `message_id`, one `feedback` table for corrections.
- Schema **migrations happen automatically**: `PRAGMA table_info` checks which columns
  exist, `ALTER TABLE ADD COLUMN` adds any that are missing - so the database upgrades
  itself in place every time a new field was added during this project, instead of needing
  a manual migration step or wiping the db.
- `status` column drives everything downstream: `new -> dismissed | drafted -> replied`.

---

## 6. The pipeline - `automation/step4_pipeline.py`

Ties it together: fetch -> skip already-processed -> analyze -> store, plus two "sync"
passes before that:
- `backfill_dates()` - fills in missing sent-dates for old rows.
- `sync_drafts()` - checks Gmail for what happened to drafts you made: found in Sent Mail
  -> `replied`; vanished entirely -> back to `new`, but only after being missing **twice in
  a row** (avoids a false "you deleted it" the moment Gmail hasn't indexed a brand-new
  draft yet).

Both are best-effort - wrapped in `try/except` so a Gmail hiccup never blocks the actual
triage run.

---

## 7. Drafting - `automation/step5_drafts.py`

Builds a real `EmailMessage`, threaded under the original conversation via
`In-Reply-To`/`References`, and **appends it to Gmail's Drafts folder over IMAP** - nothing
is ever sent automatically. `draft_states()` later re-reads Drafts/Sent by that same header
to figure out what happened to it.

---

## 8. The API + dashboard - `dashboard/`

- **Flask** JSON API (`step6_api.py`) - thin: each endpoint mostly just calls into
  storage/pipeline/gmail and mocks nothing itself.
- **Single-file HTML/CSS/JS dashboard**, no framework, no build step - plain DOM
  re-rendering off one JS `state` object.
- **Dual-caching** on `/api/unread`: `fresh` recounts everything (~10-13s over IMAP),
  `quick` reuses the Gmail totals from the last 10 minutes and only recounts the handled
  subset (fast, used right after a dismiss/draft).
- **Inline SVG sparklines** - a tiny hand-written polyline generator, no charting library.
- **CSS custom properties** (`:root` tokens) for the whole color system, so the Midnight
  Violet palette was a find-and-replace at the token level, not per-element.

---

## 9. Safety / process techniques used throughout

- **Secret scanning before every push** - grepped the diff for `EMAIL_APP_PASSWORD`, API
  keys, `NTFY_TOPIC` before committing.
- **`.gitignore`** keeps `.env`, `triage.db`, `profile.json`, logs out of git entirely.
- **Headless Chrome (CDP)** used to actually click buttons and screenshot the dashboard to
  verify UI changes worked, instead of trusting the code alone.
- **Unit test suite** (`tests/`, 134 tests): SQLite tests run against a disposable per-test
  file, Gmail/model calls are mocked out everywhere, so the suite is fast and never touches
  your real inbox or `triage.db`. This is separate from `agent/evals.py`, which scores the
  *model's own judgment* against 23 labelled real emails (currently ~78%) - the unit tests
  check the code around the model, the evals check the model's decisions.

---

That's the whole stack - one Colab-hosted (or Gemini-fallback) model, one tool-using agent,
one SQLite memory, one Flask+vanilla-JS dashboard, and a pipeline that keeps all three
honest against what's actually sitting in your Gmail.
