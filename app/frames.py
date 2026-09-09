"""Полоса кадров: превью видео, по которым выбирают позу.

Нарезаем один раз при первом запросе и складываем рядом с видео — повторное
открытие проекта уже ничего не считает.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import cv2

THUMB_WIDTH = 240          # шире не нужно: полоса и так прокручивается
DEFAULT_COUNT = 60         # столько превью раскладываем по всей длине видео
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def strip(video_path: Path, cache_dir: Path, count: int = DEFAULT_COUNT) -> dict:
    """Список кадров `{index, time, file}` плюс fps и длительность видео."""
    meta_path = cache_dir / 'frames.json'
    with _lock_for(str(cache_dir)):
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if meta.get('count') == count:
                return meta
        meta = _cut(video_path, cache_dir, count)
        cache_dir.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        return meta


def _cut(video_path: Path, cache_dir: Path, count: int) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError('не смог открыть видео')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if fps else 0.0
    cache_dir.mkdir(parents=True, exist_ok=True)

    picks = count if total <= 0 else min(count, total)
    frames = []
    for i in range(picks):
        t = duration * (i + 0.5) / picks if duration else i / fps
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        scale = THUMB_WIDTH / max(w, 1)
        thumb = cv2.resize(frame, (THUMB_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        name = f'{i:04d}.jpg'
        cv2.imwrite(str(cache_dir / name), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        frames.append({'index': i, 'time': round(t, 3), 'file': name})
    cap.release()
    if not frames:
        raise ValueError('в видео не нашлось ни одного кадра')
    return {'fps': round(fps, 3), 'duration': round(duration, 3), 'count': count, 'frames': frames}
