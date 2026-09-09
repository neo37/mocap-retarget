"""HTTP-слой: загрузка моделей и видео, сопоставление костей, съёмка движения.

Хранилище — обычный каталог (том docker), без базы: у задачи нет состояния,
которое стоило бы хранить дольше, чем сам файл результата.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import gltf, pose, retarget
from .humanoid import SLOTS, guess_mapping, missing_required

DATA = Path('/data')
MODELS = DATA / 'models'
VIDEOS = DATA / 'videos'
JOBS = DATA / 'jobs'
for p in (MODELS, VIDEOS, JOBS):
    p.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Захват движения из видео')
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _model_dir(model_id: str) -> Path:
    d = MODELS / model_id
    if not d.exists():
        raise HTTPException(404, 'модель не найдена')
    return d


def _meta(path: Path) -> dict:
    return json.loads((path / 'meta.json').read_text(encoding='utf-8'))


@app.post('/api/models')
async def upload_model(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.glb', '.gltf')):
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
    except Exception as e:
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


@app.get('/api/models')
async def list_models():
    out = []
    for d in sorted(MODELS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if (d / 'meta.json').exists():
            m = _meta(d)
            out.append({'id': m['id'], 'name': m['name'], 'bones': len(m['bones']),
                        'missing': missing_required(m['mapping']), 'clips': m.get('clips', [])})
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
    cleaned = {slot: (name if name in known else None) for slot, name in mapping.items() if slot in SLOTS}
    m['mapping'] = cleaned
    (d / 'meta.json').write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'mapping': cleaned, 'missing': missing_required(cleaned)}


@app.get('/api/models/{model_id}/file')
async def model_file(model_id: str):
    return FileResponse(_model_dir(model_id) / 'model.glb', media_type='model/gltf-binary')


@app.post('/api/videos')
async def upload_video(file: UploadFile = File(...)):
    video_id = uuid.uuid4().hex[:12]
    suffix = Path(file.filename).suffix or '.mp4'
    target = VIDEOS / f'{video_id}{suffix}'
    with target.open('wb') as out:
        shutil.copyfileobj(file.file, out)
    return {'id': target.name, 'name': file.filename, 'size': target.stat().st_size}


@app.get('/api/videos')
async def list_videos():
    return [{'id': p.name, 'name': p.name, 'size': p.stat().st_size}
            for p in sorted(VIDEOS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if p.is_file()]


def _run_job(job_id: str, model_id: str, video_name: str, clip_name: str,
             max_seconds: float, root_motion: bool, smooth_window: int):
    job = _jobs[job_id]
    try:
        model_dir = MODELS / model_id
        meta = _meta(model_dir)
        missing = missing_required(meta['mapping'])
        if missing:
            raise ValueError('не сопоставлены обязательные кости: ' + ', '.join(missing))

        job['stage'] = 'разбираю видео'
        capture = pose.extract(str(VIDEOS / video_name), max_seconds=max_seconds,
                               progress=lambda p: job.update(progress=round(p * 0.8, 3)))
        capture = pose.smooth(capture, smooth_window)
        job.update(stage='переношу на скелет', progress=0.85,
                   frames=capture.frames, detected=capture.detected, fps=round(capture.fps, 2))

        glb = gltf.GLB.load(str(model_dir / 'model.glb'))
        retarget.retarget(glb, meta['mapping'], capture, clip_name=clip_name, root_motion=root_motion)

        out_dir = JOBS / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / 'result.glb').write_bytes(glb.to_bytes())

        # Отдельно — сама анимация в JSON: её можно применить где угодно, без модели.
        anim = glb.json['animations'][-1]
        tracks = {}
        for ch in anim['channels']:
            node = glb.nodes[ch['target']['node']].get('name', str(ch['target']['node']))
            sampler = anim['samplers'][ch['sampler']]
            tracks.setdefault(node, {})[ch['target']['path']] = glb.accessor(sampler['output']).tolist()
        (out_dir / 'animation.json').write_text(json.dumps({
            'name': clip_name,
            'fps': capture.fps,
            'times': capture.times.tolist(),
            'tracks': tracks,
        }, ensure_ascii=False), encoding='utf-8')

        job.update(stage='готово', progress=1.0, status='ok',
                   result=f'/api/jobs/{job_id}/download',
                   animation=f'/api/jobs/{job_id}/animation.json',
                   clip=clip_name)
    except Exception as e:  # noqa: BLE001 — любую поломку показываем в интерфейсе
        job.update(status='error', stage='ошибка', error=str(e))


@app.post('/api/jobs')
async def create_job(payload: dict = Body(...)):
    model_id = payload.get('model_id')
    video_name = payload.get('video_id')
    if not model_id or not video_name:
        raise HTTPException(400, 'нужны модель и видео')
    _model_dir(model_id)
    if not (VIDEOS / video_name).exists():
        raise HTTPException(404, 'видео не найдено')
    job_id = uuid.uuid4().hex[:12]
    job = {'id': job_id, 'status': 'running', 'stage': 'в очереди', 'progress': 0.0,
           'model': model_id, 'video': video_name}
    with _lock:
        _jobs[job_id] = job
    threading.Thread(
        target=_run_job,
        args=(job_id, model_id, video_name, payload.get('clip_name') or 'Захват с видео',
              float(payload.get('max_seconds') or 15), bool(payload.get('root_motion')),
              int(payload.get('smooth') or 5)),
        daemon=True,
    ).start()
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
    return JSONResponse({'ok': True, 'models': len(list(MODELS.iterdir())), 'jobs': len(_jobs)})


app.mount('/', StaticFiles(directory=str(Path(__file__).parent / 'static'), html=True), name='static')
