import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import postcss from 'postcss';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('.', import.meta.url));
const interviewStyles = resolve(root, 'src/interview/styles.css');

function interviewCss() {
  return {
    name: 'interview-css-scope',
    resolveId(id) {
      if (id === 'virtual:interview.css') return '\0interview.css';
    },
    async load(id) {
      if (id !== '\0interview.css') return;
      this.addWatchFile(interviewStyles);
      const css = postcss.parse(await readFile(interviewStyles, 'utf8'));
      css.walkRules(rule => {
        if (rule.parent.type === 'atrule' && rule.parent.name.endsWith('keyframes')) return;
        rule.selectors = rule.selectors.map(selector =>
          selector === ':root' || selector === 'body' ? '.gateway-ui' : `.gateway-ui ${selector}`
        );
      });
      return css.toString();
    },
  };
}

const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8020';
const jobSync = process.env.JOB_SYNC_URL ?? 'http://127.0.0.1:8050';

const proxyConfig = {
  '/api/jobs': { target: jobSync, changeOrigin: true },
  '/api/job-sync': { target: jobSync, changeOrigin: true, rewrite: (path) => path.replace(/^\/api\/job-sync/, '/api/sync') },
  '/api/job-stats': { target: jobSync, changeOrigin: true, rewrite: (path) => path.replace(/^\/api\/job-stats/, '/api/stats') },
  '/api': backend,
  '/ws': { target: backend, ws: true },
};

export default defineConfig({
  plugins: [react(), interviewCss()],
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      '@interview': resolve(root, 'src/interview'),
      dompurify: resolve(root, 'node_modules/dompurify/dist/purify.es.mjs'),
      marked: resolve(root, 'node_modules/marked/lib/marked.esm.js'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    proxy: proxyConfig,
  },
  preview: { proxy: proxyConfig },
});
