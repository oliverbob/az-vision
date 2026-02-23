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

Set `MODEL_CHAT_URL` to your chat backend endpoint that accepts:

```json
{
  "message": "string",
  "history": [{ "role": "user|assistant", "content": "string" }]
}
```

And returns:

```json
{
  "reply": "string"
}
```

If `MODEL_CHAT_URL` is not set, `/api/chat` returns a local echo reply so UI can be tested immediately.

## 3) Run

```bash
npm run dev
```

Open: `http://localhost:5173`