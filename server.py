"""Interfaz web + procesado automático del canal.

    uvicorn server:app --host 0.0.0.0 --port 8000
"""
import base64
import os
import queue
import secrets
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clipper import kick, pipeline
from clipper.config import Settings
from clipper.pipeline import read_json, write_json

s = Settings()
OUT = Path(s.out)
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = os.getenv("APP_PASSWORD", "")

app = FastAPI(title="Resumidor de streams")
jobs = queue.Queue()      # procesados completos (GPU, de uno en uno)
cuts = queue.Queue()      # recortes sueltos, no esperan a los procesados
queued = set()
_kick_cache = {"at": 0, "vods": []}


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD:
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, pwd = base64.b64decode(header[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(pwd, PASSWORD)
            except Exception:
                pass
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="clips"'})
    return await call_next(request)


def kick_vods(force=False):
    if force or time.time() - _kick_cache["at"] > 60:
        try:
            _kick_cache["vods"] = kick.list_vods(s.channel)
            _kick_cache["at"] = time.time()
        except Exception as e:
            print(f"Kick: {e}")
    return _kick_cache["vods"]


def enqueue(vod):
    if vod["uuid"] in queued:
        return
    queued.add(vod["uuid"])
    workdir = OUT / vod["uuid"]
    workdir.mkdir(parents=True, exist_ok=True)
    write_json(workdir / "meta.json", {**vod, "key": vod["uuid"]})
    pipeline.set_status(workdir, "queued", "En cola")
    jobs.put(vod)


def job_worker():
    while True:
        vod = jobs.get()
        try:
            pipeline.process(vod["source"], s, vod["uuid"], vod["title"], vod)
        except Exception:
            traceback.print_exc()
        finally:
            queued.discard(vod["uuid"])


def cut_worker():
    while True:
        key, mid = cuts.get()
        workdir = OUT / key
        try:
            meta = read_json(workdir / "meta.json", {})
            local = [p for p in workdir.glob("video.*") if ".part" not in p.name]
            video = local[0] if local else meta["source"]
            transcript = read_json(workdir / "transcript.json", [])
            with pipeline.lock:
                moments = read_json(workdir / "moments.json", [])
                m = next(x for x in moments if x["id"] == mid)
                m = {**m}
            pipeline.cut_moment(workdir, m, video, transcript, s)
            _update_moment(key, mid, {"file": m["file"], "vertical": m["vertical"], "cutting": False})
        except Exception as e:
            traceback.print_exc()
            _update_moment(key, mid, {"cutting": False, "cut_error": str(e)})


def watcher():
    known_file = OUT / "known.json"
    known = read_json(known_file)
    if known is None:
        # Primera vez: los VODs viejos no se procesan solos (se pueden lanzar desde la interfaz)
        known = [v["uuid"] for v in kick_vods(force=True)]
        write_json(known_file, known)
    while True:
        if s.auto_process:
            for vod in reversed(kick_vods(force=True)):
                if vod["uuid"] in known:
                    continue
                known.append(vod["uuid"])
                write_json(known_file, known)
                if any(t in vod["title"].lower() for t in s.skip_titles):
                    continue
                enqueue(vod)
        time.sleep(s.interval)


@app.on_event("startup")
def startup():
    # Procesados que quedaron a medias por un reinicio
    for d in OUT.iterdir():
        st = read_json(d / "status.json") if d.is_dir() else None
        meta = read_json(d / "meta.json") if d.is_dir() else None
        if st and meta and st["state"] in ("queued", "processing") and meta.get("source"):
            enqueue({**meta, "uuid": d.name})
    # Restos de ejecuciones anteriores: VOD y wav pesan GB y ya no hacen falta
    for d in OUT.iterdir():
        st = read_json(d / "status.json") if d.is_dir() else None
        if st and st["state"] in ("done", "error") and d.name not in queued:
            pipeline.clean_temp(d)
    for target in (job_worker, cut_worker, watcher):
        threading.Thread(target=target, daemon=True).start()


def _update_moment(key, mid, changes):
    path = OUT / key / "moments.json"
    with pipeline.lock:
        moments = read_json(path, [])
        for m in moments:
            if m["id"] == mid:
                m.update(changes)
                write_json(path, moments)
                return m
    raise HTTPException(404, "Momento no encontrado")


# ---------- API ----------

@app.get("/api/vods")
def list_vods(refresh: bool = False):
    items = {}
    for v in kick_vods(force=refresh):
        items[v["uuid"]] = {**v, "key": v["uuid"]}
    for d in OUT.iterdir():
        if not d.is_dir():
            continue
        meta = read_json(d / "meta.json")
        if not meta:
            continue
        item = items.setdefault(d.name, {**meta, "key": d.name})
        item["status"] = read_json(d / "status.json")
        moments = read_json(d / "moments.json", [])
        item["moments"] = len(moments)
        item["approved"] = sum(m["status"] == "approved" for m in moments)
    return sorted(items.values(), key=lambda v: v.get("created_at") or "", reverse=True)


@app.post("/api/vods/{key}/process")
def process_vod(key: str):
    vod = next((v for v in kick_vods(force=True) if v["uuid"] == key), None)
    if not vod:
        meta = read_json(OUT / key / "meta.json")
        if not meta or not meta.get("source"):
            raise HTTPException(404, "VOD no encontrado")
        vod = {**meta, "uuid": key}
    enqueue(vod)
    return {"ok": True}


@app.get("/api/vods/{key}")
def vod_detail(key: str):
    d = OUT / key
    meta = read_json(d / "meta.json") or next((v for v in kick_vods() if v["uuid"] == key), None)
    if not meta:
        raise HTTPException(404)
    return {
        "meta": meta,
        "status": read_json(d / "status.json"),
        "timeline": read_json(d / "timeline.json"),
        "moments": read_json(d / "moments.json", []),
    }


@app.get("/api/vods/{key}/transcript")
def vod_transcript(key: str):
    return [{"start": x["start"], "end": x["end"], "text": x["text"]}
            for x in read_json(OUT / key / "transcript.json", [])]


class MomentPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    start: float | None = None
    end: float | None = None


@app.patch("/api/vods/{key}/moments/{mid}")
def patch_moment(key: str, mid: int, body: MomentPatch):
    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in ("pending", "approved", "discarded"):
        raise HTTPException(400, "Estado inválido")
    return _update_moment(key, mid, changes)


@app.post("/api/vods/{key}/moments/{mid}/cut")
def recut_moment(key: str, mid: int):
    m = _update_moment(key, mid, {"cutting": True, "cut_error": None})
    if m["end"] - m["start"] < 2:
        raise HTTPException(400, "Clip demasiado corto")
    cuts.put((key, mid))
    return m


class NewMoment(BaseModel):
    start: float
    end: float
    title: str = "Clip manual"


@app.post("/api/vods/{key}/moments")
def add_moment(key: str, body: NewMoment):
    path = OUT / key / "moments.json"
    if not (OUT / key / "meta.json").exists():
        raise HTTPException(404)
    with pipeline.lock:
        moments = read_json(path, [])
        m = {
            "id": max((x["id"] for x in moments), default=0) + 1, "start": body.start, "end": body.end,
            "title": body.title, "reason": "Añadido a mano", "llm": None, "audio": None, "score": 1.0,
            "status": "approved", "file": None, "vertical": None, "cutting": True,
        }
        moments.insert(0, m)
        write_json(path, moments)
    cuts.put((key, m["id"]))
    return m


def _delete_files(workdir: Path, m):
    """Borra los archivos de un momento (horizontal, vertical y subtítulos). Devuelve los bytes liberados."""
    freed = 0
    clips = workdir / "clips"
    for name in (m.get("file"), m.get("vertical")):
        if not name:
            continue
        for path in (clips / name, clips / (Path(name).stem + ".srt")):
            if path.exists():
                freed += path.stat().st_size
                path.unlink()
    m["file"] = m["vertical"] = None
    return freed


@app.delete("/api/vods/{key}/moments/{mid}/files")
def delete_moment_files(key: str, mid: int):
    workdir = OUT / key
    with pipeline.lock:
        moments = read_json(workdir / "moments.json", [])
        m = next((x for x in moments if x["id"] == mid), None)
        if not m:
            raise HTTPException(404, "Momento no encontrado")
        freed = _delete_files(workdir, m)
        write_json(workdir / "moments.json", moments)
    return {"freed_mb": round(freed / 1e6, 1), "moment": m}


@app.post("/api/vods/{key}/cleanup")
def cleanup_vod(key: str):
    """Borra los clips que no estén aprobados y los archivos sueltos que ya no usa nadie."""
    workdir = OUT / key
    clips = workdir / "clips"
    freed, count = 0, 0
    with pipeline.lock:
        moments = read_json(workdir / "moments.json", [])
        for m in moments:
            if m.get("status") != "approved" and (m.get("file") or m.get("vertical")):
                freed += _delete_files(workdir, m)
                count += 1
        write_json(workdir / "moments.json", moments)
        keep = {n for m in moments for n in (m.get("file"), m.get("vertical")) if n}
        keep |= {Path(n).stem + ".srt" for n in keep}
        for path in clips.glob("*") if clips.exists() else []:
            if path.name not in keep:
                freed += path.stat().st_size
                path.unlink()
    return {"deleted": count, "freed_mb": round(freed / 1e6, 1)}


@app.get("/api/clips")
def list_clips(status: str = "approved", key: str | None = None):
    """Clips con archivo de todos los streams (o de uno), filtrados por estado.
    Orden: stream más reciente primero y, dentro de cada stream, mejor puntuación primero."""
    items = []
    for d in OUT.iterdir():
        if not d.is_dir() or (key and d.name != key):
            continue
        meta = read_json(d / "meta.json")
        if not meta:
            continue
        for m in read_json(d / "moments.json", []):
            if m.get("status") == status and m.get("file") and not m.get("cutting"):
                items.append({**m, "key": d.name, "vod_title": meta.get("title") or d.name,
                              "created_at": meta.get("created_at")})
    items.sort(key=lambda m: m.get("score") or 0, reverse=True)
    items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return items


@app.get("/api/approved")
def list_approved():
    return list_clips("approved")


class KeysBody(BaseModel):
    keys: list[str] | None = None


@app.post("/api/cleanup-discarded")
def cleanup_discarded(body: KeysBody):
    """Borra los archivos de los clips descartados (de los streams indicados o de todos)."""
    freed, count = 0, 0
    for d in OUT.iterdir():
        if not d.is_dir() or (body.keys and d.name not in body.keys):
            continue
        path = d / "moments.json"
        with pipeline.lock:
            moments = read_json(path)
            if not moments:
                continue
            changed = False
            for m in moments:
                if m.get("status") == "discarded" and (m.get("file") or m.get("vertical")):
                    freed += _delete_files(d, m)
                    count += 1
                    changed = True
            if changed:
                write_json(path, moments)
    return {"deleted": count, "freed_mb": round(freed / 1e6, 1)}


def _persistent():
    """True si OUTPUT_DIR está en un volumen montado (sobrevive a los redeploys).
    Fuera de Docker (desarrollo local) siempre es persistente."""
    if not Path("/.dockerenv").exists():
        return True
    path = OUT.resolve()
    while True:
        if os.path.ismount(path):
            return str(path) != "/"
        if path.parent == path:
            return False
        path = path.parent


@app.get("/api/health")
def health():
    return {"persistent": _persistent(), "output_dir": str(OUT.resolve())}


@app.get("/api/queue")
def queue_info():
    return {"jobs": jobs.qsize(), "cuts": cuts.qsize(), "processing": sorted(queued)}


app.mount("/files", StaticFiles(directory=OUT), name="files")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web"), name="static")


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "web" / "index.html")
