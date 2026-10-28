# MailMind AI

An intelligent agent that understands, prioritizes, and manages emails.

Reads your Gmail, rates each email's urgency (critical / high / medium / low), sorts it into a
category, and drafts a reply you can review. It never sends anything: replies are saved to your
Gmail **Drafts** only after you press the button.

No model ever runs on your own computer.

## Fully hands-free setup (do this once)

1. Create a free API key (about a minute):
   - Groq: https://console.groq.com/keys (default), or
   - Google Gemini: https://aistudio.google.com/apikey
2. Put it in `.env`:

   ```
   FALLBACK_PROVIDER=groq        # or gemini / openrouter
   FALLBACK_API_KEY=your-key-here
   MODEL_ORDER=api               # api = always use the API, never wait for Colab
   ```

3. Turn on automatic start, which also starts it now:

   ```powershell
   .\.venv\Scripts\python.exe install_autostart.py
   ```

From then on, every time you log in the dashboard (http://127.0.0.1:5000) and the Gmail checks start
by themselves, and nothing has to be opened or run by hand.

**Privacy:** whenever the API is used, the text of each email (the first 4000 characters) is sent
to that provider. Check its data policy, and note that some free tiers may use data to improve
their products. The Colab route keeps email text on a GPU session you control.

**Limits:** free API tiers have rate limits. If one is hit, the email is skipped and retried at the
next check (every 5 minutes).

## Optional: use the Colab GPU instead

Set `MODEL_ORDER=colab,api` (the default) to prefer Colab and fall back to the API when Colab is off.
Open `colab_ollama_server.ipynb` in VS Code, pick a Colab GPU server and run the one cell. The
notebook announces its own address, so nothing needs copying. A free Colab session ends after a few
hours or when idle; with an API key set, the agent simply switches to the API meanwhile.

The dashboard header shows which model is answering: `<model> online · API` or `· Colab`, or a red
**Model offline** if neither is available.

## Start automatically at login

```powershell
.\.venv\Scripts\python.exe install_autostart.py            # turn on (and start it now)
.\.venv\Scripts\python.exe install_autostart.py --status   # installed? running?
.\.venv\Scripts\python.exe install_autostart.py --remove   # turn off
```

Output goes to `autopilot.log`. To run it once by hand instead: `python autopilot.py`.

## What each file does

| File | Purpose |
| --- | --- |
| `autopilot.py` | Dashboard + checks Gmail every few minutes; waits if no model is available |
| `install_autostart.py` | Makes autopilot start at Windows login (and removes that again) |
| `config.py` | Settings; chooses Colab or the API (`MODEL_ORDER`) |
| `colab_ollama_server.ipynb` | Optional: runs Ollama + the model on a Colab GPU and publishes its address |
| `step1_email_agent.py` | The Pydantic AI agent (schemas in `schemas.py`) |
| `step2_gmail_reader.py` | Reads Gmail over IMAP (read-only, never marks mail as read) |
| `step3_storage.py` | SQLite memory: analyses, review status, your corrections |
| `step4_pipeline.py` | Fetch, skip already-analyzed, analyze, store |
| `step5_drafts.py` | Saves an approved reply to Gmail Drafts, threaded under the original |
| `step6_api.py` + `static/index.html` | Dashboard server and the one-file HTML/CSS/JS dashboard |
| `tasks.yaml` | **Every prompt** the agent uses (instructions, categories, reply rules, templates). Edit it, no restart needed; then run `python evals.py` |
| `tasks.py` | Loads `tasks.yaml` |
| `profile.json` | Your name, VIP senders, what "urgent" means to you, reply style (kept local, not in git; copy `profile.example.json` to start) |
| `evals.py`, `evals/cases.json` | Scores the agent on labelled emails |
| `reanalyze.py` | Re-analyzes stored emails after you change the prompt |
| `check_ollama.py` | Shows which model is available right now |

## Notes

- `.env` is git-ignored. It holds your Gmail app password and API key; keep it private.
- Keep `NTFY_TOPIC` private if you use Colab; it is how your computer finds the Colab address.
- Localhost model servers are refused on purpose.
- Edit `tasks.yaml` to change how the agent thinks and writes; it is re-read on every email.
- Edit `profile.json` to teach the agent who matters to you. Change an email's urgency in the
  dashboard and the agent learns from it.
