import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { PAGES, SITE_URL } from './src/pageMeta.ts'

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

/**
 * A copy of index.html per page (permitting.html, affordable.html, ...) with
 * that page's title, description and URL in the <head>. Link previews read
 * the raw HTML, so this is what makes a shared /affordable link say
 * "Affordable Units". Netlify serves /affordable from affordable.html before
 * the SPA rewrite in public/_redirects applies.
 */
function pageHeads(): Plugin {
  return {
    name: 'page-heads',
    apply: 'build',
    enforce: 'post',
    generateBundle(_, bundle) {
      const index = bundle['index.html']
      if (!index || index.type !== 'asset') throw new Error('page-heads: index.html not in bundle')
      const html = String(index.source)
      for (const p of PAGES) {
        if (!p.path) continue
        const url = `${SITE_URL}/${p.path}`
        let out = html
          .replace(/<title>[^<]*<\/title>/, `<title>${esc(p.title)}</title>`)
          .replace(/(<link rel="canonical" href=")[^"]*/, `$1${url}`)
          .replace(/(<meta property="og:url" content=")[^"]*/, `$1${url}`)
        for (const k of ['name="description"', 'property="og:description"', 'name="twitter:description"'])
          out = out.replace(new RegExp(`(<meta ${k} content=")[^"]*`), `$1${esc(p.description)}`)
        for (const k of ['property="og:title"', 'name="twitter:title"'])
          out = out.replace(new RegExp(`(<meta ${k} content=")[^"]*`), `$1${esc(p.title)}`)
        if (out.includes('— A City That Works</title>')) throw new Error(`page-heads: title not replaced for ${p.path}`)
        this.emitFile({ type: 'asset', fileName: `${p.path}.html`, source: out })
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), pageHeads()],
})
