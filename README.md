# Motion capture from video → any rigged model

Upload a **video or a photo** of a person and a **rigged model** (`.glb`) — get the
model back holding that pose, or carrying a whole animation clip taken from the
video. The rig can be arbitrary: you point at the bones in a 3D view and say which
one plays which humanoid joint.

**[Live demo](https://neo37.github.io/mocap-retarget/)** — drop a photo in the
browser, get the detected pose as a 3D figure on the page (runs fully client-side).

```
docker compose up --build     →  http://127.0.0.1:8099
```

## Screens

| Screen | What it does |
|---|---|
| Projects | Every project keeps its model, its videos and photos, and everything produced from them. Stored on disk, so it is all still there after a restart. |
| Project | Pick single frames off the video's film strip → the model in exactly that pose. Or capture the whole clip. Trim a finished clip down to a fragment and save it separately. |
| Skeleton | The model's own skeleton in 3D: pick a humanoid slot, click the bone. Mapped bones light up, required-but-missing ones are called out. |
| Face | Expression transfer: 52 ARKit coefficients from a photo or video onto the model's morph targets (auto-matched by name, editable). |
| Face swap | Optional: hands a video and a donor photo to an external face-swap service (`FACESWAP_URL`), stores the resulting clips in the project. |

## What's inside

| Layer | File | Job |
|---|---|---|
| Capture | `app/pose.py` | MediaPipe Pose Landmarker: whole video, chosen frames, or a single photo — 33 points, world coordinates |
| Skeleton | `app/humanoid.py` | 19 humanoid slots, name synonyms (Meshy, Mixamo, VRM, Unreal, Rigify), automatic matching |
| Retarget | `app/retarget.py` | bone direction in the video → shortest rotation from the rest pose → local quaternion; also static poses and clip trimming |
| Face | `app/face.py` | ARKit blendshapes from MediaPipe, matched onto the model's morph targets |
| glTF | `app/gltf.py` | reading GLB, rest pose, writing rotation and morph-weight animation, baking a static pose |
| Projects | `app/projects.py`, `app/frames.py` | project files on disk, film-strip thumbnails |
| HTTP+UI | `app/main.py`, `app/static/` | uploads, jobs, three.js views |

No Blender, no game engine: forward kinematics and glTF writing are done by hand,
so the image stays small and everything runs without a GPU.

## How the retargeting works

For every bone the service takes its direction in the model's rest pose and the
direction of the same body segment in the video, computes the shortest rotation
between them, and converts it into the bone's local space using the already computed
parent rotation. Nothing is assumed about the rig's axis conventions — everything is
derived from the file itself, so Meshy models, Mixamo rigs and hand-made skeletons
all work the same way.

A single frame goes through the same maths, except the result is written into the
bones' own TRS instead of an animation track — the file opens already posed, with the
model's other clips removed so nothing plays over it.

What is not transferred: fingers, and twist around a bone's own axis — 33 MediaPipe
points do not carry it. Low-visibility points are skipped: the bone stays at rest
instead of inheriting the jitter.

## Limits

- one person in frame (`num_poses=1`);
- clips take the first 15 seconds by default (configurable, 120 max);
- the model must contain a `skin`, otherwise there is nothing to animate;
- expression transfer needs morph targets in the model — without them only the
  coefficients are saved, as JSON;
- hip movement (root motion) is off by default: the "metres → model units" scale is
  estimated from shin length and gets it wrong on unusual proportions.

## API

```
POST /api/projects                       {name}                     → project
POST /api/projects/{id}/model            multipart .glb             → bones, mapping, missing
POST /api/projects/{id}/sources          multipart video or photo   → source
GET  /api/sources/{id}/frames            ?count=60                  → film strip
POST /api/projects/{id}/poses            {source_id, times[]}       → job → one .glb per frame
POST /api/projects/{id}/clips            {source_id, max_seconds}   → job → .glb + animation.json
POST /api/projects/{id}/results/{r}/trim {start, end}               → fragment as a new result
POST /api/projects/{id}/face             {source_id, mode}          → job → morph weights
POST /api/projects/{id}/faceswap         {source_id, face_id}       → job → swapped clips
PUT  /api/models/{id}/mapping            {slot: bone}               → bone mapping
GET  /api/jobs/{id}                                                 → status, progress, results
```

`POST /api/jobs {model_id, video_id}` still exists for callers outside a project
(that is what the Telegram bot uses).

## Storage

Everything lives in `./data` (a container volume): `projects/<id>/project.json` plus
`results/`, `models/<id>/{model.glb,meta.json}`, `videos/`, `photos/`, `thumbs/`.
There is no database: a project fits in one small JSON file and the results are
files anyway.

## Running under a sub-path

The front-end resolves API paths relative to where its script is served from, so the
service works both at the root and behind a prefix, e.g.
`https://videos.ai3d.art/nella/`:

```nginx
location /nella/ {
    proxy_pass http://127.0.0.1:8099/;
    proxy_set_header Host $host;
    proxy_request_buffering off;
    proxy_read_timeout 900s;
    client_max_body_size 520M;
}
```

## Environment

| Variable | Meaning |
|---|---|
| `POSE_MODEL` | path to the pose model (`app/pose_landmarker.task` by default) |
| `FACE_MODEL` | path to the face model (`app/face_landmarker.task` by default) |
| `FACESWAP_URL` | external face-swap service; without it that screen stays off |
| `FACESWAP_TOKEN` | shared secret sent to that service as `X-Internal-Token` |

The face-swap provider protocol is deliberately tiny: `POST` the two files as
`source` and `target` → `{"job": id}`; `GET {url}/{job}` → `{"status": "running"}`
or `{"status": "ok", "files": [...]}`; `GET {url}/{job}/files/{name}` returns a clip.
Neither insightface nor the swap weights ship with this repository.

## Recognition models

`app/pose_landmarker.task` (9 MB) and `app/face_landmarker.task` (3.7 MB) — MediaPipe
Pose Landmarker (full, float16) and Face Landmarker, committed to the repository so
the build does not depend on the network.

## Demo page (GitHub Pages)

`docs/` is a standalone static page: MediaPipe Pose Landmarker compiled to WASM runs
in the browser, the photo never leaves the machine, and the detected pose is drawn as
a 3D skeleton with three.js. It is the same 33-point representation the service feeds
into the retargeter, minus the model and the video.
