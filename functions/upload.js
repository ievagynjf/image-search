const MAX_FILE_SIZE = 10 * 1024 * 1024;

export async function onRequestPost(context) {
  try {
    const req = context.request;
    const env = context.env || {};

    if (!isSameOrigin(req)) {
      return json({ ok: false, error: 'Cross-origin uploads are not allowed' }, 403);
    }

    const contentLength = Number(req.headers.get('content-length') || 0);
    if (contentLength > MAX_FILE_SIZE + 1024 * 1024) {
      return json({ ok: false, error: 'Image exceeds the 10 MB limit' }, 413);
    }

    const form = await req.formData();
    const file = form.get('file');

    if (!(file instanceof File)) {
      return json({ ok: false, error: 'No file uploaded' }, 400);
    }

    if (file.size === 0 || file.size > MAX_FILE_SIZE) {
      return json({ ok: false, error: 'Image must be between 1 byte and 10 MB' }, 413);
    }

    const image = await detectImageType(file);
    if (!image) {
      return json({ ok: false, error: 'Only valid JPG, PNG, WEBP and GIF images are accepted' }, 415);
    }

    const type = image.mime;

    if (!env.IMG_BUCKET) {
      return json({ ok: false, error: 'Cloudflare R2 bucket is not configured' }, 503);
    }

    const publicBase = (env.R2_PUBLIC_BASE_URL || '').replace(/\/$/, '');
    if (!publicBase) {
      return json({
        ok: false,
        error: 'R2_PUBLIC_BASE_URL is not configured',
        details: ['Please set Pages env var R2_PUBLIC_BASE_URL, e.g. https://img.yourdomain.com']
      }, 500);
    }

    const key = buildObjectKey(image.ext);
    await env.IMG_BUCKET.put(key, await file.arrayBuffer(), {
        httpMetadata: {
          contentType: type,
          cacheControl: 'public, max-age=3600'
        }
    });

    return json({
      ok: true,
      url: `${publicBase}/${key}`,
      provider: 'cloudflare-r2',
      type
    });
  } catch (err) {
    return json({ ok: false, error: String(err?.message || err) }, 500);
  }
}

function buildObjectKey(ext) {
  const rand = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `uploads/${y}/${m}/${day}/${rand}${ext}`;
}

async function detectImageType(file) {
  const b = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) {
    return { mime: 'image/jpeg', ext: '.jpg' };
  }
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 && b[4] === 0x0d && b[5] === 0x0a && b[6] === 0x1a && b[7] === 0x0a) {
    return { mime: 'image/png', ext: '.png' };
  }
  if (b.length >= 6 && b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x38 && (b[4] === 0x37 || b[4] === 0x39) && b[5] === 0x61) {
    return { mime: 'image/gif', ext: '.gif' };
  }
  if (b.length >= 12 && b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 && b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) {
    return { mime: 'image/webp', ext: '.webp' };
  }
  return null;
}

function isSameOrigin(req) {
  const origin = req.headers.get('origin');
  return !!origin && origin === new URL(req.url).origin;
}

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      ...extraHeaders
    }
  });
}
