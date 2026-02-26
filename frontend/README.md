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

`http://127.0.0.1:9090/`

The local frontend API route is a caller/proxy only. It forwards requests to OpenAI-compatible endpoints on the configured backend base URL:

- text-to-image/chat:

`POST /v1/chat/completions`

- image-to-image edits (when user attaches an image):

`POST /v1/images/edits`

You can now attach one or more images in the UI. If multiple images are attached, they are sent together and combined server-side before instruction-based editing.

The frontend route returns:

```json
{
  "reply": "string",
  "imageUrl": "string|null"
}
```

If `MODEL_CHAT_URL` is not set, it defaults to `http://127.0.0.1:9090/` and still calls the backend.

## 3) Run

```bash
npm run dev

# Or run on 4040
npm run dev -- --port 4040
```

Open: `http://localhost:4040`

## Reverse proxy (Caddy HTTPS)

If you expose `npm run dev` through Caddy and see WebSocket/HMR errors (`Failed to connect to websocket`, `502` on `+page.svelte`), set these in `frontend/.env`:

```dotenv
VITE_HMR_HOST=your-domain.example
VITE_HMR_PROTOCOL=wss
VITE_HMR_CLIENT_PORT=443
```

Then restart the frontend dev server.

Minimal Caddy example:

```caddyfile
your-domain.example {
  encode gzip zstd
  reverse_proxy 127.0.0.1:4040
}
```

For public hosting, prefer production mode instead of Vite dev:

```bash
npm run build
npm run preview -- --host 0.0.0.0 --port 4040
```