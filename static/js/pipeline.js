/**
 * Pipeline page — single line step-by-step processing.
 */

let lineId = null;
let lineData = null;
let uploadedBodyFile = null;

// ==================== Init ====================

document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  lineId = parseInt(params.get('id'));
  if (!lineId) {
    window.location.href = '/';
    return;
  }

  // Setup toggles
  document.querySelectorAll('.toggle-track').forEach(setupToggle);

  // Setup upload zones
  setupUploadZone(
    document.getElementById('bodyUploadZone'),
    document.getElementById('bodyInput'),
    handleBodyUpload
  );
  setupUploadZone(
    document.getElementById('videoRefUploadZone'),
    document.getElementById('videoRefInput'),
    handleVideoRefUpload
  );

  loadLine();

  // GPU status
  async function updateGPU() {
    const gpu = await checkGPU();
    document.getElementById('gpuStatus').innerHTML = gpuStatusHTML(gpu);
  }
  updateGPU();
  setInterval(updateGPU, 30000);

  // Vast status
  loadVastStatus();
  setInterval(loadVastStatus, 60000);
});

// ==================== Load Line ====================

async function loadLine() {
  try {
    lineData = await API.get(`/api/lines/${lineId}`);
    renderLineHeader();
    renderSteps();
    loadFacePresets();
  } catch (e) {
    showToast('Failed to load line: ' + e.message, 'error');
  }
}

function renderLineHeader() {
  document.getElementById('lineName').textContent = lineData.name;
  const statusEl = document.getElementById('lineStatus');
  statusEl.textContent = lineData.status;
  statusEl.className = `badge badge-${lineData.status}`;
  document.getElementById('lineDate').textContent = formatDate(lineData.created_at);
  document.title = `${lineData.name} — Content Pipeline`;
}

// ==================== Render Steps ====================

function renderSteps() {
  const steps = lineData.steps || {};
  const photos = lineData.photos || [];
  const videos = lineData.videos || [];
  const status = lineData.status;

  // Step states
  const stepOrder = ['draft', 'faceswap', 'clean', 'reference_set', 'photos', 'videos', 'done'];
  const currentIdx = stepOrder.indexOf(status);

  // Faceswap
  const fsStep = steps.faceswap;
  if (fsStep && fsStep.status === 'done' && fsStep.output_path) {
    const fn = fsStep.output_path.split('/').pop();
    document.getElementById('faceswapResult').innerHTML =
      `<img src="/api/files/results/${fn}" class="w-full rounded-lg max-h-80 object-contain cursor-pointer" onclick="window.open(this.src,'_blank')">`;
    document.getElementById('faceswapStatus').textContent = 'Done';
    document.getElementById('step-faceswap').classList.add('done');
  }

  // Clean
  const clStep = steps.clean;
  if (fsStep && fsStep.status === 'done') {
    const fn = fsStep.output_path.split('/').pop();
    document.getElementById('cleanInput').innerHTML =
      `<img src="/api/files/results/${fn}" class="w-full rounded-lg max-h-64 object-contain">`;
    document.getElementById('step-clean').classList.remove('locked');
  } else {
    document.getElementById('step-clean').classList.add('locked');
  }

  if (clStep && clStep.status === 'done' && clStep.output_path) {
    const fn = clStep.output_path.split('/').pop();
    document.getElementById('cleanResult').innerHTML =
      `<img src="/api/files/results/${fn}" class="w-full rounded-lg max-h-80 object-contain cursor-pointer" onclick="window.open(this.src,'_blank')">`;
    document.getElementById('cleanStatus').textContent = 'Done';
    document.getElementById('step-clean').classList.add('done');
  }

  // Reference
  if (lineData.photo_ref_path) {
    const fn = lineData.photo_ref_path.split('/').pop();
    document.getElementById('referencePreview').innerHTML =
      `<img src="/api/files/results/${fn}" class="rounded-lg max-h-64 object-contain">`;
    document.getElementById('referenceStatus').textContent = 'Set';
    document.getElementById('setRefBtn').textContent = 'Reference Set';
    document.getElementById('setRefBtn').disabled = true;
    document.getElementById('step-reference').classList.add('done');
  } else if (clStep && clStep.status === 'done') {
    document.getElementById('step-reference').classList.remove('locked');
    const fn = clStep.output_path.split('/').pop();
    document.getElementById('referencePreview').innerHTML =
      `<img src="/api/files/results/${fn}" class="rounded-lg max-h-64 object-contain">
       <div class="text-xs mt-1" style="color:var(--text-dim)">This clean image will be used as reference for variations</div>`;
  } else {
    document.getElementById('step-reference').classList.add('locked');
  }

  // Photos
  if (lineData.photo_ref_path) {
    document.getElementById('step-photos').classList.remove('locked');
    renderPhotos(photos);
  } else {
    document.getElementById('step-photos').classList.add('locked');
  }

  // Videos
  const acceptedPhotos = photos.filter(p => p.qa_status === 'accepted');
  if (acceptedPhotos.length > 0) {
    document.getElementById('step-videos').classList.remove('locked');
    renderVideos(videos);
    document.getElementById('videoEmpty').style.display = videos.length ? 'none' : 'block';
  } else {
    document.getElementById('step-videos').classList.add('locked');
    document.getElementById('videoEmpty').style.display = 'block';
  }

  // Video ref indicator
  if (lineData.video_ref_path) {
    const vfn = lineData.video_ref_path.split('/').pop();
    document.getElementById('videoRefLabel').textContent = vfn;
  }

  // Drive
  const acceptedVideos = videos.filter(v => v.qa_status === 'accepted');
  if (acceptedPhotos.length > 0 || acceptedVideos.length > 0) {
    document.getElementById('step-drive').classList.remove('locked');
    document.getElementById('driveSummary').textContent =
      `Ready: ${acceptedPhotos.length} photos, ${acceptedVideos.length} videos. Folder: ${lineData.drive_folder_id || 'not set'}`;
    document.getElementById('uploadDriveBtn').disabled = !lineData.drive_folder_id;
  } else {
    document.getElementById('step-drive').classList.add('locked');
  }
}

// ==================== Face Presets ====================

async function loadFacePresets() {
  try {
    const faces = await API.get('/api/face-presets');
    const sel = document.getElementById('faceSelect');
    sel.innerHTML = faces.map(f =>
      `<option value="${f.name}" ${f.name === lineData.face_ref_name ? 'selected' : ''}>${f.name}</option>`
    ).join('');
    if (!faces.length) {
      sel.innerHTML = '<option value="reference_face.jpg">reference_face.jpg</option>';
    }
  } catch {}
}

// ==================== Body Upload ====================

async function handleBodyUpload(file) {
  const reader = new FileReader();
  reader.onload = e => {
    document.getElementById('bodyPreview').src = e.target.result;
    document.getElementById('bodyPreview').classList.remove('hidden');
    document.getElementById('bodyPlaceholder').classList.add('hidden');
  };
  reader.readAsDataURL(file);

  try {
    const result = await API.upload('/api/upload/body', file);
    uploadedBodyFile = result.filename;
    showToast('Body image uploaded');
  } catch (e) {
    showToast('Upload failed: ' + e.message, 'error');
  }
}

// ==================== Video Ref Upload ====================

async function handleVideoRefUpload(file) {
  try {
    const result = await API.upload(`/api/upload/video-ref/${lineId}`, file);
    document.getElementById('videoRefLabel').textContent = result.filename;
    showToast('Video reference uploaded');
    loadLine();
  } catch (e) {
    showToast('Upload failed: ' + e.message, 'error');
  }
}

// ==================== Faceswap ====================

async function runFaceswap() {
  if (!uploadedBodyFile) {
    showToast('Upload a body image first', 'error');
    return;
  }

  const btn = document.getElementById('runFaceswapBtn');
  const progress = document.getElementById('faceswapProgress');
  btn.disabled = true;
  progress.style.display = 'flex';

  const face = document.getElementById('faceSelect').value;

  const { ws, promise } = API.openWS(`/api/lines/${lineId}/faceswap`, {
    body_image: uploadedBodyFile,
    face_ref: face,
  }, {
    status(msg) {
      document.getElementById('faceswapProgressText').textContent = msg.message;
    },
    result(msg) {
      document.getElementById('faceswapResult').innerHTML =
        `<img src="${msg.url}" class="w-full rounded-lg max-h-80 object-contain cursor-pointer" onclick="window.open(this.src,'_blank')">`;
    },
    error(msg) {
      showToast(msg.message, 'error');
    },
    done() {
      btn.disabled = false;
      progress.style.display = 'none';
      loadLine();
    },
  });

  try {
    await promise;
  } catch {}
}

// ==================== Clean ====================

async function runClean() {
  const btn = document.getElementById('runCleanBtn');
  const progress = document.getElementById('cleanProgress');
  btn.disabled = true;
  progress.style.display = 'flex';

  const customPrompt = document.getElementById('cleanPrompt').value.trim();

  const { ws, promise } = API.openWS(`/api/lines/${lineId}/clean`, {
    prompt: customPrompt || undefined,
  }, {
    status(msg) {
      document.getElementById('cleanProgressText').textContent = msg.message;
    },
    result(msg) {
      document.getElementById('cleanResult').innerHTML =
        `<img src="${msg.url}" class="w-full rounded-lg max-h-80 object-contain cursor-pointer" onclick="window.open(this.src,'_blank')">`;
    },
    error(msg) {
      showToast(msg.message, 'error');
    },
    done() {
      btn.disabled = false;
      progress.style.display = 'none';
      loadLine();
    },
  });

  try {
    await promise;
  } catch {}
}

// ==================== Set Reference ====================

async function setReference() {
  try {
    await API.post(`/api/lines/${lineId}/set-reference`);
    showToast('Reference set');
    loadLine();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ==================== Photo Generation ====================

function getToggles() {
  const toggles = {};
  document.querySelectorAll('.toggle-track[data-toggle]').forEach(el => {
    toggles[el.dataset.toggle] = el.classList.contains('active');
  });
  return toggles;
}

async function generatePhotos() {
  const toggles = getToggles();
  const anyToggle = Object.entries(toggles).some(([k, v]) => v && k !== 'verify_detail');
  if (!anyToggle) {
    showToast('Enable at least one toggle', 'error');
    return;
  }

  const count = parseInt(document.getElementById('photoCount').value);
  const btn = document.getElementById('genPhotosBtn');
  const progress = document.getElementById('photoProgress');

  btn.disabled = true;
  progress.style.display = 'block';

  const { ws, promise } = API.openWS(`/api/lines/${lineId}/generate-photos`, {
    count,
    toggles,
    verify_detail: toggles.verify_detail || false,
  }, {
    status(msg) {
      document.getElementById('photoProgressDetail').textContent = msg.message;
    },
    progress(msg) {
      const pct = (msg.current / msg.total * 100).toFixed(0);
      document.getElementById('photoProgressFill').style.width = pct + '%';
      document.getElementById('photoProgressCount').textContent = `${msg.current}/${msg.total}`;
      document.getElementById('photoProgressDetail').textContent = msg.description || '';
    },
    result(msg) {
      appendPhoto(msg);
      const pct = (msg.current / msg.total * 100).toFixed(0);
      document.getElementById('photoProgressFill').style.width = pct + '%';
      document.getElementById('photoProgressCount').textContent = `${msg.current}/${msg.total}`;
    },
    error(msg) {
      if (msg.current) {
        document.getElementById('photoProgressDetail').textContent = msg.message;
      } else {
        showToast(msg.message, 'error');
      }
    },
    done(msg) {
      btn.disabled = false;
      document.getElementById('photoProgressText').textContent = 'Done!';
      document.getElementById('photoProgressFill').style.width = '100%';
      showToast(`Generated ${msg.generated || 0} photos`);
      loadLine();
    },
  });

  try {
    await promise;
  } catch {}
}

// ==================== Photo Rendering ====================

function renderPhotos(photos) {
  const grid = document.getElementById('photoGrid');
  const empty = document.getElementById('photoEmpty');

  if (!photos.length) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  const accepted = photos.filter(p => p.qa_status === 'accepted').length;
  const rejected = photos.filter(p => p.qa_status === 'rejected').length;
  document.getElementById('photoStats').textContent =
    `${accepted} accepted, ${rejected} rejected, ${photos.length} total`;

  grid.innerHTML = photos.map(p => `
    <div class="photo-card ${p.qa_status}" data-photo-id="${p.id}">
      <img src="/api/files/results/${p.filename}" onclick="window.open(this.src,'_blank')"
           loading="lazy">
      <div class="qa-overlay">
        <button class="qa-btn accept" onclick="event.stopPropagation();qaPhoto(${p.id},'accepted')">&#10003;</button>
        <button class="qa-btn reject" onclick="event.stopPropagation();qaPhoto(${p.id},'rejected')">&#10007;</button>
      </div>
      <div class="absolute top-1 right-1 text-xs px-1.5 py-0.5 rounded" style="background:rgba(0,0,0,0.6)">
        ${p.qa_status === 'accepted' ? '<span style="color:var(--green)">&#10003;</span>' :
          p.qa_status === 'rejected' ? '<span style="color:var(--red)">&#10007;</span>' : ''}
      </div>
    </div>
  `).join('');
}

function appendPhoto(msg) {
  const grid = document.getElementById('photoGrid');
  document.getElementById('photoEmpty').style.display = 'none';

  const div = document.createElement('div');
  div.className = 'photo-card fade-in';
  div.dataset.photoId = msg.id;
  div.innerHTML = `
    <img src="${msg.url}" onclick="window.open(this.src,'_blank')" loading="lazy">
    <div class="qa-overlay">
      <button class="qa-btn accept" onclick="event.stopPropagation();qaPhoto(${msg.id},'accepted')">&#10003;</button>
      <button class="qa-btn reject" onclick="event.stopPropagation();qaPhoto(${msg.id},'rejected')">&#10007;</button>
    </div>
  `;
  grid.appendChild(div);
}

async function qaPhoto(photoId, status) {
  try {
    await API.post(`/api/photos/${photoId}/qa`, { status });
    // Update UI immediately
    const card = document.querySelector(`.photo-card[data-photo-id="${photoId}"]`);
    if (card) {
      card.className = `photo-card ${status}`;
    }
    // Refresh step states
    loadLine();
  } catch (e) {
    showToast('QA error: ' + e.message, 'error');
  }
}

async function bulkQA(status) {
  if (!lineData || !lineData.photos.length) return;
  const ids = lineData.photos.filter(p => p.qa_status === 'pending').map(p => p.id);
  if (!ids.length) {
    // If no pending, apply to all
    const allIds = lineData.photos.map(p => p.id);
    if (!allIds.length) return;
    try {
      await API.post(`/api/lines/${lineId}/bulk-qa`, { ids: allIds, status });
      showToast(`${allIds.length} photos ${status}`);
      loadLine();
    } catch (e) {
      showToast('Error: ' + e.message, 'error');
    }
    return;
  }
  try {
    await API.post(`/api/lines/${lineId}/bulk-qa`, { ids, status });
    showToast(`${ids.length} photos ${status}`);
    loadLine();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ==================== Video Generation ====================

async function loadVastStatus() {
  try {
    const s = await API.get('/api/vast/status');
    const dot = document.getElementById('vastDot');
    const text = document.getElementById('vastStatusText');

    if (s.tunnel_ok) {
      dot.className = 'status-dot green';
      let info = 'Tunnel OK';
      if (s.balance !== null) info += ` | Balance: $${s.balance.toFixed(2)}`;
      if (s.instances.length) {
        const inst = s.instances[0];
        info += ` | ${inst.gpu} ${inst.status} ($${inst.cost_per_hr.toFixed(2)}/hr)`;
      }
      text.textContent = info;
    } else {
      dot.className = 'status-dot red';
      let info = 'Tunnel DOWN (localhost:28188)';
      if (s.balance !== null) info += ` | Balance: $${s.balance.toFixed(2)}`;
      text.textContent = info;
    }
  } catch {
    document.getElementById('vastDot').className = 'status-dot red';
    document.getElementById('vastStatusText').textContent = 'Could not check Vast';
  }
}

async function generateVideos() {
  const btn = document.getElementById('genVideosBtn');
  const progress = document.getElementById('videoProgress');
  btn.disabled = true;
  progress.style.display = 'block';

  const { ws, promise } = API.openWS(`/api/lines/${lineId}/generate-videos`, {}, {
    status(msg) {
      document.getElementById('videoProgressText').textContent = msg.message;
    },
    video_progress(msg) {
      const pct = msg.total ? (msg.current / msg.total * 100).toFixed(0) : 0;
      document.getElementById('videoProgressFill').style.width = pct + '%';
      document.getElementById('videoProgressCount').textContent = `${msg.current}/${msg.total}`;
      if (msg.progress_text) {
        document.getElementById('videoProgressText').textContent = msg.progress_text;
      }
    },
    video_result(msg) {
      appendVideo(msg);
    },
    error(msg) {
      if (msg.current) {
        document.getElementById('videoProgressText').textContent = msg.message;
      } else {
        showToast(msg.message, 'error');
      }
    },
    done(msg) {
      btn.disabled = false;
      document.getElementById('videoProgressText').textContent = `Done! ${msg.done || 0}/${msg.total || 0} videos`;
      document.getElementById('videoProgressFill').style.width = '100%';
      loadLine();
    },
  });

  try {
    await promise;
  } catch {}
}

function renderVideos(videos) {
  const grid = document.getElementById('videoGrid');
  if (!videos.length) {
    grid.innerHTML = '';
    return;
  }

  const done = videos.filter(v => v.status === 'done').length;
  document.getElementById('videoStats').textContent =
    `${done} done, ${videos.length} total`;

  grid.innerHTML = videos.map(v => {
    if (v.status === 'done' && v.filename) {
      return `
        <div class="photo-card ${v.qa_status}" data-video-id="${v.id}">
          <video src="/api/files/results/${v.filename}" class="w-full rounded-lg" controls preload="metadata"></video>
          <div class="qa-overlay">
            <button class="qa-btn accept" onclick="event.stopPropagation();qaVideo(${v.id},'accepted')">&#10003;</button>
            <button class="qa-btn reject" onclick="event.stopPropagation();qaVideo(${v.id},'rejected')">&#10007;</button>
          </div>
        </div>`;
    }
    return `
      <div class="card p-3 flex items-center gap-2">
        <div class="spinner"></div>
        <span class="text-sm" style="color:var(--text-dim)">${v.status}: ${v.progress_text || ''}</span>
      </div>`;
  }).join('');
}

function appendVideo(msg) {
  const grid = document.getElementById('videoGrid');
  const div = document.createElement('div');
  div.className = 'photo-card fade-in';
  div.dataset.videoId = msg.video_id;
  div.innerHTML = `
    <video src="${msg.url}" class="w-full rounded-lg" controls preload="metadata"></video>
    <div class="qa-overlay">
      <button class="qa-btn accept" onclick="event.stopPropagation();qaVideo(${msg.video_id},'accepted')">&#10003;</button>
      <button class="qa-btn reject" onclick="event.stopPropagation();qaVideo(${msg.video_id},'rejected')">&#10007;</button>
    </div>
  `;
  grid.appendChild(div);
}

async function qaVideo(videoId, status) {
  try {
    await API.post(`/api/videos/${videoId}/qa`, { status });
    const card = document.querySelector(`.photo-card[data-video-id="${videoId}"]`);
    if (card) card.className = `photo-card ${status}`;
    loadLine();
  } catch (e) {
    showToast('QA error: ' + e.message, 'error');
  }
}

// ==================== Drive Upload ====================

async function uploadToDrive() {
  const btn = document.getElementById('uploadDriveBtn');
  const progress = document.getElementById('driveProgress');
  btn.disabled = true;
  progress.style.display = 'block';

  const { ws, promise } = API.openWS(`/api/lines/${lineId}/upload-drive`, {}, {
    status(msg) {
      document.getElementById('driveProgressText').textContent = msg.message;
    },
    upload_progress(msg) {
      const pct = (msg.current / msg.total * 100).toFixed(0);
      document.getElementById('driveProgressFill').style.width = pct + '%';
      document.getElementById('driveProgressText').textContent = `${msg.current}/${msg.total}: ${msg.filename}`;
    },
    upload_done(msg) {
      document.getElementById('driveProgressFill').style.width = '100%';
      document.getElementById('driveProgressText').textContent = `Done! ${msg.uploaded}/${msg.total} uploaded`;
      if (msg.folder_link) {
        const link = document.getElementById('driveFolderLink');
        link.href = msg.folder_link;
        link.style.display = 'inline-flex';
      }
      showToast(`${msg.uploaded} files uploaded to Drive`);
    },
    error(msg) {
      showToast(msg.message, 'error');
    },
    done() {
      btn.disabled = false;
      loadLine();
    },
  });

  try {
    await promise;
  } catch {}
}

// ==================== Edit Line ====================

function editLineInfo() {
  const modal = document.getElementById('editModal');
  modal.style.display = modal.style.display === 'none' ? 'block' : 'none';
  if (lineData) {
    document.getElementById('editName').value = lineData.name || '';
    document.getElementById('editDriveFolder').value = lineData.drive_folder_id || '';
    document.getElementById('editPositive').value = lineData.positive_prompt || '';
  }
}

async function saveLineInfo() {
  try {
    await API.put(`/api/lines/${lineId}`, {
      name: document.getElementById('editName').value.trim(),
      drive_folder_id: document.getElementById('editDriveFolder').value.trim(),
      positive_prompt: document.getElementById('editPositive').value,
    });
    document.getElementById('editModal').style.display = 'none';
    showToast('Line updated');
    loadLine();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function autoPrompt() {
  const btn = document.getElementById('autoPromptBtn');
  const ta = document.getElementById('editPositive');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '✨ Analyzing photo…'; }
  try {
    const res = await API.post(`/api/lines/${lineId}/auto-prompt`, {});
    ta.value = res.positive || '';
    showToast(res.fallback ? 'Vision returned nothing — inserted a default, editable'
                           : `Prompt from photo (${res.source})`,
              res.fallback ? 'error' : 'success');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}
