export async function onRequestPost(context) {
  try {
    const req = context.request;
    const env = context.env || {};
    const form = await req.formData();
    const file = form.get('file');

    if (!(file instanceof File)) {
      return json({ ok: false, error: 'No file uploaded' }, 400);
    }

    const name = sanitizeName(file.name || 'image.bin');
    const type = file.type || 'application/octet-stream';

    // 1) Best path: your own Cloudflare R2 bucket (stable)
    if (env.IMG_BUCKET) {
      const key = buildObjectKey(name);
      await env.IMG_BUCKET.put(key, await file.arrayBuffer(), {
        httpMetadata: {
          contentType: type,
          cacheControl: 'public, max-age=31536000, immutable'
        }
      });

      const publicBase = (env.R2_PUBLIC_BASE_URL || '').replace(/\/$/, '');
      if (!publicBase) {
        return json({
          ok: false,
          error: 'R2 uploaded, but R2_PUBLIC_BASE_URL is not configured',
          details: ['Please set Pages env var R2_PUBLIC_BASE_URL, e.g. https://img.yourdomain.com']
        }, 500);
      }

      return json({
        ok: true,
        url: `${publicBase}/${key}`,
        provider: 'cloudflare-r2',
        type
      });
    }

    // 2) Fallback path: anonymous third-party mirrors (unreliable)
    const errors = [];

    try {
      const out = await tryTelegraph(file, name);
      return json({ ok: true, url: out.url, provider: out.provider, type });
    } catch (e) {
      errors.push(String(e?.message || e));
    }

    try {
      const out = await try0x0(file, name);
      return json({ ok: true, url: out.url, provider: out.provider, type });
    } catch (e) {
      errors.push(String(e?.message || e));
    }

    try {
      const out = await tryCatbox(file, name);
      return json({ ok: true, url: out.url, provider: out.provider, type });
    } catch (e) {
      errors.push(String(e?.message || e));
    }

    return json({
      ok: false,
      error: 'All upstream upload providers failed',
      details: [
        ...errors,
        'Tip: bind an R2 bucket as IMG_BUCKET and set R2_PUBLIC_BASE_URL for stable uploads.'
      ]
    }, 502);
  } catch (err) {
    return json({ ok: false, error: String(err?.message || err) }, 500);
  }
}

async function tryTelegraph(file, name) {
  const fd = new FormData();
  fd.append('file', file, name);
  const res = await fetch('https://telegra.ph/upload', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`telegra.ph HTTP ${res.status}`);
  const data = await res.json();
  if (!Array.isArray(data) || !data[0] || !data[0].src) {
    throw new Error(`telegra.ph invalid response: ${JSON.stringify(data)}`);
  }
  return { url: 'https://telegra.ph' + data[0].src, provider: 'telegra.ph' };
}

async function try0x0(file, name) {
  const fd = new FormData();
  fd.append('file', file, name);
  const res = await fetch('https://0x0.st', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`0x0 HTTP ${res.status}`);
  const text = (await res.text()).trim();
  if (!/^https?:\/\//.test(text)) throw new Error('0x0 invalid response');
  return { url: text, provider: '0x0.st' };
}

async function tryCatbox(file, name) {
  const fd = new FormData();
  fd.append('reqtype', 'fileupload');
  fd.append('userhash', '');
  fd.append('fileToUpload', file, name);
  const res = await fetch('https://catbox.moe/user/api.php', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`catbox HTTP ${res.status}`);
  const text = (await res.text()).trim();
  if (!/^https?:\/\//.test(text)) throw new Error(`catbox invalid response: ${text}`);
  return { url: text, provider: 'catbox.moe' };
}

function buildObjectKey(name) {
  const rand = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const ext = getExt(name);
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `uploads/${y}/${m}/${day}/${rand}${ext}`;
}

function getExt(name) {
  const i = name.lastIndexOf('.');
  if (i === -1) return '';
  const ext = name.slice(i).toLowerCase();
  return /\.[a-z0-9]{1,8}$/.test(ext) ? ext : '';
}

function sanitizeName(name) {
  return String(name).replace(/[^\w.-]+/g, '_');
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store'
    }
  });
}
