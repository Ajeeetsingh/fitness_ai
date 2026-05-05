## fitness-ai-main

### Run locally (Uvicorn)

Create and activate a virtual environment, then:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9001 --reload
```

### API base path

The app is configured with `root_path="/fitness"` and `API_V1_STR="/api"`, so your routes are under:

- `GET /fitness/api/fitness/workout_plan/healthz`
- `POST /fitness/api/fitness/workout_plan/plans/generate`
- `POST /fitness/api/fitness/workout_plan/plans/generate/athlete`

### Configure the LLM (single place)

The entire project uses **one single file** to decide which LLM endpoint to call:

- `app/core/llm.py`

By default it is configured for **Ollama** (local).

#### Ollama quick start

1) Install Ollama and start it (it runs on `http://localhost:11434` by default).
2) Pull the model configured in `app/core/llm.py` (default: `llama3.1:8b`).
3) Run the server and call the plan endpoints.

#### Switching to another LLM URL

Edit `LLM` in `app/core/llm.py`:

- **`provider="ollama"`**: uses `POST {base_url}/api/generate` with `{model,prompt,num_predict}`
- **`provider="generic"`**: uses `POST {base_url}` with `{"query": "...", "max_new_tokens": N}`

If your LLM API expects a different payload (e.g. OpenAI chat completions), update the adapter logic in `app/core/llm.py`.

