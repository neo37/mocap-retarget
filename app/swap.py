"""Замена лица на видео — через внешний сервис.

В образе нет ни insightface, ни весов inswapper: они тяжёлые, а лицензия на
веса мутная, поэтому в открытый репозиторий им дороги нет. Экран «Замена лица»
включается переменной `FACESWAP_URL` и разговаривает с провайдером по простому
протоколу: положить пару файлов → получить id задачи → забрать готовые ролики.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

BASE = os.environ.get('FACESWAP_URL', '').rstrip('/')
TOKEN = os.environ.get('FACESWAP_TOKEN', '')
TIMEOUT = int(os.environ.get('FACESWAP_TIMEOUT', '1800'))


def enabled() -> bool:
    return bool(BASE)


def _headers() -> dict:
    return {'X-Internal-Token': TOKEN} if TOKEN else {}


def run(source_image: Path, target_video: Path, out_dir: Path, progress=None) -> list[Path]:
    """Отправить фото-донор и видео, дождаться и скачать готовые отрезки."""
    if not enabled():
        raise RuntimeError('провайдер замены лица не настроен (FACESWAP_URL)')
    with open(source_image, 'rb') as src, open(target_video, 'rb') as dst:
        r = requests.post(BASE, headers=_headers(), timeout=600,
                          files={'source': (source_image.name, src),
                                 'target': (target_video.name, dst)})
    if r.status_code >= 400:
        raise RuntimeError(f'провайдер ответил {r.status_code}: {r.text[:200]}')
    job = r.json()['job']

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        time.sleep(5)
        state = requests.get(f'{BASE}/{job}', headers=_headers(), timeout=60).json()
        if progress and state.get('progress') is not None:
            progress(float(state['progress']))
        if state.get('status') == 'ok':
            break
        if state.get('status') == 'error':
            raise RuntimeError(state.get('error') or 'замена лица не удалась')
    else:
        raise RuntimeError('провайдер не ответил за отведённое время')

    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for url in state.get('files', []):
        full = url if url.startswith('http') else f'{BASE}/{job}/files/{url}'
        target = out_dir / Path(url).name
        with requests.get(full, headers=_headers(), stream=True, timeout=600) as resp:
            resp.raise_for_status()
            with target.open('wb') as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
        saved.append(target)
    return saved
