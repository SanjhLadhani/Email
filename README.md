# MailMind AI

An intelligent agent that understands, prioritizes, and manages emails.

MailMind is a real agent, not a fixed prompt: for each email the model **decides for itself whether it
needs more context**, calls read-only tools (earlier emails from the sender, a keyword search of your
history, a check of your Sent folder for an existing reply), reads what they return, and only then gives its
verdict. The dashboard shows what it checked on every email.

MailMind reads your Gmail, rates every email's urgency (critical / high / medium / low), sorts it
into a category, explains why, and drafts a reply you can edit. A local dashboard lets you filter by
day, category and urgency, correct the agent, ask it to rewrite a draft ("say I don't have a Mac"),
and save the final reply to your Gmail **Drafts**.

**Safety guarantees (by design, not by promise):**

- It **never sends email.** The strongest thing it can do is append a draft to your Drafts folder,
  and only when you press the button.
- Its tools are **read-only look-ups.** None can send, delete, move or change anything, and each email's
  run is capped at 4 tool calls and 8 model requests.
- It **barely touches your mailbox.** Mail is opened read-only and fetched with `BODY.PEEK`, so reading never
  marks anything read, moves or deletes it. There are exactly two changes, both only when you ask: saving a
  draft, and marking one email **read** when you press **Dismiss** (**unread** again if you press Restore). The
  agent and autopilot never do either on their own, and nothing is ever moved or deleted.
- **No model runs on your computer.** Analysis is done by a hosted API (Gemini / Groq / OpenRouter)
  or by an Ollama server on a Colab GPU. A localhost model address is refused on purpose.
- **Email text is treated as untrusted data**, never as instructions to the agent.
- Email bodies are **not stored** in the database; only the analysis is.

---

## Project layout

The code is split into three folders, plus shared settings at the root.

```
Email_tracker/
├── config.py                  Shared settings: .env, which model answers (API or Colab), paths
├── .env                       Your secrets (git-ignored)
├── triage.db                  The SQLite database (git-ignored)
├── colab_ollama_server.ipynb  Optional: run the model on a Colab GPU
│
├── agent/                     THE AI: it thinks, decides and writes
│   ├── step1_email_agent.py   The Pydantic AI agent: triage + rewrite, reply composing
│   ├── agent_tools.py         The look-up tools the agent can choose to call
│   ├── schemas.py             The data types going in and out of the agent
│   ├── tasks.py + tasks.yaml  Every prompt, editable without touching code
│   ├── user_profile.py        Reads profile.json (who you are)
│   ├── profile.json           Your name, VIPs, tone (git-ignored; see profile.example.json)
│   └── evals.py + evals/      Test cases that score the agent's judgment
│
├── automation/                THE MACHINERY: it fetches, stores and schedules
│   ├── step2_gmail_reader.py  Reads Gmail over IMAP (read-only) and counts unread
│   ├── step3_storage.py       SQLite memory: analyses, status, your corrections
│   ├── step4_pipeline.py      Fetch, skip already-seen, run the agent, store
│   ├── step5_drafts.py        Saves a reply to Gmail Drafts (never sends)
│   ├── autopilot.py           The hands-free loop: dashboard + mail check every 5 minutes
│   ├── install_autostart.py   Start autopilot at every Windows login
│   ├── check_ollama.py        Which model is available right now
│   └── reanalyze.py           Re-run the agent over stored emails after a prompt change
│
└── dashboard/                 THE SCREEN: what you see and click in the browser
    ├── step6_api.py           Flask JSON API + serves the page
    └── static/index.html      The dashboard (one HTML/CSS/JS file)
```

How they relate: **`automation/` runs the agent**. The pipeline hands each email to
`agent/step1_email_agent.py`, and the agent's tools read through `automation/` (the Gmail reader and the
database) to look things up. **`dashboard/` shows the results** and calls both: it reads the database, asks the
agent to rewrite a draft, and saves drafts through `automation/`. Autopilot starts the dashboard. Nothing in
`agent/` schedules, serves or sends anything.

Run everything from the project root as a module, for example `python -m automation.autopilot`,
`python -m dashboard.step6_api` or `python -m agent.evals`.

---

## Contents

1. [Project layout](#project-layout)
   · [How it fits together](#how-it-fits-together)
2. [Quick start](#quick-start)
3. [Configuration (`.env`)](#configuration-env)
4. [File-by-file guide](#file-by-file-guide)
   - [config.py](#configpy) · [schemas.py](#schemaspy) · [tasks.py and tasks.yaml](#taskspy-and-tasksyaml) ·
     [agent_tools.py](#agent_toolspy) ·
     [user_profile.py and profile.json](#user_profilepy-and-profilejson)
   - The pipeline: [step1_email_agent.py](#step1_email_agentpy) · [step2_gmail_reader.py](#step2_gmail_readerpy) ·
     [step3_storage.py](#step3_storagepy) · [step4_pipeline.py](#step4_pipelinepy) ·
     [step5_drafts.py](#step5_draftspy) · [step6_api.py](#step6_apipy)
   - Running it: [autopilot.py](#autopilotpy) · [install_autostart.py](#install_autostartpy) ·
     [check_ollama.py](#check_ollamapy) · [reanalyze.py](#reanalyzepy)
   - Quality: [evals.py](#evalspy)
   - Non-Python: [static/index.html](#staticindexhtml) · [colab_ollama_server.ipynb](#colab_ollama_serveripynb)
5. [Database](#database)
6. [HTTP API](#http-api)
7. [How the agent learns from you](#how-the-agent-learns-from-you)
8. [Privacy](#privacy)
9. [Troubleshooting](#troubleshooting)

---

## How it fits together

```
                         ┌──────────────────────────── autopilot.py ────────────────────────────┐
                         │  every 5 min                                  dashboard thread        │
                         ▼                                                      ▲                 │
 Gmail (IMAP, read-only) ──► step2_gmail_reader ──► step4_pipeline ──► step3_storage (SQLite) ──► step6_api ──► static/index.html
                                                       │  ▲                                          │   (your browser)
                                                       ▼  │                                          ▼
                                              step1_email_agent                                step5_drafts
                                              (Pydantic AI agent)                         (Gmail Drafts folder)
                                                       │
                          tasks.yaml + profile.json ───┤  prompts and who-you-are, re-read on every email
                                                       ▼
                                    config.backend(): hosted API (Gemini/Groq) or Colab GPU (Ollama)
```

The life of one email:

1. `step2` downloads it (read-only) and turns it into an `Email` object (`schemas.py`).
2. `step4` skips it if its Message-ID is already in the database.
3. `step1` builds the prompt (`tasks.yaml` + `profile.json` + your past corrections), asks the model
   for a structured `EmailAnalysis`, then post-processes the reply (greeting, sign-off, no-reply rules).
4. `step3` stores the analysis.
5. The dashboard (`step6` + `static/index.html`) shows it. You may correct the urgency, rewrite the
   draft, or press **Save as Gmail draft**, which calls `step5`.

---

## Quick start

1. **Create a free model key** (about a minute): Gemini at https://aistudio.google.com/apikey, or Groq
   at https://console.groq.com/keys. Make sure the key's project has no billing account if you want to
   stay on the free tier.
2. **Create `.env`** (copy `.env.example`) and fill in:

   ```
   EMAIL_ADDRESS=you@gmail.com
   EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx     # a Google "app password", not your normal password
   FALLBACK_PROVIDER=gemini                   # gemini | groq | openrouter
   FALLBACK_API_KEY=your-key
   FALLBACK_MODEL=gemini-3.8-flash,gemini-3.5-flash-lite   # optional; comma-separated: next one is used when a model's free quota is used up
   MODEL_ORDER=api
   ```

   An app password needs 2-Step Verification: https://myaccount.google.com/apppasswords. IMAP must be
   enabled in Gmail settings.
3. **Copy `agent/profile.example.json` to `agent/profile.json`** and put your real name in it (see
   [user_profile.py](#user_profilepy-and-profilejson)).
4. **Install and start:**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install pydantic-ai flask python-dotenv pyyaml
   .\.venv\Scripts\python.exe -m automation.install_autostart
   ```

   This registers autopilot to start at every Windows login, and starts it now. Open
   http://127.0.0.1:5000.
5. **Optional**: `.\.venv\Scripts\python.exe -m agent.evals` scores the agent on 23 labelled emails.

To run in a console instead of the background: `.\.venv\Scripts\python.exe -m automation.autopilot` (from the
project root).

---

## Configuration (`.env`)

`.env` is git-ignored. It is re-read on every access, so editing it does not need a restart (the one
exception is `EMAIL_ADDRESS` / `EMAIL_APP_PASSWORD`, which are read once when the program starts).
A real environment variable of the same name wins over `.env`.

| Key | Meaning | Default |
| --- | --- | --- |
| `EMAIL_ADDRESS` | Your Gmail address | required |
| `EMAIL_APP_PASSWORD` | Gmail app password (spaces are ignored) | required |
| `IMAP_HOST` | IMAP server | `imap.gmail.com` |
| `FALLBACK_PROVIDER` | Hosted provider: `groq`, `gemini` or `openrouter` | `groq` |
| `FALLBACK_API_KEY` | That provider's API key | empty (API disabled) |
| `FALLBACK_MODEL` | Model name, or a comma-separated list tried in order (the next is used when one returns HTTP 429, i.e. its free quota is used up; each Gemini model has its own quota) | provider preset |
| `MODEL_ORDER` | `api`, `colab`, `colab,api` or `api,colab`: what to try, in order | `colab,api` |
| `NTFY_TOPIC` | Private topic name the Colab notebook announces its address on (Colab only) | empty |
| `OLLAMA_URL`, `OLLAMA_MODEL` | Fixed Colab address and model, if you do not use `NTFY_TOPIC` | empty, `qwen3:8b` |

---

## File-by-file guide

### config.py

*Folder: project root (shared by both folders)*

**Role:** the single place that knows *settings* and *which model answers right now*. Every other
module imports it.

**Settings.** `_env(key, default)` returns a real environment variable first, then the value in
`.env` (re-read each call, so changes apply live), then the default. `EMAIL_ADDRESS`,
`EMAIL_APP_PASSWORD` (Gmail shows app passwords in groups of four; the spaces are stripped) and
`IMAP_HOST` are loaded at import. `require_email_credentials()` returns the address and password or
raises a clear error telling you to fill in `.env`.

**Finding the Colab model.** The Colab notebook cannot be reached directly, so it *announces* its
public tunnel URL as a JSON message `{url, model, ts}` on the private ntfy.sh topic `NTFY_TOPIC`.
`_remote_settings()` polls that topic (cached 60 s), takes the announcement with the newest sender
timestamp (`ts`, because ntfy does not order messages sent within the same second), and returns it. Two safety
rules apply: the URL's host must end in `.trycloudflare.com` (`ALLOWED_REMOTE_HOSTS`), because anyone who
learns your topic name could otherwise post an address and receive your emails; and any failure
silently falls back to `.env`.

**Never local.** `ollama_url()` refuses `localhost`, `127.0.0.1`, `::1` and `0.0.0.0` and returns a
deliberately unreachable address instead. This is how "no model ever runs on this PC" is enforced in
code. `ollama_server()` and `ollama_base_url()` derive the bare server address and the OpenAI-compatible
`/v1` base URL that Pydantic AI needs.

**Choosing a backend.** `backend()` returns a `Backend(kind, base_url, model, api_key)` or `None`:

- `_colab_backend()` calls the server's `/api/tags` and requires the wanted model to be installed.
- `_api_backend()` needs `FALLBACK_API_KEY` and a known provider (`API_PRESETS` holds each provider's
  OpenAI-compatible URL and default model). It validates the key with a `GET /models`: HTTP 401/403 means
  the key is wrong (no backend); other HTTP errors such as rate limiting still count as "reachable";
  network failure means no backend.
- `MODEL_ORDER` decides which is tried first; the result is cached for 30 s. `refresh()` clears both caches.

### schemas.py

*Folder: `agent/`*

**Role:** the typed data that flows between steps. Kept free of model code so storage, API and evals can
import it cheaply.

- `Urgency` (critical, high, medium, low) and `EmailCategory` (work, personal, billing, promotion,
  support, other): enums. The model's output is validated against them, so it cannot invent a new level.
- `Email`: the **input** (`message_id`, `sender`, `subject`, `body`, `received_at`, `to`, `cc`).
  `to_prompt()` truncates the body to `MAX_BODY_CHARS` (4000) and renders it through the template in
  `tasks.yaml`.
- `EmailAnalysis`: the **output** the agent must produce: `category`, `urgency`, `summary`, `reason`,
  `requires_action`, plus two judgments the model makes: `sender_is_person` (is a human writing to
  you, or a company/bank/robot?) and `reply_name` (the first name to greet), and `suggested_reply`.
  `sender_is_person` and `reply_name` have defaults so old stored data and offline tests still validate.
- `RewrittenReply`: the output of the "Rewrite" button (`reply` text only).
- `NOREPLY_SENDER` / `is_noreply(sender)`: a regular expression for obviously automated addresses
  (`no-reply`, `do-not-reply`, `mailer-daemon`, `postmaster`, `notifications@`, `alerts@`, `bounce`). It is a safety
  net on top of the model's judgment; the model's `sender_is_person` handles the cases an address
  pattern cannot.

### tasks.py and tasks.yaml

*Folder: `agent/`*

**Role:** every prompt lives in `tasks.yaml`; `tasks.py` loads and renders it. The file is re-read
whenever its modification time changes, so you can edit the wording while autopilot is running. A
missing or invalid file raises a `RuntimeError` naming it, rather than letting the agent run without
instructions.

`tasks.yaml` sections:

- `analyze_email.agent_instructions`: the system prompt, built from named parts joined in the order
  listed under `order` (`role`, `urgency_levels`, `categories`, `fyi_messages`, `reply_rules`,
  `urgency_context`, `safety`). Add a rule by editing a part, or add a part and list it in `order`.
- `analyze_email.email_prompt`: how one email is shown to the model. Placeholders: `{today}`,
  `{received}`, `{sender}`, `{recipients}` (To/Cc lines), `{subject}`, `{body}`.
- `rewrite_reply`: instructions and prompt for the "Rewrite" button.
- `past_decisions`: wording of the "your earlier corrections" block (see
  [learning](#how-the-agent-learns-from-you)).
- `profile`: sentence templates for lines built from `profile.json`.

`tasks.py` functions: `load()` (cached parse), `agent_instructions()`, `email_prompt(...)` (adds today's
date, the received time in your local zone, and To/Cc), `rewrite_instructions()`, `rewrite_prompt(...)`,
`past_decisions()`, `profile_templates()`. After changing prompts, run `evals.py`.

### user_profile.py and profile.json

*Folder: `agent/`*

**Role:** tells the agent who *you* are. You write `profile.json` yourself, so it is **trusted** and goes
into the agent's instructions (unlike email text or past examples). It is git-ignored; copy
`profile.example.json` to start. Empty fields are ignored; the file is re-read on every email.

| Key | Used for |
| --- | --- |
| `name` | Sign-off ("Best regards, Your Name"), Gmail's From field, "the recipient is ..." |
| `about` | One or two sentences about you and your work |
| `vip_senders` | Names, addresses or domains that deserve at least "high" (the prompt warns the model that display names can be faked) |
| `low_priority_senders` | Senders that are rarely urgent |
| `urgent_means` | What "urgent" means in your life, in your own words |
| `reply_style` | How replies should sound |

Functions: `load_profile()` (empty profile if the file is missing or broken), `signature_name()`,
`render_profile()` (turns filled fields into prompt lines via the templates in `tasks.yaml`).

### agent_tools.py

*Folder: `agent/`*

**Role:** the agent's tools. This file is what turns the triage step from "one prompt, one answer" into an
agent loop: model, then tool call, then result, then model again, until it answers. Pydantic AI runs the loop;
this file defines what the model is allowed to look up.

All tools are **read-only**, their results are shown to the model as *data, never instructions*, and a failed
look-up returns a message instead of breaking the analysis. `LIMITS` caps one email at 4 tool calls and 8 model
requests, so a confused model cannot loop forever.

| Tool | What it does | When the prompt tells the model to use it |
| --- | --- | --- |
| `sender_history` | Earlier stored emails from this address, then this domain: date, urgency, whether you dismissed or drafted | Unfamiliar sender, or a recurring pattern |
| `search_my_emails(query)` | Earlier stored emails whose subject, sender or summary contains a keyword | A project, order number or topic that may have come up before |
| `did_i_already_reply(subject)` | Searches Gmail **Sent Mail** (last 30 days, read-only) for that subject | Reminders and follow-ups: if you already replied, no action is needed |

The model is told to skip tools for obvious newsletters and automated notices, so most emails cost one request.

- `TriageContext` carries the email being analyzed to the tools (so it is never listed as its own history)
  and an `allow_lookups` switch. `evals.py` turns look-ups off so scores depend on the email alone.
- `describe_steps(messages)` turns a finished run into `[{label, result}]`. It is stored and shown on the card
  as **What the agent checked**, for example *Checked Sent mail for a reply to "REMINDER..."* with
  *No reply to this subject in Sent mail in the last 30 days.*

The wording that tells the model when to use them is the `tools` part of `tasks.yaml`.

### step1_email_agent.py

*Folder: `agent/`*

**Role:** the agent. Everything that talks to the language model is here.

**Building the agent: `get_agent(task)`.** Picks the backend that is available *right now* and builds a
Pydantic AI `Agent` for it, cached per task and backend (so Colab going away and an API taking over
just works). `task` is `"analyze"` (output `EmailAnalysis`, instructions from `tasks.yaml` + profile) or
`"rewrite"` (output `RewrittenReply`). Only the `"analyze"` agent gets the tools from `agent_tools.py`.
The output mode differs by backend on purpose: Colab/Ollama uses `NativeOutput` (schema-constrained JSON,
far more reliable on small models), while a hosted API answers through a tool call (Gemini rejects JSON-mode
output combined with tools, with HTTP 400).
`retries=3` lets Pydantic AI re-ask when output fails validation. If no backend exists it raises the
"No model is available" message instead of hanging.

**`analyze_email(email, use_feedback=True)`.** The main entry point:

1. Render the email prompt; prepend `feedback_block(sender)` (your past corrections) when
   `use_feedback` is on. Evals turn it off so scores do not drift.
2. Run the agent loop with the email as context. The model may call its tools before answering; the run is
   capped by `agent_tools.LIMITS`. The result is a validated `EmailAnalysis`, and the tool calls are recorded
   as its `steps`.
3. Apply the hard rules on the reply. If the sender looks like a no-reply address *or* the model said
   `sender_is_person` is false, the reply is set to empty. Otherwise `compose_reply` wraps it.

**Reply post-processing.** Small models disobey "no greeting, no sign-off", so the code removes them
and adds its own:

- `clean_body()` strips a model-written greeting (`Hi Chloe,`) and sign-off (`Best regards,\nName`,
  and the run-on form `... soon? Best, Name`), replaces placeholders like `[Your Name]` with your real
  name and `[Sender Name]` with the sender's first name, and leaves other placeholders visible so you notice them.
- `compose_reply(body, sender, name)` returns `Hi <name>,` + body + `Best regards,\n<your name>`, or
  `Hello,` when no name is known. `name` is the model's `reply_name`; if omitted, the first word of the
  sender's display name is used.

**`rewrite_reply(email, current_reply, instruction)`.** Backs the dashboard's Rewrite button. It sends the
original email, the current draft and *your* instruction (trusted) to the rewrite agent, keeps whoever
the draft already greets, and re-wraps the result. It refuses no-reply addresses and empty results.

**`feedback_block(sender)`** renders up to 4 urgency corrections and 2 of your edited replies (same
sender domain first, then newest) as a labelled, untrusted reference block.

Run `python -m agent.step1_email_agent` to analyze three built-in sample emails.

### step2_gmail_reader.py

*Folder: `automation/`*

**Role:** reads Gmail over IMAP. Reading is read-only (`BODY.PEEK`), so it never marks mail as read. The one exception is
`set_seen`, called when you press Dismiss or Restore.

- `fetch_emails(limit, unread_only, since, until)`: returns the newest `limit` emails, newest first. With
  `since`/`until` (your local days, inclusive) it fetches that day range. Gmail's IMAP `SINCE`/`BEFORE`
  work in whole days in the server's time zone, so `_numbers_between()` asks for one extra day on each
  side, downloads only the `Date` header of the candidates, and keeps those whose *local* date is in
  range; only the selected emails are then downloaded in full. This is why "Yesterday" means your
  yesterday.
- `_extract_body()` prefers the `text/plain` part and falls back to HTML with tags stripped; attachments
  are skipped. `_decode()` decodes encoded headers (names with accents, emoji). `_received_at()` normalises
  the `Date` header to ISO-8601 UTC.
- `fetch_body(message_id)`: reads one email's text on demand (used by Rewrite, because bodies are not
  stored). Returns `None` if it is no longer in the inbox.
- `fetch_received_dates(ids)`: looks up the sent date of emails stored before dates were recorded.
- `set_seen(message_id, seen)`: flips one email's read flag in Gmail (the only mailbox change besides drafts). Called by the
  dashboard when you press **Dismiss** (marks it read) or **Restore** (marks it unread again); returns whether the flag
  changed and whether the email is in the Primary tab. Never called by the agent or autopilot.
- `unread_counts()`: unread totals: `all` (unread messages in the whole inbox) and `primary` (unread *conversations* in
  the Primary tab, matching Gmail's sidebar), using Gmail's `X-GM-RAW` search and `X-GM-THRID`.
- `count_unread_among(ids, primary_only)`: how many of the given emails (by Message-ID) are still unread, using
  batched IMAP `OR` searches. The header count is the Primary unread count minus the ones you handled here.
- `find_sent_reply(subject, days=30)`: searches `[Gmail]/Sent Mail` read-only for a message about a subject
  (`Re:` / `Fwd:` ignored); the agent's `did_i_already_reply` tool. Subjects with non-ASCII characters cannot be
  searched and report that instead.
- Emails without a Message-ID header get a stand-in id `imap-<number>`.

Run `python -m automation.step2_gmail_reader` to print your five newest unread emails.

### step3_storage.py

*Folder: `automation/`*

**Role:** SQLite memory (`triage.db`). It exists so an email is never analyzed twice and so the
dashboard has something to show. See [Database](#database) for the tables.

- `save_result(email, analysis)`: insert or replace by Message-ID.
- `is_processed(id)`, `get_email(id)`, `list_results(urgency, status)` (sorted critical to low, then
  newest by sent date).
- `set_status(id, status)`: `new`, `dismissed`, `drafted` (a reply sits in Gmail Drafts) or `replied` (it was sent). The dashboard shows dismissed and drafted emails only in
  their own tabs, never in the Inbox or the last-fetch view. `update_reply(id, text)`: stores the final draft.
- `record_urgency_feedback(id, urgency)`: updates the email and remembers your correction as a lesson.
  `record_reply_feedback(id, reply)`: remembers a reply you edited. `feedback_examples(sender, kind, n)`:
  past corrections, same sender domain first.
- `missing_received_dates()` / `set_received_at()`: support the date backfill.
- `sender_history(sender, exclude_id)` and `search_emails(query, exclude_id)`: the queries behind the agent's
  look-up tools.
- The table is created on first use, and an older database is upgraded in place (new columns are added).

### step4_pipeline.py

*Folder: `automation/`*

**Role:** ties steps 1-3 together: **fetch, skip already-seen, analyze, store.**

`process_new_emails(limit, unread_only, since, until)` returns a `RunResult(checked, new, already, failed,
message_ids)`; `message_ids` lists every email the run looked at, which is how the dashboard shows just your last fetch:

- It first calls `backfill_dates()` (best effort, never blocks).
- Emails already in the database are counted as `already`.
- A failure on one email is printed and counted as `failed`; nothing is stored for it, so it is retried
  on the next run.

Run `python -m automation.step4_pipeline` for one pass.

### step5_drafts.py

*Folder: `automation/`*

**Role:** turns an approved reply into a Gmail *draft*. Nothing is ever sent.

- `build_draft(sender, subject, body, message_id)` builds the `EmailMessage`: From is your profile name
  plus your address, To is the original sender, subject gets `Re: ` if missing, and `In-Reply-To` /
  `References` are set to the original Message-ID so Gmail files the draft in the same conversation. It raises
  `ValueError` for no-reply addresses.
- `save_draft(...)` appends it to `[Gmail]/Drafts` with the `\Draft` flag over IMAP. (This folder name
  is for English-language Gmail; a mailbox in another language names it differently.)

### step6_api.py

*Folder: `dashboard/`*

**Role:** the web server: JSON API plus the dashboard page. Flask, bound to `127.0.0.1` only, so nothing
outside your computer can reach it. See [HTTP API](#http-api) for every endpoint. Notable behaviour:
`/api/fetch` clamps `limit` to 1-50, validates dates, and returns HTTP 503 with a readable message if no
model is available; `/api/unread` returns the Primary-tab unread count still to deal with, cached for a minute; `/api/draft` saves the draft, records your edit as feedback, and marks the email
`drafted`; `/api/sync` checks Gmail for drafts you sent or deleted; `/api/rewrite` returns a new draft but saves nothing.

Run `python -m dashboard.step6_api` for the dashboard alone (autopilot normally does this).

### autopilot.py

*Folder: `automation/`*

**Role:** the one command that runs everything hands-free: dashboard in a background thread plus a loop
that checks Gmail.

- **Single instance.** `already_running()` binds a private local port (50123). A second copy fails to
  bind and exits. (The dashboard port cannot do this, because Windows lets two programs share a port.)
- **Dashboard.** `start_dashboard()` serves `dashboard.step6_api.app` on port 5000 in a thread.
- **Model waiting.** `wait_for_model()` blocks until `check_ollama` reports a model, re-checking every
  15 s, so a Colab session ending only pauses analysis.
- **Loop.** Every `--every` minutes (default 5) it calls `process_new_emails(limit=50)` and logs
  `checked N: X new, Y already done, Z failed`. Any error is logged and retried next cycle.
- **UTF-8 output.** stdout/stderr are forced to UTF-8 so an emoji in a subject cannot crash a check
  when output goes to the log file.

Flags: `--every MINUTES`, `--limit N`, `--port N`, `--no-dashboard`, `--reanalyze` (refresh stored emails
once at startup). Stop with Ctrl+C.

### install_autostart.py

*Folder: `automation/`*

**Role:** makes autopilot start at every Windows login, with no admin rights.

It writes a small VBScript (`EmailTriageAutopilot.vbs`) to your Startup folder. The script runs the project's
`.venv` Python on `autopilot.py` in a hidden window and redirects output to `autopilot.log` (overwritten
at each login). Commands:

```powershell
.\.venv\Scripts\python.exe -m automation.install_autostart            # install and start now
.\.venv\Scripts\python.exe -m automation.install_autostart --status   # installed? running?
.\.venv\Scripts\python.exe -m automation.install_autostart --remove   # uninstall (does not stop the running copy)
```

It expects the virtual environment at `.venv`. Windows only.

### check_ollama.py

*Folder: `automation/`*

**Role:** reports which model would be used right now. (The name is historical: it checks Colab *and* the
API.) `main(verbose)` returns exit code 0 if a model is available, 1 otherwise, and prints e.g.
`OK - gemini-3.8-flash ready via the gemini API.` Autopilot and the API use it as the "is a model
available?" test. Run `python -m automation.check_ollama` any time.

### reanalyze.py

*Folder: `automation/`*

**Role:** refresh old analyses after you change the prompt, profile or model. Designed so it cannot make
things worse:

1. Stops without touching anything if no model is available.
2. Copies the database to `triage.backup-<timestamp>.db`.
3. Re-fetches your still-unread emails and re-analyzes those stored with status `new`.
4. Replaces each row **only after** its new analysis succeeds; a failure keeps the old analysis.

Emails you already drafted or dismissed are left alone. Your manual urgency changes on re-analyzed
emails are overwritten (they stay in the feedback table as examples). It uses one API request per email.

### evals.py

*Folder: `agent/` (cases in `agent/evals/cases.json`)*

**Role:** a test suite for the *agent's judgment*, so you can see whether a prompt change helped or
hurt instead of guessing. It runs every case in `agent/evals/cases.json` through `analyze_email` (feedback off
for repeatable scores) and prints `PASS`/`FAIL` per case plus a percentage.

A case has `sender`, `subject`, `body` and any of: `urgency` (acceptable list), `category` (acceptable
list), `requires_action`, `reply_empty`, `forbid_in_reply` (phrases), `reply_contains_any` (e.g. a
language check), `to`/`cc`, and `profile` (a profile used for that case only, e.g. to test VIP spoofing).
Every reply is also scanned for claiming an action was already done ("I have already reset..."), which the
agent must never say. The 23 included cases cover security alerts, phishing that spoofs a VIP, automated
invoices, newsletters, CC-only mail, deadlines, a Spanish email, an IPO allotment notice and more. Add your
own labelled emails; those are the most valuable.

```powershell
.\.venv\Scripts\python.exe -m agent.evals            # all cases (23 API requests)
.\.venv\Scripts\python.exe -m agent.evals --limit 5  # first five
```

### static/index.html

*Folder: `dashboard/static/`*

**Role:** the whole dashboard in one file (HTML + CSS + JavaScript, no build step, no libraries).

- **Header:** title, which model is answering (polled every 20 s: `online · API/Colab`, or red *Model
  offline*), the **unread counts still to deal with**: the **Primary** tab (the number Gmail shows beside Inbox) and **all tabs**
  together. Dismissing an email marks it read in Gmail, so it drops out of Gmail's own count; a drafted email
  stays unread there, so it is subtracted. They refresh every minute, after a fetch, and whenever you dismiss, restore or draft (an email
  from another tab lowers the all-tabs number, a Primary one lowers both). And
  the fetch controls: which day(s) (Newest, Today, Yesterday, Last 3 days, Pick a date),
  how many (5-50), *Unread only*, and **Fetch & analyze**. After a fetch the list shows just the emails
  that fetch looked at, newest first, with a **Show the whole inbox** button (and, for a day fetch, the date
  filter jumps to those days).
- **Stats:** counts per urgency with a share bar; click one to filter. Urgency colours are flat red /
  yellow / green; shading appears only on hover.
- **Toolbar:** view tabs (Inbox, Drafted, Replied, Dismissed: drafted, replied and dismissed emails appear **only** in
  their own tab, never in the Inbox or the last-fetch view; a draft you send from Gmail moves from Drafted to Replied by
  itself), a **Most urgent / Newest first** sort, search (subject, sender, summary, category,
  reason), category dropdown with counts (so "work" lists all job-related mail), date filter (any time,
  today, 7 / 30 days, custom range) and *Needs action*.
- **Cards:** urgency badge, subject, sender, relative time, category and *action needed* tags. Opening a
  card shows the summary, why it got that urgency, **what the agent checked** (its look-ups, when it made
  any), and a compose-style **draft** (From / To / Subject / body).
  Under the draft: an instruction box with **Rewrite**, an **Original** button that brings back the AI's
  first draft, **Save as Gmail draft**, **Open in Gmail**, an urgency correction dropdown (the agent learns
  from it) and Dismiss. Automated senders show "No reply needed" instead of a draft.
- **Behaviour:** the list reloads itself every 30 s so background results appear, but never while a card is
  open or you are typing (that would wipe an unsaved draft).

Main functions: `api()` (fetch wrapper), `pollUnread()` (header count), `load()`/`render()` (state to screen), `visibleEmails()`
(applies all filters), `renderCard()`, `fetchRange()`/`showDays()` (day fetching), `run()` (button
spinner and error toast), `pollStatus()`, `originalReply()`/`syncOriginal()` (Original button).

### colab_ollama_server.ipynb

*Folder: project root*

**Role (optional):** run the model on a free Colab GPU instead of using an API. One code cell installs
Ollama, pulls `qwen3:8b`, opens a Cloudflare quick tunnel, and announces its URL to your private ntfy topic
every 5 minutes (see [config.py](#configpy)). Setup: add a Colab secret named `NTFY_TOPIC` with the same
value as in your `.env`, choose a GPU runtime, run the cell, and set `MODEL_ORDER=colab,api`.

Caveat: Colab's FAQ lists "web service offerings not related to interactive compute", "remote proxies" and
bypassing the notebook UI as disallowed on free runtimes, which may be terminated without warning. A hosted
API is the more dependable choice.

---

## Database

`triage.db` (SQLite, git-ignored), created automatically.

**`emails`**: one row per analyzed email.

| Column | Meaning |
| --- | --- |
| `message_id` (key) | The email's Message-ID header |
| `sender`, `subject` | As received |
| `category`, `urgency`, `summary`, `reason`, `requires_action` | The analysis |
| `suggested_reply` | The draft (the final text once you save it) |
| `status` | `new`, `dismissed` or `drafted` |
| `agent_steps` | JSON list of what the agent looked up before deciding |
| `sender_is_person`, `reply_name` | The analysis's judgment of whether a person wrote it and whom to greet (used by *Write a draft*) |
| `processed_at`, `received_at` | When analyzed / when sent (ISO UTC) |

**`feedback`**: your corrections (`kind` is `urgency` or `reply`) used as examples for future emails.

---

## HTTP API

All endpoints are on `http://127.0.0.1:5000`, JSON in and out.

| Method and path | Purpose |
| --- | --- |
| `GET /` | The dashboard |
| `GET /api/config` | `{email, name, model}` for the header and Gmail links |
| `GET /api/status` | `{online, model, via}`: is a model reachable, and is it `api` or `colab` |
| `GET /api/emails` | Every stored analysis, most urgent first |
| `GET /api/sync` | Checks Gmail for drafts that were sent (email moves to *Replied*) or deleted (back to the inbox); returns `{changed}`; cached 45 s |
| `GET /api/unread` | `{unread, all_left, primary_unread, all_unread, handled, handled_all}`: `unread` = Primary-tab unread conversations minus Primary emails you dismissed/drafted here; `all_left` = all-tabs unread minus everything you handled here (cached 60 s; `?fresh=1` recounts everything, about 13 s; `?quick=1` reuses recent Gmail totals and recounts only what you handled) |
| `POST /api/fetch` | `{limit, unread_only, since, until}` analyze new mail; returns `{checked, new, already, failed, message_ids}` (503 if no model) |
| `POST /api/rewrite` | `{message_id, reply, instruction}` returns `{reply}`; saves nothing |
| `POST /api/draft` | `{message_id, reply}` saves to Gmail Drafts, marks `drafted` |
| `POST /api/status` | `{message_id, status}` with `dismissed` or `new` |
| `POST /api/urgency` | `{message_id, urgency}` correct the urgency; recorded as a lesson |

---

## How the agent learns from you

- Change an email's **urgency** in the dashboard, or **edit a reply** before saving. Both are stored in
  `feedback`.
- On later emails, up to 4 urgency corrections and 2 replies of yours (same sender domain first) are shown
  to the model as *reference examples*, labelled untrusted so they cannot act as instructions.
- `profile.json` is the *trusted* way to state lasting preferences (VIPs, what urgent means to you, tone).
- `tasks.yaml` is where you change how the agent thinks and writes.

---

## Privacy

- With a hosted API, the first 4000 characters of each analyzed email, its sender, subject, To and Cc, and
  the date are sent to the provider. Free tiers of some providers may use data to improve their products; read
  the provider's policy. Colab keeps the text on a GPU session you control. When the agent uses a tool, the
  result (for example the subjects of earlier emails from that sender, or whether a reply exists in Sent mail)
  is also sent to the provider as part of the conversation. Each tool call is an extra model request, so
  tool use also counts against a free tier's per-minute and daily quota.
- Your Gmail app password and API key live only in `.env` (git-ignored). Never paste them anywhere; if one
  leaks, revoke it and create a new one.
- Keep `NTFY_TOPIC` private (it is effectively a password for the Colab route).
- Git ignores `.env`, `triage.db`, `*.log`, `agent/profile.json`, backups and `.venv`.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Header says **Model offline** / "No model is available" | No API key and Colab is off. Set `FALLBACK_API_KEY` and `MODEL_ORDER=api` in `.env`. Run `python -m automation.check_ollama`. |
| Many `Skipped '...'` lines with a rate-limit (429) error | Your provider's own free quota is used up. Emails are retried at the next 5-minute check, so they catch up when the quota frees. |
| Fewer emails than expected after a fetch | The count is a *maximum* of the newest matching unread emails, and emails already analyzed are skipped. Untick *Unread only* or pick a day. |
| Dashboard looks stale | It auto-refreshes every 30 s unless a card is open; reload the page after code changes. |
| "Open in Gmail" opens the wrong account | The link uses your `EMAIL_ADDRESS`; check it matches the account you are signed in to. |
| A draft is missing from Gmail | The Drafts folder name `[Gmail]/Drafts` assumes English-language Gmail. |
| Autopilot "already running" | A copy holds the lock. `python -m automation.install_autostart --status`; end the old `python` process before restarting. |
| Check `autopilot.log` | Every run prints what it did; `Skipped '...'` lines say why an email failed. |
