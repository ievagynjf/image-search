export async function onRequestPost(context) {
  try {
    const req = context.request;
    const form = await req.formData();
    const file = form.get('file');

    if (!(file instanceof File)) {
      return json({ ok: false, error: 'No file uploaded' }, 400);
    }

    const name = file.name || 'image.bin';
    const type = file.type || 'application/octet-stream';

    const tryTelegraph = async () => {
      const fd = new FormData();
      fd.append('file', file, name);
      const res = await fetch('https://telegra.ph/upload', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(`telegra.ph HTTP ${res.status}`);
      const data = await res.json();
      if (!Array.isArray(data) || !data[0] || !data[0].src) {
        throw new Error(`telegra.ph invalid response: ${JSON.stringify(data)}`);
      }
      return { url: 'https://telegra.ph' + data[0].src, provider: 'telegra.ph' };
    };

    const try0x0 = async () => {
      const fd = new FormData();
      fd.append('file', file, name);
      const res = await fetch('https://0x0.st', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(`0x0 HTTP ${res.status}`);
      const text = (await res.text()).trim();
      if (!/^https?:\/\//.test(text)) throw new Error('0x0 invalid response');
      return { url: text, provider: '0x0.st' };
    };

    const tryCatbox = async () => {
      const fd = new FormData();
      fd.append('reqtype', 'fileupload');
      fd.append('userhash', '');
      fd.append('fileToUpload', file, name);
      const res = await fetch('https://catbox.moe/user/api.php', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(`catbox HTTP ${res.status}`);
      const text = (await res.text()).trim();
      if (!/^https?:\/\//.test(text)) throw new Error(`catbox invalid response: ${text}`);
      return { url: text, provider: 'catbox.moe' };
    };

    const errors = [];

    try {
      const out = await tryTelegraph();
      return json({ ok: true, url: out.url, provider: out.provider, type });
    } catch (e) {
      errors.push(String(e?.message || e));
    }

    try {
      const out = await try0x0();
      return json({ ok: true, url: out.url, provider: out.provider, type });
    } catch (e) {
      errors.push(String(e?.message || e));
    }

    try {
      const out = await tryCatbox();
      return json({ ok: true, url: out.url, provider: out.provider, type });
    } catch (e) {
      errors.push(String(e?.message || e));
    }

    return json({
      ok: false,
      error: 'All upstream upload providers failed',
      details: errors
    }, 502);
  } catch (err) {
    return json({ ok: false, error: String(err?.message || err) }, 500);
  }
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
