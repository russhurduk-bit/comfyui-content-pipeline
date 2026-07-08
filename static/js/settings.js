/**
 * Settings page logic.
 */

async function loadSettings() {
  try {
    const s = await API.get('/api/settings');

    // Vast key status
    if (s.vast_api_key_set) {
      document.getElementById('vastKeyStatus').textContent = `Key set: ${s.vast_api_key_masked}`;
      document.getElementById('vastKey').placeholder = s.vast_api_key_masked;
    }

    // Drive
    document.getElementById('driveFolder').value = s.drive_default_folder || '';
    const driveEl = document.getElementById('driveStatus');
    driveEl.textContent = s.drive_credentials_exist
      ? 'Credentials found'
      : 'Credentials NOT found (set GOOGLE_OAUTH_CRED)';
    driveEl.style.color = s.drive_credentials_exist ? 'var(--green)' : 'var(--red)';

    // Prompts
    document.getElementById('defaultPositive').value = s.default_positive_prompt || '';
    document.getElementById('defaultNegative').value = s.default_negative_prompt || '';

    // Vast info
    loadVastInfo();
  } catch (e) {
    showToast('Failed to load settings: ' + e.message, 'error');
  }
}

async function loadVastInfo() {
  try {
    const v = await API.get('/api/vast/status');
    const el = document.getElementById('vastInfo');
    let html = '';
    if (v.balance !== null) {
      html += `<div>Balance: <strong>$${v.balance.toFixed(2)}</strong></div>`;
    }
    html += `<div>Tunnel: <span style="color:${v.tunnel_ok ? 'var(--green)' : 'var(--red)'}">${v.tunnel_ok ? 'Connected' : 'Not connected'}</span> (localhost:28188)</div>`;
    if (v.instances && v.instances.length) {
      html += '<div class="mt-2">Instances:</div>';
      v.instances.forEach(i => {
        html += `<div class="ml-4 text-xs">#${i.id} ${i.gpu} — ${i.status} ($${i.cost_per_hr.toFixed(2)}/hr)</div>`;
      });
    }
    el.innerHTML = html;
  } catch {
    document.getElementById('vastInfo').textContent = 'Could not load Vast info';
  }
}

async function saveVastKey() {
  const key = document.getElementById('vastKey').value.trim();
  if (!key) return;
  try {
    await API.put('/api/settings', { vast_api_key: key });
    showToast('Vast API key saved');
    document.getElementById('vastKey').value = '';
    loadSettings();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function saveDriveFolder() {
  const folder = document.getElementById('driveFolder').value.trim();
  try {
    await API.put('/api/settings', { drive_default_folder: folder });
    showToast('Drive folder saved');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function savePrompts() {
  try {
    await API.put('/api/settings', {
      default_positive_prompt: document.getElementById('defaultPositive').value,
      default_negative_prompt: document.getElementById('defaultNegative').value,
    });
    showToast('Prompts saved');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function loadFaces() {
  try {
    const faces = await API.get('/api/face-presets');
    const grid = document.getElementById('faceGrid');
    grid.innerHTML = faces.map(f => `
      <div class="relative group">
        <img src="${f.url}" class="w-full aspect-square object-cover rounded-lg border border-transparent hover:border-purple-500 transition">
        <div class="absolute bottom-0 left-0 right-0 text-center text-xs py-1 bg-black/60 rounded-b-lg truncate px-1">${f.name}</div>
      </div>
    `).join('');
  } catch {}
}

// Face upload
document.addEventListener('DOMContentLoaded', () => {
  const zone = document.getElementById('faceUploadZone');
  const input = document.getElementById('faceInput');
  setupUploadZone(zone, input, async (file) => {
    try {
      await API.upload('/api/upload/face', file);
      showToast('Face preset added');
      loadFaces();
    } catch (e) {
      showToast('Upload failed: ' + e.message, 'error');
    }
  });

  loadSettings();
  loadFaces();

  // GPU status
  async function updateGPU() {
    const gpu = await checkGPU();
    document.getElementById('gpuStatus').innerHTML = gpuStatusHTML(gpu);
  }
  updateGPU();
  setInterval(updateGPU, 30000);
});
