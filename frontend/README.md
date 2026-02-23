# Z-Image Chat Frontend (SvelteKit)

Mobile-first chat UI for fast model interface prototyping.

## 1) Install

```bash
cd frontend
npm install
```

## 2) Configure backend (optional)

```bash
cp .env.example .env
```

Set `MODEL_CHAT_URL` to your chat backend endpoint. By default, `.env.example` points to:

`http://127.0.0.1:9090/api/chat`

The local frontend API route supports either:

- simple backend shape:

```json
{
  "message": "string",
  "history": [{ "role": "user|assistant", "content": "string" }]
}
```

- or Ollama-like backend shape (`/api/chat`) with image data.

Simple shape returns:

```json
{
  "reply": "string"
}
```

If `MODEL_CHAT_URL` is not set, `/api/chat` returns a local echo reply so UI can be tested immediately.

## 3) Run

```bash
npm run dev

# Or run on 4040
npm run dev -- --port 4040
```

Open: `http://localhost:4040`