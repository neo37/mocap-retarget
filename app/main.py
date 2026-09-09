"""HTTP-слой: проекты, модели, видео, полоса кадров и съёмка движения.

Хранилище — обычный каталог (том docker), без базы: проект целиком помещается в
один `project.json`, а результаты — это готовые `.glb` рядом с ним.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import face, frames, gltf, pose, retarget, swap
from .humanoid import SLOTS, guess_mapping, missing_required
from .projects import Projects

DATA = Path('/data')
MODELS = DATA / 'models'
VIDEOS = DATA / 'videos'
PHOTOS = DATA / 'photos'
JOBS = DATA / 'jobs'
THUMBS = DATA / 'thumbs'
IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
for p in (MODELS, VIDEOS, PHOTOS, JOBS, THUMBS):
    p.mkdir(parents=True, exist_ok=True)

projects = Projects(DATA / 'projects')

app = FastAPI(title='Захват движения из видео')
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- общее

def _model_dir(model_id: str) -> Path:
    d = MODELS / model_id
    if not d.exists():
        raise HTTPException(404, 'модель не найдена')
    return d


def _meta(path: Path) -> dict:
    return json.loads((path / 'meta.json').read_text(encoding='utf-8'))


def _project(pid: str) -> dict:
    p = projects.load(pid)
    if not p:
        raise HTTPException(404, 'проект не найден')
    return p


def _video_path(video_id: str) -> Path:
    path = VIDEOS / video_id
    if path.parent != VIDEOS or not path.exists():
        raise HTTPException(404, 'видео не найдено')
    return path


def _store_model(file: UploadFile) -> dict:
    if not (file.filename or '').lower().endswith(('.glb', '.gltf')):
        raise HTTPException(400, 'нужен файл .glb')
    model_id = uuid.uuid4().hex[:12]
    d = MODELS / model_id
    d.mkdir(parents=True)
    target = d / 'model.glb'
    with target.open('wb') as out:
        shutil.copyfileobj(file.file, out)
    try:
        glb = gltf.GLB.load(str(target))
        bones = glb.bone_names()
        if not bones:
            raise ValueError('в модели нет скелета (skin) — анимировать нечего')
    except Exception as e:  # noqa: BLE001 — показываем причину пользователю
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f'не смог разобрать модель: {e}')
    mapping = guess_mapping(bones)
    meta = {
        'id': model_id,
        'name': file.filename,
        'bones': bones,
        'mapping': mapping,
        'clips': glb.animation_names(),
        'uploaded': time.time(),
    }
    (d / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    return {**meta, 'missing': missing_required(mapping), 'slots': SLOTS}


def _store_video(file: UploadFile) -> dict:
    video_id = uuid.uuid4().hex[:12]
    suffix = Path(file.filename or '').suffix or '.mp4'
    target = VIDEOS / f'{video_id}{suffix}'
    with target.open('wb') as out:
        shutil.copyfileobj(file.file, out)
    return {'id': target.name, 'name': file.filename or target.name, 'size': target.stat().st_size}


def _store_source(file: UploadFile) -> dict:
    """Положить источник: видео идёт в кадры, фотография — сразу одна поза."""
    name = file.filename or 'source'
    suffix = Path(name).suffix.lower()
    photo = suffix in IMAGE_SUFFIXES or (file.content_type or '').startswith('image/')
    sid = uuid.uuid4().hex[:12] + (suffix or ('.jpg' if photo else '.mp4'))
    target = (PHOTOS if photo else VIDEOS) / sid
    with target.open('wb') as out:
        shutil.copyfileobj(file.file, out)
    return {'id': sid, 'kind': 'photo' if photo else 'video', 'name': name,
            'size': target.stat().st_size}


def _source_path(source_id: str) -> tuple[Path, str]:
    """Путь и вид источника по его id (суффикс в id и решает, где искать)."""
    for base, kind in ((VIDEOS, 'video'), (PHOTOS, 'photo')):
        path = base / source_id
        if path.parent == base and path.exists():
            return path, kind
    raise HTTPException(404, 'источник не найден')


def _model_brief(model_id: str | None) -> dict | None:
    if not model_id or not (MODELS / model_id / 'meta.json').exists():
        return None
    m = _meta(MODELS / model_id)
    return {'id': m['id'], 'name': m['name'], 'bones': len(m['bones']),
            'missing': missing_required(m['mapping']), 'clips': m.get('clips', [])}


def _with_model(project: dict) -> dict:
    return {**project, 'model': _model_brief(project.get('model_id'))}


def _new_job(**fields) -> dict:
    job = {'id': uuid.uuid4().hex[:12], 'status': 'running', 'stage': 'в очереди',
           'progress': 0.0, **fields}
    with _lock:
        _jobs[job['id']] = job
    return job


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


# ---------------------------------------------------------------- модели

@app.post('/api/models')
async def upload_model(file: UploadFile = File(...)):
    return _store_model(file)


@app.get('/api/models')
async def list_models():
    out = []
    for d in sorted(MODELS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if (d / 'meta.json').exists():
            out.append(_model_brief(d.name))
    return out


@app.get('/api/models/{model_id}')
async def get_model(model_id: str):
    m = _meta(_model_dir(model_id))
    return {**m, 'missing': missing_required(m['mapping']), 'slots': SLOTS}


@app.put('/api/models/{model_id}/mapping')
async def set_mapping(model_id: str, mapping: dict = Body(...)):
    d = _model_dir(model_id)
    m = _meta(d)
    known = set(m['bones'])
    cleaned = {slot: (name if name in known else None)
               for slot, name in mapping.items() if slot in SLOTS}
    m['mapping'] = cleaned
    (d / 'meta.json').write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'mapping': cleaned, 'missing': missing_required(cleaned)}


@app.get('/api/models/{model_id}/file')
async def model_file(model_id: str):
    return FileResponse(_model_dir(model_id) / 'model.glb', media_type='model/gltf-binary')


# ---------------------------------------------------------------- видео и кадры

@app.post('/api/videos')
async def upload_video(file: UploadFile = File(...)):
    return _store_video(file)


@app.get('/api/videos')
async def list_videos():
    return [{'id': p.name, 'name': p.name, 'size': p.stat().st_size}
            for p in sorted(VIDEOS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if p.is_file()]


@app.get('/api/sources/{source_id}/frames')
async def source_frames(source_id: str, count: int = frames.DEFAULT_COUNT):
    path, kind = _source_path(source_id)
    if kind == 'photo':
        return {'kind': 'photo', 'fps': 0, 'duration': 0, 'count': 1,
                'frames': [{'index': 0, 'time': 0.0,
                            'url': f'/api/sources/{source_id}/file'}]}
    try:
        meta = frames.strip(path, THUMBS / source_id, max(8, min(count, 240)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**meta, 'kind': 'video', 'source_id': source_id,
            'frames': [{**f, 'url': f'/api/sources/{source_id}/frames/{f["file"]}'}
                       for f in meta['frames']]}


@app.get('/api/sources/{source_id}/frames/{name}')
async def source_frame(source_id: str, name: str):
    path = (THUMBS / source_id / name).resolve()
    if not path.is_relative_to((THUMBS / source_id).resolve()) or not path.exists():
        raise HTTPException(404, 'кадр не найден')
    return FileResponse(path, media_type='image/jpeg')


@app.get('/api/sources/{source_id}/file')
async def source_file(source_id: str):
    path, kind = _source_path(source_id)
    return FileResponse(path, media_type='video/mp4' if kind == 'video' else 'image/jpeg')


# ---------------------------------------------------------------- проекты

@app.get('/api/projects')
async def list_projects():
    return [{'id': p['id'], 'name': p['name'], 'created': p.get('created'),
             'updated': p.get('updated'), 'model': _model_brief(p.get('model_id')),
             'sources': len(p.get('sources', [])), 'results': len(p.get('results', []))}
            for p in projects.all()]


@app.post('/api/projects')
async def create_project(payload: dict = Body(default={})):
    return _with_model(projects.create(payload.get('name', '')))


@app.get('/api/projects/{pid}')
async def get_project(pid: str):
    return _with_model(_project(pid))


@app.patch('/api/projects/{pid}')
async def patch_project(pid: str, payload: dict = Body(...)):
    project = _project(pid)
    if 'name' in payload:
        project['name'] = (payload['name'] or '').strip() or project['name']
    if 'model_id' in payload:
        model_id = payload['model_id']
        if model_id:
            _model_dir(model_id)
        project['model_id'] = model_id
    return _with_model(projects.save(project))


@app.delete('/api/projects/{pid}')
async def delete_project(pid: str):
    _project(pid)
    projects.delete(pid)
    return {'ok': True}


@app.post('/api/projects/{pid}/model')
async def project_model(pid: str, file: UploadFile = File(...)):
    project = _project(pid)
    meta = _store_model(file)
    project['model_id'] = meta['id']
    projects.save(project)
    return meta


@app.post('/api/projects/{pid}/sources')
async def project_source(pid: str, file: UploadFile = File(...)):
    project = _project(pid)
    source = _store_source(file)
    projects.add_source(project, source)
    return source


@app.delete('/api/projects/{pid}/sources/{source_id}')
async def project_source_delete(pid: str, source_id: str):
    projects.drop_source(_project(pid), source_id)
    return {'ok': True}


# ---------------------------------------------------------------- результаты

def _result_file(pid: str, rid: str, project: dict, key: str) -> Path:
    for r in project.get('results', []):
        if r['id'] == rid and r.get(key):
            path = projects.results_dir(pid) / r[key]
            if path.exists():
                return path
    raise HTTPException(404, 'файла нет')


@app.get('/api/projects/{pid}/results/{rid}/file')
async def result_file(pid: str, rid: str):
    project = _project(pid)
    path = _result_file(pid, rid, project, 'file')
    kind = 'video/mp4' if path.suffix == '.mp4' else 'model/gltf-binary'
    return FileResponse(path, media_type=kind, filename=path.name)


@app.get('/api/projects/{pid}/results/{rid}/animation.json')
async def result_animation(pid: str, rid: str):
    project = _project(pid)
    return FileResponse(_result_file(pid, rid, project, 'animation'),
                        media_type='application/json', filename='animation.json')


@app.delete('/api/projects/{pid}/results/{rid}')
async def result_delete(pid: str, rid: str):
    projects.drop_result(_project(pid), rid)
    return {'ok': True}


# ---------------------------------------------------------------- съёмка

def _mapping_of(model_id: str) -> tuple[Path, dict]:
    model_dir = _model_dir(model_id)
    meta = _meta(model_dir)
    missing = missing_required(meta['mapping'])
    if missing:
        raise ValueError('не сопоставлены обязательные кости: ' + ', '.join(missing))
    return model_dir, meta


def _register(pid: str, result: dict) -> None:
    """Дописать результат в проект, перечитав его с диска: пока шла задача,
    проект могли переименовать или пополнить."""
    project = projects.load(pid)
    if project:
        projects.add_result(project, result)


def _run_poses(job_id: str, pid: str, model_id: str, source_id: str,
               times: list[float], base_name: str):
    job = _jobs[job_id]
    try:
        model_dir, meta = _mapping_of(model_id)
        path, kind = _source_path(source_id)
        job['stage'] = 'ищу человека на кадрах'
        capture = (pose.extract_image(str(path)) if kind == 'photo'
                   else pose.extract_at(str(path), times))
        out_dir = projects.results_dir(pid)
        made = []
        for i in range(capture.frames):
            t = float(capture.times[i])
            job.update(stage=f'ставлю позу на {t:.2f} с', progress=round(i / capture.frames, 3))
            glb = gltf.GLB.load(str(model_dir / 'model.glb'))
            retarget.pose_frame(glb, meta['mapping'], capture, i)
            glb.drop_animations()
            rid = uuid.uuid4().hex[:10]
            (out_dir / f'{rid}.glb').write_bytes(glb.to_bytes())
            label = base_name if kind == 'photo' else f'{base_name} {t:.2f} с'
            result = {'id': rid, 'kind': 'pose', 'name': label,
                      'created': time.time(), 'source': source_id, 'time': round(t, 3),
                      'model': model_id, 'file': f'{rid}.glb'}
            _register(pid, result)
            made.append(result)
        job.update(stage='готово', progress=1.0, status='ok', results=made,
                   skipped=max(0, len(times) - capture.frames))
    except Exception as e:  # noqa: BLE001 — любую поломку показываем в интерфейсе
        job.update(status='error', stage='ошибка', error=str(e))


def _run_clip(job_id: str, pid: str | None, model_id: str, source_id: str, clip_name: str,
              max_seconds: float, root_motion: bool, smooth_window: int):
    job = _jobs[job_id]
    try:
        model_dir, meta = _mapping_of(model_id)
        path, _ = _source_path(source_id)
        job['stage'] = 'разбираю видео'
        capture = pose.extract(str(path), max_seconds=max_seconds,
                               progress=lambda p: job.update(progress=round(p * 0.8, 3)))
        capture = pose.smooth(capture, smooth_window)
        job.update(stage='переношу на скелет', progress=0.85,
                   frames=capture.frames, detected=capture.detected, fps=round(capture.fps, 2))

        glb = gltf.GLB.load(str(model_dir / 'model.glb'))
        retarget.retarget(glb, meta['mapping'], capture, clip_name=clip_name,
                          root_motion=root_motion)
        animation = _animation_json(glb, clip_name, capture)

        if pid:
            out_dir = projects.results_dir(pid)
            rid = uuid.uuid4().hex[:10]
            (out_dir / f'{rid}.glb').write_bytes(glb.to_bytes())
            (out_dir / f'{rid}.json').write_text(json.dumps(animation, ensure_ascii=False),
                                                 encoding='utf-8')
            result = {'id': rid, 'kind': 'clip', 'name': clip_name, 'created': time.time(),
                      'source': source_id, 'model': model_id, 'file': f'{rid}.glb',
                      'animation': f'{rid}.json', 'frames': capture.frames,
                      'detected': capture.detected}
            _register(pid, result)
            job.update(stage='готово', progress=1.0, status='ok', results=[result])
            return

        out_dir = JOBS / job_id                    # старый путь: задача вне проекта
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / 'result.glb').write_bytes(glb.to_bytes())
        (out_dir / 'animation.json').write_text(json.dumps(animation, ensure_ascii=False),
                                                encoding='utf-8')
        job.update(stage='готово', progress=1.0, status='ok', clip=clip_name,
                   result=f'/api/jobs/{job_id}/download',
                   animation=f'/api/jobs/{job_id}/animation.json')
    except Exception as e:  # noqa: BLE001
        job.update(status='error', stage='ошибка', error=str(e))


def _animation_json(glb: gltf.GLB, clip_name: str, capture) -> dict:
    """Сама анимация отдельно от модели: времена и кватернионы по костям."""
    anim = glb.json['animations'][-1]
    tracks: dict[str, dict] = {}
    for ch in anim['channels']:
        node = glb.nodes[ch['target']['node']].get('name', str(ch['target']['node']))
        sampler = anim['samplers'][ch['sampler']]
        tracks.setdefault(node, {})[ch['target']['path']] = glb.accessor(sampler['output']).tolist()
    return {'name': clip_name, 'fps': capture.fps, 'times': capture.times.tolist(),
            'tracks': tracks}


@app.post('/api/projects/{pid}/poses')
async def create_poses(pid: str, payload: dict = Body(...)):
    project = _project(pid)
    model_id = payload.get('model_id') or project.get('model_id')
    source_id = payload.get('source_id')
    times = [float(t) for t in (payload.get('times') or [])]
    if not model_id:
        raise HTTPException(400, 'в проекте нет модели')
    if not source_id:
        raise HTTPException(400, 'нужен источник')
    _, kind = _source_path(source_id)
    if kind == 'video' and not times:
        raise HTTPException(400, 'выберите хотя бы один кадр')
    job = _new_job(project=pid, kind='pose', model=model_id, source=source_id, times=times)
    _spawn(_run_poses, job['id'], pid, model_id, source_id, times or [0.0],
           payload.get('name') or 'Поза')
    return job


@app.post('/api/projects/{pid}/clips')
async def create_clip(pid: str, payload: dict = Body(...)):
    project = _project(pid)
    model_id = payload.get('model_id') or project.get('model_id')
    source_id = payload.get('source_id')
    if not model_id:
        raise HTTPException(400, 'в проекте нет модели')
    if not source_id:
        raise HTTPException(400, 'нужно видео')
    _, kind = _source_path(source_id)
    if kind != 'video':
        raise HTTPException(400, 'для клипа нужно видео, а не фотография')
    job = _new_job(project=pid, kind='clip', model=model_id, source=source_id)
    _spawn(_run_clip, job['id'], pid, model_id, source_id,
           payload.get('clip_name') or 'Захват с видео',
           float(payload.get('max_seconds') or 15), bool(payload.get('root_motion')),
           int(payload.get('smooth') or 5))
    return job


@app.post('/api/projects/{pid}/results/{rid}/trim')
async def trim_result(pid: str, rid: str, payload: dict = Body(...)):
    """Вырезать кусок готового клипа в отдельный результат — режим редактирования."""
    project = _project(pid)
    source = next((r for r in project['results'] if r['id'] == rid), None)
    if not source or not source.get('animation'):
        raise HTTPException(404, 'у этого результата нет анимации')
    model_id = source.get('model') or project.get('model_id')
    animation = json.loads(_result_file(pid, rid, project, 'animation').read_text(encoding='utf-8'))
    start = float(payload.get('start') or 0.0)
    end = payload.get('end')
    end = float(end) if end is not None else None
    glb = gltf.GLB.load(str(_model_dir(model_id) / 'model.glb'))
    name = payload.get('name') or f'{source["name"]} · фрагмент'
    try:
        kept = retarget.apply_animation(glb, animation, start, end, clip_name=name)
    except ValueError as e:
        raise HTTPException(400, str(e))

    stop = end if end is not None else animation['times'][-1]
    keep = [i for i, t in enumerate(animation['times']) if start - 1e-6 <= t <= stop + 1e-6]
    first, last = keep[0], keep[-1] + 1
    zero = animation['times'][first]
    cut = {**animation, 'name': name,
           'times': [round(t - zero, 4) for t in animation['times'][first:last]],
           'tracks': {bone: {path: values[first:last] for path, values in paths.items()}
                      for bone, paths in animation['tracks'].items()}}

    out_dir = projects.results_dir(pid)
    new_id = uuid.uuid4().hex[:10]
    (out_dir / f'{new_id}.glb').write_bytes(glb.to_bytes())
    (out_dir / f'{new_id}.json').write_text(json.dumps(cut, ensure_ascii=False), encoding='utf-8')
    result = {'id': new_id, 'kind': 'clip', 'name': name, 'created': time.time(),
              'source': source.get('source'), 'model': model_id, 'file': f'{new_id}.glb',
              'animation': f'{new_id}.json', 'frames': kept, 'trimmed_from': rid,
              'range': [round(zero, 3), round(animation['times'][last - 1], 3)]}
    _register(pid, result)
    return result


# ---------------------------------------------------------------- мимика

def _face_setup(model_id: str) -> tuple[Path, dict, list[dict]]:
    model_dir = _model_dir(model_id)
    meta = _meta(model_dir)
    morphs = gltf.GLB.load(str(model_dir / 'model.glb')).morph_meshes()
    return model_dir, meta, morphs


@app.get('/api/models/{model_id}/face')
async def get_face(model_id: str):
    """Морф-таргеты модели и то, как на них ложатся коэффициенты мимики."""
    _, meta, morphs = _face_setup(model_id)
    saved = meta.get('face') or {}
    node = saved.get('node') if saved.get('node') is not None else (morphs[0]['node'] if morphs else None)
    names = next((m['names'] for m in morphs if m['node'] == node), [])
    mapping = saved.get('mapping') or face.guess_mapping(names)
    return {'shapes': list(face.SHAPES), 'meshes': morphs, 'node': node,
            'mapping': mapping, 'targets': names}


@app.put('/api/models/{model_id}/face')
async def set_face(model_id: str, payload: dict = Body(...)):
    model_dir, meta, morphs = _face_setup(model_id)
    node = payload.get('node')
    names = set(next((m['names'] for m in morphs if m['node'] == node), []))
    mapping = {k: v for k, v in (payload.get('mapping') or {}).items()
               if k in face.SHAPES and v in names}
    meta['face'] = {'node': node, 'mapping': mapping}
    (model_dir / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                         encoding='utf-8')
    return meta['face']


def _run_face(job_id: str, pid: str, model_id: str, source_id: str, mode: str,
              times: list[float], max_seconds: float, name: str):
    job = _jobs[job_id]
    try:
        model_dir, meta, morphs = _face_setup(model_id)
        path, kind = _source_path(source_id)
        saved = meta.get('face') or {}
        node = saved.get('node') if saved.get('node') is not None else (morphs[0]['node'] if morphs else None)
        targets = next((m['names'] for m in morphs if m['node'] == node), [])
        mapping = saved.get('mapping') or (face.guess_mapping(targets) if targets else {})

        out_dir = projects.results_dir(pid)
        rid = uuid.uuid4().hex[:10]
        glb = gltf.GLB.load(str(model_dir / 'model.glb'))

        if mode == 'clip' and kind == 'video':
            job['stage'] = 'читаю мимику с видео'
            stamps, series = face.from_video(str(path), max_seconds=max_seconds,
                                             progress=lambda p: job.update(progress=round(p * 0.9, 3)))
            frames_count = len(stamps)
            matrix = np.array([[series.get(shape, np.zeros(frames_count))[i] for shape in face.SHAPES]
                               for i in range(frames_count)])
            payload = {'kind': 'face', 'name': name, 'times': stamps.tolist(),
                       'shapes': {k: v.tolist() for k, v in series.items()}}
            if targets:
                weights = np.array([face.weights(targets, mapping,
                                                 dict(zip(face.SHAPES, row))) for row in matrix],
                                   dtype=np.float32)
                glb.add_weights_animation(name, stamps.astype(np.float32), node, weights)
        else:
            job['stage'] = 'читаю мимику'
            scores = (face.from_image(str(path)) if kind == 'photo'
                      else face.from_video_at(str(path), times or [0.0])[0])
            payload = {'kind': 'face', 'name': name, 'scores': scores}
            if targets:
                glb.drop_animations()
                glb.set_weights(node, face.weights(targets, mapping, scores))

        result = {'id': rid, 'kind': 'face', 'name': name, 'created': time.time(),
                  'source': source_id, 'model': model_id,
                  'animation': f'{rid}.json', 'targets': len(targets)}
        (out_dir / f'{rid}.json').write_text(json.dumps(payload, ensure_ascii=False),
                                             encoding='utf-8')
        if targets:
            (out_dir / f'{rid}.glb').write_bytes(glb.to_bytes())
            result['file'] = f'{rid}.glb'
        _register(pid, result)
        job.update(stage='готово', progress=1.0, status='ok', results=[result],
                   note=None if targets else 'в модели нет морф-таргетов — сохранил только коэффициенты')
    except Exception as e:  # noqa: BLE001
        job.update(status='error', stage='ошибка', error=str(e))


@app.post('/api/projects/{pid}/face')
async def create_face(pid: str, payload: dict = Body(...)):
    project = _project(pid)
    model_id = payload.get('model_id') or project.get('model_id')
    source_id = payload.get('source_id')
    if not model_id:
        raise HTTPException(400, 'в проекте нет модели')
    if not source_id:
        raise HTTPException(400, 'нужен источник')
    _source_path(source_id)
    job = _new_job(project=pid, kind='face', model=model_id, source=source_id)
    _spawn(_run_face, job['id'], pid, model_id, source_id, payload.get('mode') or 'pose',
           [float(t) for t in (payload.get('times') or [])],
           float(payload.get('max_seconds') or 15), payload.get('name') or 'Мимика')
    return job


# ---------------------------------------------------------------- замена лица

def _run_swap(job_id: str, pid: str, video_id: str, photo_id: str, name: str):
    job = _jobs[job_id]
    try:
        video, _ = _source_path(video_id)
        photo, _ = _source_path(photo_id)
        job['stage'] = 'меняю лицо покадрово'
        out_dir = projects.results_dir(pid)
        files = swap.run(photo, video, out_dir,
                         progress=lambda p: job.update(progress=round(p, 3)))
        made = []
        for i, f in enumerate(files, 1):
            rid = uuid.uuid4().hex[:10]
            target = out_dir / f'{rid}.mp4'
            f.replace(target)
            result = {'id': rid, 'kind': 'video', 'created': time.time(),
                      'name': f'{name} · отрезок {i}' if len(files) > 1 else name,
                      'source': video_id, 'file': f'{rid}.mp4'}
            _register(pid, result)
            made.append(result)
        if not made:
            raise RuntimeError('ни на одном кадре не удалось распознать лицо')
        job.update(stage='готово', progress=1.0, status='ok', results=made)
    except Exception as e:  # noqa: BLE001
        job.update(status='error', stage='ошибка', error=str(e))


@app.get('/api/faceswap')
async def faceswap_state():
    return {'enabled': swap.enabled()}


@app.post('/api/projects/{pid}/faceswap')
async def create_swap(pid: str, payload: dict = Body(...)):
    _project(pid)
    if not swap.enabled():
        raise HTTPException(503, 'провайдер замены лица не настроен')
    video_id, photo_id = payload.get('source_id'), payload.get('face_id')
    if not video_id or not photo_id:
        raise HTTPException(400, 'нужны видео и фотография с лицом')
    _, kind = _source_path(video_id)
    if kind != 'video':
        raise HTTPException(400, 'заменять лицо нужно на видео')
    _, kind = _source_path(photo_id)
    if kind != 'photo':
        raise HTTPException(400, 'лицо-донор — это фотография')
    job = _new_job(project=pid, kind='swap', source=video_id, face=photo_id)
    _spawn(_run_swap, job['id'], pid, video_id, photo_id, payload.get('name') or 'Замена лица')
    return job


@app.post('/api/jobs')
async def create_job(payload: dict = Body(...)):
    """Съёмка вне проекта — этим ходит телеграм-бот и внешние вызовы."""
    model_id = payload.get('model_id')
    video_id = payload.get('video_id')
    if not model_id or not video_id:
        raise HTTPException(400, 'нужны модель и видео')
    _model_dir(model_id)
    _video_path(video_id)
    job = _new_job(model=model_id, video=video_id, kind='clip')
    _spawn(_run_clip, job['id'], None, model_id, video_id,
           payload.get('clip_name') or 'Захват с видео',
           float(payload.get('max_seconds') or 15), bool(payload.get('root_motion')),
           int(payload.get('smooth') or 5))
    return job


@app.get('/api/jobs/{job_id}')
async def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, 'задача не найдена')
    return job


@app.get('/api/jobs/{job_id}/download')
async def job_download(job_id: str):
    path = JOBS / job_id / 'result.glb'
    if not path.exists():
        raise HTTPException(404, 'результата ещё нет')
    return FileResponse(path, media_type='model/gltf-binary', filename='animated.glb')


@app.get('/api/jobs/{job_id}/animation.json')
async def job_animation(job_id: str):
    path = JOBS / job_id / 'animation.json'
    if not path.exists():
        raise HTTPException(404, 'результата ещё нет')
    return FileResponse(path, media_type='application/json', filename='animation.json')


@app.get('/api/health')
async def health():
    return JSONResponse({'ok': True, 'models': len(list(MODELS.iterdir())),
                         'projects': len(list(projects.root.iterdir())), 'jobs': len(_jobs)})


app.mount('/', StaticFiles(directory=str(Path(__file__).parent / 'static'), html=True),
          name='static')
