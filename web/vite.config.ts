import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The build is committed (src/kb_intake/service/static) and served by kb-intake at /ui/; CI rebuilds it and
// diffs, so every output name is a content hash and nothing depends on the time or the machine.
export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  build: {
    outDir: '../src/kb_intake/service/static',
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 2048,
    license: false,
    rolldownOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  server: {
    proxy: { '/v1': 'http://127.0.0.1:8700' },
  },
});
