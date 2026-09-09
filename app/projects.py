"""Проекты: модель, её видео и всё, что из них получилось.

Проект — это каталог с `project.json` и папкой результатов. Базы по-прежнему нет:
состояние проекта целиком помещается в один небольшой файл, а результаты — это
готовые `.glb`, которые и так лежат на диске.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path


class Projects:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- пути ----------

    def dir(self, pid: str) -> Path:
        return self.root / pid

    def results_dir(self, pid: str) -> Path:
        d = self.dir(pid) / 'results'
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---------- чтение и запись ----------

    def load(self, pid: str) -> dict | None:
        path = self.dir(pid) / 'project.json'
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding='utf-8'))

    def save(self, project: dict) -> dict:
        project['updated'] = time.time()
        d = self.dir(project['id'])
        d.mkdir(parents=True, exist_ok=True)
        (d / 'project.json').write_text(json.dumps(project, ensure_ascii=False, indent=1),
                                        encoding='utf-8')
        return project

    def create(self, name: str) -> dict:
        pid = uuid.uuid4().hex[:12]
        return self.save({
            'id': pid,
            'name': (name or '').strip() or 'Без названия',
            'created': time.time(),
            'model_id': None,
            'sources': [],
            'results': [],
        })

    def all(self) -> list[dict]:
        out = []
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            p = self.load(d.name)
            if p:
                out.append(p)
        return sorted(out, key=lambda p: p.get('updated', 0), reverse=True)

    def delete(self, pid: str) -> None:
        shutil.rmtree(self.dir(pid), ignore_errors=True)

    # ---------- содержимое ----------

    def add_source(self, project: dict, source: dict) -> dict:
        """Источник — видео или фотография; поза снимается и с того, и с другого."""
        project['sources'] = [v for v in project.get('sources', []) if v['id'] != source['id']]
        project['sources'].insert(0, {**source, 'added': time.time()})
        return self.save(project)

    def drop_source(self, project: dict, source_id: str) -> dict:
        project['sources'] = [v for v in project.get('sources', []) if v['id'] != source_id]
        return self.save(project)

    def add_result(self, project: dict, result: dict) -> dict:
        project['results'].insert(0, result)
        return self.save(project)

    def drop_result(self, project: dict, rid: str) -> dict:
        for r in project['results']:
            if r['id'] == rid:
                for key in ('file', 'animation'):
                    if r.get(key):
                        Path(self.results_dir(project['id']) / r[key]).unlink(missing_ok=True)
        project['results'] = [r for r in project['results'] if r['id'] != rid]
        return self.save(project)
