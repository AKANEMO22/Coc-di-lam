# Cóc Đi Làm — frontend

The main React interface now lives at the repository root. It combines the Cóc Đi Làm screens with the interview gateway and Live2D Linh components in `src/interview/`; the Linh model is served from `public/linh/`.

## Run locally

Requirements: Node.js 18 or newer and npm. Start the backend gateway first; the default address is `http://127.0.0.1:8020`.

```powershell
npm ci
npm run dev
```

Open `http://localhost:5173`. If the gateway is running elsewhere, set its URL before starting Vite:

```powershell
$env:BACKEND_URL="http://host:port"
npm run dev
```

Build and serve the production bundle with `npm run build` and `npm run preview`. The Docker image serves the frontend on port `8080`; its `/api` and `/ws` routes expect a gateway named `gateway` on the same Docker network.

The local login state is a demo; session data and interview reports come from the backend.
