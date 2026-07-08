/**
 * Content Pipeline — shared API helpers.
 */

const API = {
  /** GET JSON */
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`GET ${path}: ${r.status}`);
    return r.json();
  },

  /** POST JSON */
  async post(path, data = {}) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || err.message || r.statusText);
    }
    return r.json();
  },

  /** PUT JSON */
  async put(path, data = {}) {
    const r = await fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!r.ok) throw new Error(`PUT ${path}: ${r.status}`);
    return r.json();
  },

  /** DELETE */
  async del(path) {
    const r = await fetch(path, { method: 'DELETE' });
    if (!r.ok) throw new Error(`DELETE ${path}: ${r.status}`);
    return r.json();
  },

  /** Upload file */
  async upload(path, file) {
    const form = new FormData();
    form.append('file', file);
    const r = await fetch(path, { method: 'POST', body: form });
    if (!r.ok) throw new Error(`Upload ${path}: ${r.status}`);
    return r.json();
  },

  /**
   * Open WebSocket with message handling.
   * Returns { ws, promise } — promise resolves on "done" message.
   */
  openWS(path, sendData, handlers = {}) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}${path}`);

    const promise = new Promise((resolve, reject) => {
      ws.onopen = () => {
        ws.send(JSON.stringify(sendData));
      };

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        // Call specific handler
        if (handlers[msg.type]) {
          handlers[msg.type](msg);
        }
        // Call catch-all
        if (handlers.message) {
          handlers.message(msg);
        }
        if (msg.type === 'done') {
          resolve(msg);
        }
      };

      ws.onerror = () => {
        if (handlers.error) handlers.error({ type: 'error', message: 'Connection lost' });
        reject(new Error('WebSocket error'));
      };

      ws.onclose = () => {
        if (handlers.close) handlers.close();
      };
    });

    return { ws, promise };
  },
};


/** Toast notification */
function showToast(message, type = 'success', duration = 3000) {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(20px)';
    toast.style.transition = '0.3s';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}


/** Format date string */
function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' +
    d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}


/** Toggle switch helper */
function setupToggle(el) {
  el.addEventListener('click', () => {
    el.classList.toggle('active');
    const knob = el.querySelector('.toggle-knob');
    knob.style.left = el.classList.contains('active') ? '22px' : '2px';
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
}


/** Setup upload zone with drag & drop */
function setupUploadZone(zoneEl, inputEl, onFile) {
  zoneEl.addEventListener('click', () => inputEl.click());
  zoneEl.addEventListener('dragover', e => { e.preventDefault(); zoneEl.classList.add('drag'); });
  zoneEl.addEventListener('dragleave', () => zoneEl.classList.remove('drag'));
  zoneEl.addEventListener('drop', e => {
    e.preventDefault();
    zoneEl.classList.remove('drag');
    if (e.dataTransfer.files.length) {
      onFile(e.dataTransfer.files[0]);
    }
  });
  inputEl.addEventListener('change', () => {
    if (inputEl.files[0]) onFile(inputEl.files[0]);
  });
}


/** GPU status checker */
async function checkGPU() {
  try {
    const data = await API.get('/api/gpu/status');
    return data;
  } catch {
    return { online: false };
  }
}


/** Render GPU status dot */
function gpuStatusHTML(gpu) {
  if (!gpu.online) {
    return `<span class="status-dot red"></span> <span class="text-sm" style="color:var(--text-dim)">PC offline</span>`;
  }
  const color = gpu.temp > 70 ? 'red' : gpu.util > 50 ? 'yellow' : 'green';
  return `<span class="status-dot ${color}"></span>
    <span class="text-sm" style="color:var(--text-dim)">${gpu.mem_used}/${gpu.mem_total}MB ${gpu.temp}°C</span>`;
}
