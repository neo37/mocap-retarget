/** Интерфейс: загрузка модели и видео, сопоставление костей, просмотр результата. */
import * as THREE from './vendor/three.module.js';
import { GLTFLoader } from './vendor/GLTFLoader.js';

const $ = (id) => document.getElementById(id);
/** Базовый путь: скрипт лежит рядом с index.html, поэтому сервис работает и в
    подкаталоге (например https://videos.ai3d.art/nella/), и в корне. */
const BASE = new URL('.', import.meta.url).pathname.replace(/\/$/, '');
const u = (path) => (path && path.startsWith('/') ? BASE + path : path);
const api = async (url, opts) => {
  const r = await fetch(u(url), opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${url} → ${r.status}`);
  return r.json();
};

let state = { models: [], videos: [], model: null };

/* ---------- вкладки ---------- */
document.querySelectorAll('nav button').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach((x) => x.classList.toggle('active', x === b));
    $('tab-capture').classList.toggle('hidden', b.dataset.tab !== 'capture');
    $('tab-skeleton').classList.toggle('hidden', b.dataset.tab !== 'skeleton');
    if (b.dataset.tab === 'skeleton') renderSkeletonTab();
  };
});

/* ---------- загрузка файлов ---------- */
function bindDrop(dropId, inputId, onFile) {
  const drop = $(dropId);
  const input = $(inputId);
  drop.onclick = () => input.click();
  input.onchange = () => input.files[0] && onFile(input.files[0]);
  ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', (ev) => ev.dataTransfer.files[0] && onFile(ev.dataTransfer.files[0]));
}

async function upload(url, file, dropId, label) {
  const drop = $(dropId);
  const before = drop.textContent;
  drop.textContent = `${label}: ${file.name} …`;
  try {
    const fd = new FormData();
    fd.append('file', file);
    const res = await api(url, { method: 'POST', body: fd });
    drop.textContent = `готово: ${file.name}`;
    return res;
  } catch (e) {
    drop.textContent = 'ошибка: ' + e.message;
    setTimeout(() => { drop.textContent = before; }, 4000);
    throw e;
  }
}

bindDrop('model-drop', 'model-input', async (f) => {
  const m = await upload('/api/models', f, 'model-drop', 'загружаю модель');
  await refreshModels(m.id);
});
bindDrop('video-drop', 'video-input', async (f) => {
  const v = await upload('/api/videos', f, 'video-drop', 'загружаю видео');
  await refreshVideos(v.id);
});

/* ---------- списки ---------- */
async function refreshModels(select) {
  state.models = await api('/api/models');
  const options = state.models.map((m) => {
    const warn = m.missing.length ? ` — не хватает костей: ${m.missing.length}` : '';
    return `<option value="${m.id}">${m.name} (${m.bones} костей)${warn}</option>`;
  });
  $('model-select').innerHTML = options.join('') || '<option value="">пусто</option>';
  $('sk-model').innerHTML = $('model-select').innerHTML;
  if (select) { $('model-select').value = select; $('sk-model').value = select; }
  showModelInfo();
}

async function refreshVideos(select) {
  state.videos = await api('/api/videos');
  $('video-select').innerHTML = state.videos
    .map((v) => `<option value="${v.id}">${v.name} (${(v.size / 1048576).toFixed(1)} МБ)</option>`)
    .join('') || '<option value="">пусто</option>';
  if (select) $('video-select').value = select;
}

async function showModelInfo() {
  const id = $('model-select').value;
  if (!id) return;
  const m = await api('/api/models/' + id);
  state.model = m;
  const missing = m.missing.length
    ? `<span class="pill err">не сопоставлено обязательных: ${m.missing.length}</span> — заполните на вкладке «Скелет»`
    : '<span class="pill ok">скелет сопоставлен полностью</span>';
  $('model-info').innerHTML = `${missing}<br>клипы в файле: ${m.clips.length ? m.clips.join(', ') : 'нет'}`;
}
$('model-select').onchange = showModelInfo;

/* ---------- вкладка «Скелет» ---------- */
async function renderSkeletonTab() {
  const id = $('sk-model').value;
  if (!id) { $('sk-rows').innerHTML = '<tr><td colspan="2" class="muted">Сначала загрузите модель</td></tr>'; return; }
  const m = await api('/api/models/' + id);
  const rows = Object.entries(m.slots).map(([slot, [title, required]]) => {
    const chosen = m.mapping[slot] || '';
    const opts = ['<option value="">— не задано —</option>']
      .concat(m.bones.map((b) => `<option value="${b}"${b === chosen ? ' selected' : ''}>${b}</option>`));
    const cls = [required ? 'required' : '', required && !chosen ? 'unmapped' : ''].join(' ').trim();
    return `<tr class="${cls}"><td>${title}<div class="muted">${slot}</div></td>
            <td><select data-slot="${slot}">${opts.join('')}</select></td></tr>`;
  });
  $('sk-rows').innerHTML = rows.join('');
  $('sk-status').textContent = m.missing.length
    ? `не хватает: ${m.missing.join(', ')}`
    : 'всё обязательное сопоставлено';
}
$('sk-model').onchange = renderSkeletonTab;

$('sk-save').onclick = async () => {
  const id = $('sk-model').value;
  const mapping = {};
  document.querySelectorAll('#sk-rows select').forEach((s) => { mapping[s.dataset.slot] = s.value || null; });
  const res = await api(`/api/models/${id}/mapping`, {
    method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(mapping),
  });
  $('sk-status').textContent = res.missing.length
    ? `сохранено; не хватает: ${res.missing.join(', ')}`
    : 'сохранено, скелет готов к съёмке';
  await refreshModels(id);
  renderSkeletonTab();
};

/* ---------- запуск задачи ---------- */
$('run').onclick = async () => {
  const model_id = $('model-select').value;
  const video_id = $('video-select').value;
  if (!model_id || !video_id) { $('job-stage').textContent = 'нужны и модель, и видео'; return; }
  $('run').disabled = true;
  $('job-result').classList.add('hidden');
  try {
    const job = await api('/api/jobs', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        model_id, video_id,
        clip_name: $('clip-name').value,
        max_seconds: Number($('seconds').value),
        smooth: Number($('smooth').value),
        root_motion: $('root-motion').checked,
      }),
    });
    await poll(job.id);
  } catch (e) {
    $('job-stage').textContent = 'ошибка: ' + e.message;
  } finally {
    $('run').disabled = false;
  }
};

async function poll(jobId) {
  for (;;) {
    const j = await api('/api/jobs/' + jobId);
    $('job-stage').textContent = j.stage + (j.frames ? ` · кадров ${j.frames}, распознано ${j.detected}` : '');
    $('job-bar').style.width = Math.round((j.progress || 0) * 100) + '%';
    if (j.status === 'error') { $('job-stage').textContent = 'ошибка: ' + j.error; return; }
    if (j.status === 'ok') {
      $('dl-glb').href = u(j.result);
      $('dl-anim').href = u(j.animation);
      $('job-stats').textContent = `клип «${j.clip}», ${j.frames} кадров, ${j.fps} к/с`;
      $('job-result').classList.remove('hidden');
      preview(j.result);
      return;
    }
    await new Promise((r) => setTimeout(r, 700));
  }
}

/* ---------- просмотр результата ---------- */
let viewer = null;
function preview(url) {
  const host = $('viewer');
  if (!viewer) {
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    host.innerHTML = '';
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8d99b5, 2.2));
    const dir = new THREE.DirectionalLight(0xffffff, 1.6);
    dir.position.set(2, 4, 3);
    scene.add(dir);
    viewer = { renderer, scene, camera, mixer: null, model: null, yaw: 0 };

    let drag = false, lastX = 0;
    renderer.domElement.style.cursor = 'grab';
    renderer.domElement.addEventListener('pointerdown', (e) => { drag = true; lastX = e.clientX; });
    addEventListener('pointerup', () => { drag = false; });
    addEventListener('pointermove', (e) => { if (drag) { viewer.yaw += (e.clientX - lastX) * 0.01; lastX = e.clientX; } });

    const clock = new THREE.Clock();
    (function loop() {
      requestAnimationFrame(loop);
      const w = host.clientWidth, h = host.clientHeight;
      if (renderer.domElement.width !== w || renderer.domElement.height !== h) {
        renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
      }
      if (viewer.mixer) viewer.mixer.update(clock.getDelta());
      if (viewer.model) viewer.model.rotation.y = viewer.yaw;
      renderer.render(scene, camera);
    })();
  }
  new GLTFLoader().load(u(url), (gltf) => {
    if (viewer.model) viewer.scene.remove(viewer.model);
    const root = gltf.scene;
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const scale = 1.7 / (size.y || 1);
    root.scale.setScalar(scale);
    root.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);
    viewer.scene.add(root);
    viewer.model = root;
    viewer.camera.position.set(0, 1.0, 4.2);
    viewer.camera.lookAt(0, 0.9, 0);
    viewer.mixer = new THREE.AnimationMixer(root);
    const clip = gltf.animations[gltf.animations.length - 1];
    if (clip) viewer.mixer.clipAction(clip).play();
  });
}

/* ---------- старт ---------- */
(async () => {
  try {
    const h = await api('/api/health');
    $('health').textContent = `моделей: ${h.models}`;
  } catch (e) { $('health').textContent = 'сервис не отвечает'; }
  await refreshModels();
  await refreshVideos();
})();
