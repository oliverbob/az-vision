import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    host: "0.0.0.0",
    port: Number(process.env.FRONTEND_PORT ?? "4040"),
    strictPort: true,
    hmr: {
      host: process.env.VITE_HMR_HOST || undefined,
      protocol: (process.env.VITE_HMR_PROTOCOL as "ws" | "wss" | undefined) || undefined,
      clientPort: process.env.VITE_HMR_CLIENT_PORT
        ? Number(process.env.VITE_HMR_CLIENT_PORT)
        : undefined,
    },
  },
});