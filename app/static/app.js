/** Экраны сервиса: проекты, работа с проектом (кадры → позы и клипы),
 *  скелет в 3D, перенос мимики и замена лица.
 *  Пути к API считаются от места, где лежит скрипт: сервис одинаково работает
 *  и в корне, и в подкаталоге (videos.ai3d.art/nella/). */
import { createViewer } from './viewer.js';

const BASE = new URL('.', import.meta.url).pathname.replace(/\/$/, '');
const u = (p) => (p && p.startsWith('/') ? BASE + p : p);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, { method = 'GET', body = null } = {}) {
  const init = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== null) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(u(path), init);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${path} → ${r.status}`);
  return r.json();
}
const upload = (path, file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api(path, { method: 'POST', body: fd });
};

/* ---------- мелкий DOM ---------- */
function h(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node[k.toLowerCase()] = v;
    else if (k in node && k !== 'list') node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
const app = document.getElementById('app');
const toastBox = document.getElementById('toast');

function toast(text, isError = false) {
  const node = h('div', { class: isError ? 'err' : '' }, text);
  toastBox.append(node);
  setTimeout(() => node.remove(), isError ? 9000 : 4500);
}
const fail = (e) => toast(e.message || String(e), true);

function crumbs(items) {
  const box = document.getElementById('crumbs');
  box.textContent = '';
  items.forEach(([label, href], i) => {
    if (i) box.append(h('span', {}, '/'));
    box.append(href ? h('a', { href }, label) : h('span', {}, label));
  });
}

function panel(title, body, ...actions) {
  return h('section', { class: 'panel' },
    h('header', {}, h('h2', {}, title), h('div', { class: 'row' }, ...actions)),
    h('div', { class: 'body col' }, body));
}

function dropZone(text, accept, onFile) {
  const input = h('input', { type: 'file', accept, hidden: true,
    onchange: () => input.files[0] && onFile(input.files[0]) });
  const zone = h('div', { class: 'drop', onclick: () => input.click() }, text);
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('over')));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]);
  });
  return h('div', {}, zone, input);
}

/** Следить за задачей до конца, показывая прогресс. */
async function watchJob(job, onTick) {
  let state = job;
  while (state.status === 'running') {
    await sleep(1500);
    state = await api(`/api/jobs/${job.id}`);
    onTick && onTick(state);
  }
  if (state.status === 'error') throw new Error(state.error || 'задача сломалась');
  return state;
}

function progressBox() {
  const bar = h('span');
  const text = h('div', { class: 'muted mono' }, 'ждём…');
  const box = h('div', { class: 'col', hidden: true }, text, h('div', { class: 'bar' }, bar));
  return {
    node: box,
    show(on = true) { box.hidden = !on; },
    tick(job) {
      bar.style.width = `${Math.round((job.progress || 0) * 100)}%`;
      text.textContent = `${job.stage || ''} ${Math.round((job.progress || 0) * 100)}%`;
    },
  };
}

const fmtSize = (n) => `${(n / 1024 / 1024).toFixed(1)} МБ`;
const fmtDate = (t) => new Date(t * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });

/* ---------- экран: проекты ---------- */
async function screenProjects() {
  crumbs([['Проекты', null]]);
  const list = await api('/api/projects');
  const name = h('input', { placeholder: 'Название проекта' });

  const create = async () => {
    try {
      const p = await api('/api/projects', { method: 'POST', body: { name: name.value } });
      location.hash = `#/p/${p.id}`;
    } catch (e) { fail(e); }
  };

  const cards = list.map((p) => h('div', { class: 'card' },
    h('div', { class: 'name' }, p.name),
    h('div', { class: 'muted mono' },
      `${p.model ? p.model.name : 'без модели'} · источников ${p.sources} · результатов ${p.results}`),
    h('div', { class: 'muted mono' }, fmtDate(p.updated || p.created)),
    h('div', { class: 'row' },
      h('button', { class: 'primary', onclick: () => { location.hash = `#/p/${p.id}`; } }, 'Открыть'),
      h('button', { class: 'ghost', onclick: async (e) => {
        e.stopPropagation();
        if (!confirm(`Удалить проект «${p.name}» со всеми результатами?`)) return;
        await api(`/api/projects/${p.id}`, { method: 'DELETE' });
        render();
      } }, '✕'))));

  app.textContent = '';
  app.append(h('div', { class: 'col' },
    panel('Новый проект',
      h('div', { class: 'row' }, h('div', { class: 'grow' }, name),
        h('button', { class: 'primary', onclick: create }, 'Создать'))),
    list.length ? h('div', { class: 'cards' }, ...cards)
                : h('p', { class: 'muted' }, 'Проектов пока нет — создайте первый.')));
}

/* ---------- экран: проект ---------- */
async function screenProject(pid) {
  const project = await api(`/api/projects/${pid}`);
  crumbs([['Проекты', '#/'], [project.name, null]]);

  const state = { source: (project.sources || [])[0] || null, frames: null, picked: new Set(),
                  result: null };

  const stage = h('div', { id: 'stage' });
  const viewer = createViewer(stage);
  currentViewer = viewer;

  const left = h('div', { class: 'col' });
  const right = h('div', { class: 'col' });
  app.textContent = '';
  app.append(h('div', { class: 'col' },
    h('div', { class: 'row' },
      h('input', { value: project.name, style: 'max-width:340px',
        onchange: async (e) => {
          await api(`/api/projects/${pid}`, { method: 'PATCH', body: { name: e.target.value } });
          toast('название сохранено');
        } }),
      h('button', { onclick: () => { location.hash = `#/p/${pid}/skeleton`; } }, '🦴 Скелет в 3D'),
      h('button', { onclick: () => { location.hash = `#/p/${pid}/face`; } }, '😐 Мимика'),
      h('button', { onclick: () => { location.hash = `#/p/${pid}/swap`; } }, '🎭 Замена лица'),
      h('div', { class: 'grow' }),
      h('a', { class: 'muted mono', href: '#/' }, '← все проекты')),
    h('div', { class: 'split' }, left, right)));

  /* --- модель --- */
  function renderModel() {
    const m = project.model;
    const body = m
      ? h('div', { class: 'col' },
          h('div', { class: 'row' }, h('span', { class: 'tag acid' }, 'модель'),
            h('b', {}, m.name)),
          h('div', { class: 'muted mono' }, `костей ${m.bones}`),
          m.missing.length
            ? h('div', { class: 'row' }, h('span', { class: 'tag hot' }, 'нет костей'),
                h('span', { class: 'mono' }, m.missing.join(', ')),
                h('button', { class: 'ghost', onclick: () => { location.hash = `#/p/${pid}/skeleton`; } }, 'указать в 3D'))
            : h('span', { class: 'tag sky' }, 'скелет разобран'),
          dropZone('заменить модель (.glb)', '.glb,.gltf', async (f) => {
            try {
              const meta = await upload(`/api/projects/${pid}/model`, f);
              project.model = { id: meta.id, name: meta.name, bones: meta.bones.length,
                                missing: meta.missing, clips: meta.clips };
              project.model_id = meta.id;
              renderModel();
              viewer.load(u(`/api/models/${meta.id}/file`)).catch(() => {});
              toast('модель загружена');
            } catch (e) { fail(e); }
          }))
      : dropZone('перетащите модель .glb со скелетом', '.glb,.gltf', async (f) => {
          try {
            const meta = await upload(`/api/projects/${pid}/model`, f);
            project.model = { id: meta.id, name: meta.name, bones: meta.bones.length,
                              missing: meta.missing, clips: meta.clips };
            project.model_id = meta.id;
            renderModel();
            viewer.load(u(`/api/models/${meta.id}/file`)).catch(() => {});
            toast('модель загружена');
          } catch (e) { fail(e); }
        });
    modelPanel.querySelector('.body').replaceChildren(body);
  }
  const modelPanel = panel('Модель', h('div'));

  /* --- источники --- */
  const sourcesBody = h('div', { class: 'col' });
  const sourcesPanel = panel('Видео и фото', sourcesBody);

  function renderSources() {
    const items = (project.sources || []).map((s) => h('div', {
      class: `item${state.source && state.source.id === s.id ? ' on' : ''}`,
      onclick: () => selectSource(s),
    },
      h('span', { class: `tag ${s.kind === 'photo' ? 'violet' : 'sky'}` }, s.kind === 'photo' ? 'фото' : 'видео'),
      h('div', { class: 'who' }, h('div', {}, s.name), h('div', { class: 'muted mono' }, fmtSize(s.size))),
      h('button', { class: 'ghost', onclick: async (e) => {
        e.stopPropagation();
        await api(`/api/projects/${pid}/sources/${s.id}`, { method: 'DELETE' });
        project.sources = project.sources.filter((x) => x.id !== s.id);
        if (state.source && state.source.id === s.id) { state.source = null; state.frames = null; }
        renderSources(); renderPicker();
      } }, '✕')));

    sourcesBody.replaceChildren(
      dropZone('перетащите видео или фото', 'video/*,image/*', async (f) => {
        try {
          const s = await upload(`/api/projects/${pid}/sources`, f);
          project.sources = [s, ...(project.sources || []).filter((x) => x.id !== s.id)];
          renderSources();
          selectSource(s);
          toast(`${s.kind === 'photo' ? 'фото' : 'видео'} загружено`);
        } catch (e) { fail(e); }
      }),
      items.length ? h('div', { class: 'list' }, ...items) : h('p', { class: 'muted' }, 'пока пусто'));
  }

  async function selectSource(s) {
    state.source = s;
    state.frames = null;
    state.picked.clear();
    renderSources();
    renderPicker();
    try {
      state.frames = await api(`/api/sources/${s.id}/frames`);
    } catch (e) { fail(e); }
    renderPicker();
  }

  /* --- кадры и запуск --- */
  const pickerBody = h('div', { class: 'col' });
  const pickerPanel = panel('Кадры и съёмка', pickerBody);
  const poseProgress = progressBox();
  const clipProgress = progressBox();

  function renderPicker() {
    if (!state.source) {
      pickerBody.replaceChildren(h('p', { class: 'muted' }, 'выберите видео или фото слева'));
      return;
    }
    if (state.source.kind === 'photo') {
      pickerBody.replaceChildren(
        h('img', { src: u(`/api/sources/${state.source.id}/file`), style: 'width:100%;border:3px solid var(--edge)' }),
        h('button', { class: 'primary', onclick: () => runPoses([]) }, 'Снять позу с фото'),
        poseProgress.node);
      return;
    }
    if (!state.frames) {
      pickerBody.replaceChildren(h('p', { class: 'muted mono' }, 'нарезаю кадры…'));
      return;
    }
    const strip = h('div', { class: 'frames' },
      ...state.frames.frames.map((f) => {
        const cell = h('div', { class: `frame${state.picked.has(f.time) ? ' on' : ''}`,
          onclick: () => {
            state.picked.has(f.time) ? state.picked.delete(f.time) : state.picked.add(f.time);
            cell.classList.toggle('on');
            count.textContent = `выбрано кадров: ${state.picked.size}`;
          } },
          h('img', { src: u(f.url), loading: 'lazy' }),
          h('span', { class: 't' }, `${f.time.toFixed(2)} с`));
        return cell;
      }));
    const count = h('span', { class: 'muted mono' }, `выбрано кадров: ${state.picked.size}`);
    const seconds = h('input', { type: 'number', value: 15, min: 1, max: 120 });
    const smooth = h('input', { type: 'number', value: 5, min: 0, max: 21 });
    const root = h('input', { type: 'checkbox' });

    pickerBody.replaceChildren(
      h('div', { class: 'muted mono' },
        `${state.frames.duration.toFixed(1)} с · ${state.frames.fps.toFixed(1)} кадр/с`),
      strip,
      h('div', { class: 'row' }, count,
        h('button', { class: 'ghost', onclick: () => { state.picked.clear(); renderPicker(); } }, 'сбросить'),
        h('button', { class: 'primary', onclick: () => runPoses([...state.picked]) }, 'Позы из кадров')),
      poseProgress.node,
      h('hr', { style: 'border:0;border-top:3px solid var(--edge);width:100%' }),
      h('div', { class: 'row' },
        h('div', { class: 'field' }, h('label', {}, 'секунд'), seconds),
        h('div', { class: 'field' }, h('label', {}, 'сглаживание'), smooth),
        h('label', { class: 'row', style: 'margin:0' }, root, ' таз'),
        h('button', { class: 'primary', onclick: () => runClip(+seconds.value, +smooth.value, root.checked) },
          'Снять клип целиком')),
      clipProgress.node);
  }

  async function runPoses(times) {
    if (!project.model_id) return toast('сначала загрузите модель', true);
    poseProgress.show(); poseProgress.tick({ progress: 0, stage: 'ставлю в очередь' });
    try {
      const job = await api(`/api/projects/${pid}/poses`, { method: 'POST',
        body: { source_id: state.source.id, times } });
      const done = await watchJob(job, poseProgress.tick);
      project.results = [...(done.results || []), ...project.results];
      renderResults();
      showResult(done.results[0]);
      toast(`готово: поз ${done.results.length}`);
    } catch (e) { fail(e); } finally { poseProgress.show(false); }
  }

  async function runClip(maxSeconds, smooth, rootMotion) {
    if (!project.model_id) return toast('сначала загрузите модель', true);
    clipProgress.show(); clipProgress.tick({ progress: 0, stage: 'ставлю в очередь' });
    try {
      const job = await api(`/api/projects/${pid}/clips`, { method: 'POST',
        body: { source_id: state.source.id, max_seconds: maxSeconds, smooth, root_motion: rootMotion } });
      const done = await watchJob(job, clipProgress.tick);
      project.results = [...(done.results || []), ...project.results];
      renderResults();
      showResult(done.results[0]);
      toast('клип снят');
    } catch (e) { fail(e); } finally { clipProgress.show(false); }
  }

  /* --- результаты и режим редактирования --- */
  const resultsBody = h('div', { class: 'col' });
  const editorBody = h('div', { class: 'col' });

  function renderResults() {
    const rows = (project.results || []).map((r) => h('div', {
      class: `item${state.result && state.result.id === r.id ? ' on' : ''}`,
      onclick: () => showResult(r),
    },
      h('span', { class: `tag ${r.kind === 'pose' ? 'acid' : r.kind === 'clip' ? 'sky' : r.kind === 'face' ? 'violet' : 'hot'}` }, r.kind),
      h('div', { class: 'who' }, h('div', {}, r.name),
        h('div', { class: 'muted mono' }, fmtDate(r.created))),
      r.file ? h('a', { class: 'btn', href: u(`/api/projects/${pid}/results/${r.id}/file`),
                        onclick: (e) => e.stopPropagation() }, r.kind === 'video' ? 'MP4' : 'GLB') : null,
      r.animation ? h('a', { class: 'btn', href: u(`/api/projects/${pid}/results/${r.id}/animation.json`),
                             onclick: (e) => e.stopPropagation() }, 'JSON') : null,
      h('button', { class: 'ghost', onclick: async (e) => {
        e.stopPropagation();
        await api(`/api/projects/${pid}/results/${r.id}`, { method: 'DELETE' });
        project.results = project.results.filter((x) => x.id !== r.id);
        if (state.result && state.result.id === r.id) { state.result = null; editorBody.replaceChildren(); }
        renderResults();
      } }, '✕')));
    resultsBody.replaceChildren(rows.length ? h('div', { class: 'list' }, ...rows)
      : h('p', { class: 'muted' }, 'результатов пока нет'));
  }

  async function showResult(r) {
    if (!r) return;
    state.result = r;
    renderResults();
    if (r.kind === 'video') {
      editorBody.replaceChildren(h('video', { src: u(`/api/projects/${pid}/results/${r.id}/file`),
        controls: true, style: 'width:100%;border:3px solid var(--line)' }));
      return;
    }
    if (!r.file) {
      editorBody.replaceChildren(h('p', { class: 'muted' },
        'в модели нет морф-таргетов — сохранились только коэффициенты (JSON)'));
      return;
    }
    try {
      const info = await viewer.load(u(`/api/projects/${pid}/results/${r.id}/file`));
      editorBody.replaceChildren(r.kind === 'clip' && info.duration
        ? trimEditor(r, info.duration)
        : h('div', { class: 'muted mono' }, r.kind === 'pose' ? 'статичная поза' : 'клип без анимации'));
    } catch (e) { fail(e); }
  }

  /** Режим редактирования: играем и вырезаем кусок клипа. */
  function trimEditor(r, duration) {
    const from = h('input', { type: 'range', min: 0, max: duration.toFixed(2), step: 0.01, value: 0 });
    const to = h('input', { type: 'range', min: 0, max: duration.toFixed(2), step: 0.01, value: duration });
    const label = h('div', { class: 'muted mono' });
    const sync = () => {
      if (+from.value > +to.value - 0.05) from.value = Math.max(0, +to.value - 0.05);
      label.textContent = `фрагмент ${(+from.value).toFixed(2)} — ${(+to.value).toFixed(2)} с из ${duration.toFixed(2)} с`;
      viewer.playRange(+from.value, +to.value);
    };
    from.oninput = sync; to.oninput = sync;
    sync();
    return h('div', { class: 'col' },
      label,
      h('div', {}, h('label', {}, 'начало'), from),
      h('div', {}, h('label', {}, 'конец'), to),
      h('div', { class: 'row' },
        h('button', { onclick: () => viewer.playRange(+from.value, +to.value) }, '▶ отрезок'),
        h('button', { onclick: () => viewer.pause(true) }, '⏸'),
        h('button', { class: 'primary', onclick: async () => {
          try {
            const cut = await api(`/api/projects/${pid}/results/${r.id}/trim`, { method: 'POST',
              body: { start: +from.value, end: +to.value } });
            project.results = [cut, ...project.results];
            renderResults();
            showResult(cut);
            toast('фрагмент сохранён отдельным результатом');
          } catch (e) { fail(e); }
        } }, 'Вырезать фрагмент')));
  }

  left.append(modelPanel, sourcesPanel, pickerPanel);
  right.append(h('section', { class: 'panel' }, stage, h('div', { class: 'body col' }, editorBody)),
               panel('Результаты', resultsBody));
  renderModel();
  renderSources();
  renderPicker();
  renderResults();
  if (project.model_id) viewer.load(u(`/api/models/${project.model_id}/file`)).catch(() => {});
}

/* ---------- экран: скелет в 3D ---------- */
async function screenSkeleton(pid) {
  const project = await api(`/api/projects/${pid}`);
  crumbs([['Проекты', '#/'], [project.name, `#/p/${pid}`], ['Скелет', null]]);
  if (!project.model_id) {
    app.replaceChildren(h('p', { class: 'muted' }, 'в проекте ещё нет модели'));
    return;
  }
  const model = await api(`/api/models/${project.model_id}`);
  const mapping = { ...model.mapping };
  let active = null;

  const stage = h('div', { id: 'stage' });
  const viewer = createViewer(stage);
  currentViewer = viewer;
  const slotsBody = h('div', { class: 'col' });

  const redrawBones = () => viewer.setSkeleton(true, {
    mapped: Object.fromEntries(Object.values(mapping).filter(Boolean).map((n) => [n, true])),
    active: active ? mapping[active] : null,
    onPick: (bone) => {
      if (!active) return toast('сначала выберите слот слева', true);
      const slot = active;
      mapping[slot] = bone;
      const order = Object.keys(model.slots);
      active = order.slice(order.indexOf(slot) + 1)
        .find((s) => model.slots[s][1] && !mapping[s]) || null;
      renderSlots();
      redrawBones();
      toast(`${model.slots[slot][0]} → ${bone}`);
    },
  });

  function renderSlots() {
    const rows = Object.entries(model.slots).map(([slot, [label, required]]) => h('div', {
      class: `item${active === slot ? ' on' : ''}`,
      onclick: () => { active = slot; renderSlots(); redrawBones(); },
    },
      h('span', { class: `tag ${mapping[slot] ? 'acid' : required ? 'hot' : ''}` }, required ? 'нужна' : 'можно'),
      h('div', { class: 'who' }, h('div', {}, label),
        h('div', { class: 'muted mono' }, mapping[slot] || 'кость не выбрана')),
      mapping[slot] ? h('button', { class: 'ghost', onclick: (e) => {
        e.stopPropagation(); mapping[slot] = null; renderSlots(); redrawBones();
      } }, '✕') : null));
    slotsBody.replaceChildren(
      h('p', { class: 'muted' }, active
        ? `кликните кость в 3D — она станет слотом «${model.slots[active][0]}»`
        : 'выберите слот, затем кликните кость на модели'),
      h('div', { class: 'list' }, ...rows));
  }

  app.textContent = '';
  app.append(h('div', { class: 'split' },
    panel('Слоты гуманоида', slotsBody,
      h('button', { class: 'primary', onclick: async () => {
        try {
          const res = await api(`/api/models/${model.id}/mapping`, { method: 'PUT', body: mapping });
          toast(res.missing.length ? `сохранено, не хватает: ${res.missing.join(', ')}` : 'сопоставление сохранено');
        } catch (e) { fail(e); }
      } }, 'Сохранить'),
      h('button', { onclick: () => { location.hash = `#/p/${pid}`; } }, '← в проект')),
    h('section', { class: 'panel' }, stage,
      h('div', { class: 'body muted mono' },
        'колесо — приблизить, перетаскивание — повернуть, клик по кости — назначить'))));

  renderSlots();
  await viewer.load(u(`/api/models/${model.id}/file`), { autoplay: false });
  redrawBones();
}

/* ---------- экран: мимика ---------- */
async function screenFace(pid) {
  const project = await api(`/api/projects/${pid}`);
  crumbs([['Проекты', '#/'], [project.name, `#/p/${pid}`], ['Мимика', null]]);
  if (!project.model_id) {
    app.replaceChildren(h('p', { class: 'muted' }, 'в проекте ещё нет модели'));
    return;
  }
  const face = await api(`/api/models/${project.model_id}/face`);
  const mapping = { ...face.mapping };
  const stage = h('div', { id: 'stage' });
  const viewer = createViewer(stage);
  currentViewer = viewer;

  const sourceSelect = h('select', {}, ...(project.sources || []).map((s) =>
    h('option', { value: s.id }, `${s.kind === 'photo' ? 'фото' : 'видео'} · ${s.name}`)));
  const mode = h('select', {}, h('option', { value: 'pose' }, 'одна гримаса (кадр)'),
                                h('option', { value: 'clip' }, 'дорожка мимики с видео'));
  const at = h('input', { type: 'number', value: 0, min: 0, step: 0.1 });
  const seconds = h('input', { type: 'number', value: 15, min: 1, max: 120 });
  const progress = progressBox();

  const targets = face.targets || [];
  const table = h('div', { style: 'max-height:320px;overflow:auto;border:3px solid var(--edge)' },
    h('table', {}, h('tbody', {}, ...face.shapes.map((shape) => h('tr', {},
      h('td', { class: 'mono' }, shape),
      h('td', {}, h('select', { onchange: (e) => { mapping[shape] = e.target.value || null; } },
        h('option', { value: '' }, '—'),
        ...targets.map((t) => h('option', { value: t, selected: mapping[shape] === t }, t)))))))));

  const run = async () => {
    progress.show(); progress.tick({ progress: 0, stage: 'ставлю в очередь' });
    try {
      const job = await api(`/api/projects/${pid}/face`, { method: 'POST', body: {
        source_id: sourceSelect.value, mode: mode.value,
        times: [+at.value], max_seconds: +seconds.value } });
      const done = await watchJob(job, progress.tick);
      const r = (done.results || [])[0];
      if (done.note) toast(done.note, true);
      if (r && r.file) await viewer.load(u(`/api/projects/${pid}/results/${r.id}/file`));
      toast('мимика перенесена');
    } catch (e) { fail(e); } finally { progress.show(false); }
  };

  app.textContent = '';
  app.append(h('div', { class: 'split' },
    h('div', { class: 'col' },
      panel('Откуда брать выражение',
        h('div', { class: 'col' },
          h('div', {}, h('label', {}, 'источник'), sourceSelect),
          h('div', {}, h('label', {}, 'что снимаем'), mode),
          h('div', { class: 'row' },
            h('div', { class: 'field grow' }, h('label', {}, 'секунда кадра'), at),
            h('div', { class: 'field grow' }, h('label', {}, 'секунд с видео'), seconds)),
          h('button', { class: 'primary', onclick: run }, 'Перенести мимику'),
          progress.node)),
      panel(targets.length ? `Морф-таргеты модели (${targets.length})` : 'Морф-таргетов нет',
        targets.length ? table
          : h('p', { class: 'muted' }, 'в модели нет морф-таргетов — сохраню коэффициенты в JSON, их можно применить в своём коде'),
        targets.length ? h('button', { onclick: async () => {
          try {
            await api(`/api/models/${project.model_id}/face`, { method: 'PUT',
              body: { node: face.node, mapping } });
            toast('сопоставление мимики сохранено');
          } catch (e) { fail(e); }
        } }, 'Сохранить') : null)),
    h('section', { class: 'panel' }, stage,
      h('div', { class: 'body muted mono' }, 'результат кладётся в проект — там же его можно скачать'))));

  viewer.load(u(`/api/models/${project.model_id}/file`)).catch(() => {});
}

/* ---------- экран: замена лица ---------- */
async function screenSwap(pid) {
  const project = await api(`/api/projects/${pid}`);
  crumbs([['Проекты', '#/'], [project.name, `#/p/${pid}`], ['Замена лица', null]]);
  const { enabled } = await api('/api/faceswap');
  const videos = (project.sources || []).filter((s) => s.kind === 'video');
  const photos = (project.sources || []).filter((s) => s.kind === 'photo');
  const progress = progressBox();
  const out = h('div', { class: 'col' });

  const video = h('select', {}, ...videos.map((s) => h('option', { value: s.id }, s.name)));
  const photo = h('select', {}, ...photos.map((s) => h('option', { value: s.id }, s.name)));

  const run = async () => {
    progress.show(); progress.tick({ progress: 0, stage: 'ставлю в очередь' });
    try {
      const job = await api(`/api/projects/${pid}/faceswap`, { method: 'POST',
        body: { source_id: video.value, face_id: photo.value } });
      const done = await watchJob(job, progress.tick);
      out.replaceChildren(...(done.results || []).map((r) => h('div', { class: 'col' },
        h('b', {}, r.name),
        h('video', { src: u(`/api/projects/${pid}/results/${r.id}/file`), controls: true,
                     style: 'width:100%;border:3px solid var(--line)' }))));
      toast('лицо заменено');
    } catch (e) { fail(e); } finally { progress.show(false); }
  };

  app.textContent = '';
  app.append(h('div', { class: 'col' },
    panel('Замена лица на видео',
      !enabled
        ? h('p', { class: 'muted' }, 'провайдер замены лица не подключён (переменная FACESWAP_URL)')
        : h('div', { class: 'col' },
            h('div', {}, h('label', {}, 'видео из проекта'), video),
            h('div', {}, h('label', {}, 'лицо-донор (фото из проекта)'), photo),
            h('div', { class: 'muted' },
              'кадры, где лицо не нашлось, разрывают ролик: получится несколько отрезков'),
            h('button', { class: 'primary', disabled: !videos.length || !photos.length, onclick: run },
              'Заменить лицо'),
            progress.node),
      h('button', { onclick: () => { location.hash = `#/p/${pid}`; } }, '← в проект')),
    out));
}

/* ---------- маршрутизация ---------- */
let currentViewer = null;
const ROUTES = [
  [/^#\/?$/, screenProjects],
  [/^#\/p\/([a-z0-9]+)$/, screenProject],
  [/^#\/p\/([a-z0-9]+)\/skeleton$/, screenSkeleton],
  [/^#\/p\/([a-z0-9]+)\/face$/, screenFace],
  [/^#\/p\/([a-z0-9]+)\/swap$/, screenSwap],
];

async function render() {
  if (currentViewer) { currentViewer.dispose(); currentViewer = null; }
  const hash = location.hash || '#/';
  for (const [pattern, screen] of ROUTES) {
    const m = hash.match(pattern);
    if (m) {
      app.replaceChildren(h('p', { class: 'muted mono' }, 'загружаю…'));
      try { await screen(...m.slice(1)); } catch (e) { fail(e); app.replaceChildren(h('p', { class: 'muted' }, e.message)); }
      return;
    }
  }
  location.hash = '#/';
}

addEventListener('hashchange', render);
render();
api('/api/health').then((s) => {
  document.getElementById('health').textContent = `проектов ${s.projects} · моделей ${s.models}`;
}).catch(() => {});
