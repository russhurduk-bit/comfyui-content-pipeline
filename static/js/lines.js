/**
 * Dashboard — lines list logic.
 */

let lines = [];

async function loadLines() {
  try {
    lines = await API.get('/api/lines');
    renderLines();
  } catch (e) {
    showToast('Failed to load lines: ' + e.message, 'error');
  }
}

function renderLines() {
  const el = document.getElementById('linesList');
  const empty = document.getElementById('emptyState');

  if (!lines.length) {
    el.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  el.innerHTML = lines.map(l => `
    <div class="card p-4 mb-3 flex items-center gap-4 cursor-pointer hover:border-purple-500/50 transition"
         onclick="openLine(${l.id})">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-3">
          <span class="font-semibold truncate">${esc(l.name)}</span>
          <span class="badge badge-${l.status}">${l.status}</span>
        </div>
        <div class="flex gap-4 mt-1 text-xs" style="color:var(--text-dimmer)">
          <span>Photos: ${l.photo_accepted}/${l.photo_count}</span>
          <span>Videos: ${l.video_done}/${l.video_count}</span>
          <span>${formatDate(l.created_at)}</span>
        </div>
      </div>
      <div class="flex gap-2 flex-shrink-0">
        <button class="btn-icon" onclick="event.stopPropagation(); deleteLine(${l.id}, '${esc(l.name)}')" title="Delete">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>
  `).join('');
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function openLine(id) {
  window.location.href = `/line?id=${id}`;
}

async function deleteLine(id, name) {
  if (!confirm(`Delete line "${name}"? This cannot be undone.`)) return;
  try {
    await API.del(`/api/lines/${id}`);
    showToast('Line deleted');
    loadLines();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

function showNewLineForm() {
  document.getElementById('newLineForm').style.display = 'block';
  document.getElementById('newName').focus();
  loadFacePresets();
}

function hideNewLineForm() {
  document.getElementById('newLineForm').style.display = 'none';
}

async function loadFacePresets() {
  try {
    const faces = await API.get('/api/face-presets');
    const sel = document.getElementById('newFace');
    sel.innerHTML = faces.map(f =>
      `<option value="${f.name}" ${f.name === 'reference_face.jpg' ? 'selected' : ''}>${f.name}</option>`
    ).join('');
    if (!faces.length) {
      sel.innerHTML = '<option value="reference_face.jpg">reference_face.jpg (default)</option>';
    }
  } catch {}
}

async function createLine() {
  const name = document.getElementById('newName').value.trim();
  if (!name) {
    showToast('Name is required', 'error');
    return;
  }

  try {
    const data = {
      name,
      face_ref_name: document.getElementById('newFace').value,
      drive_folder_id: document.getElementById('newDriveFolder').value.trim(),
    };
    const result = await API.post('/api/lines', data);
    showToast('Line created');
    hideNewLineForm();
    // Open the line immediately
    openLine(result.id);
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  loadLines();

  // GPU status
  async function updateGPU() {
    const gpu = await checkGPU();
    document.getElementById('gpuStatus').innerHTML = gpuStatusHTML(gpu);
  }
  updateGPU();
  setInterval(updateGPU, 30000);
});
