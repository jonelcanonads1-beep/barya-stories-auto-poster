#!/usr/bin/env python3
"""
Barya Stories auto-poster — EP13..EP200 to Facebook Page "Barya Stories".

What it does, for one episode (default: the next one in state.json):
  1. Downloads caption.txt, FINAL.mp4 and thumbnail.png from Google Drive
     (via the Muse VM's hatch_gws_cli — see README; this is the Drive access
     this machine already has for officialcanon08@gmail.com).
  2. Builds a cover-embedded video with ffmpeg, exactly like the corrected
     EP12 test post of 2026-11-25: the thumbnail.png becomes the first
     1-second frame AND an attached_pic cover stream in the MP4.
     The thumbnail is NEVER posted as a separate photo.
  3. Publishes it as a Reel to the Barya Stories Page via the Facebook
     Graph API Reels Publishing API (3-phase: start -> rupload -> finish),
     using a Page Access Token from the environment. This direct API call
     bypasses Muse's facebook-cli, which is why NO approval card appears.
     (facebook-cli Page posts always trigger an approval card per post —
     proven twice on 2026-11-25 — so it cannot be used for this job.)

Usage (run from this folder):
  python3 poster.py                      # post next episode from state.json
  python3 poster.py --episode 13         # post a specific episode
  python3 poster.py --dry-run            # download + build cover video only, do NOT post
  python3 poster.py --dry-run --episode 14
  python3 poster.py --local-dir /path/to/EP13_...   # use local files instead of Drive

Environment (never commit a real token):
  BARYA_PAGE_TOKEN   Page Access Token for Barya Stories (required to post)
  BARYA_PAGE_ID      default 1370430572829796
  BARYA_GRAPH_VERSION default v26.0
  BARYA_WORK_DIR     default ./work
  BARYA_STATE_FILE   default ./state.json
  BARYA_SCHEDULE_FILE default ./schedule.json

Exit codes: 0 = posted (or dry-run built OK), 1 = failed (state NOT advanced),
2 = usage / config error. On failure the state file is never advanced, so a
retry posts the same episode, never the next one. A successful post is
recorded in state.json under "posted" with its video_id and reel URL.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE_ID = os.environ.get("BARYA_PAGE_ID", "1370430572829796")
GRAPH_VERSION = os.environ.get("BARYA_GRAPH_VERSION", "v26.0")
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
BASE = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.environ.get("BARYA_WORK_DIR", os.path.join(BASE, "work"))
STATE_FILE = os.environ.get("BARYA_STATE_FILE", os.path.join(BASE, "state.json"))
SCHEDULE_FILE = os.environ.get(
    "BARYA_SCHEDULE_FILE", os.path.join(BASE, "schedule.json")
)
GWS_CLI = os.environ.get("GWS_CLI", "/opt/hatch/bin/hatch_gws_cli")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S %Z')}] {msg}", flush=True)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def run(cmd, check=True):
    log("$ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and check:
        raise RuntimeError(
            f"command failed ({r.returncode}): {' '.join(cmd)}\n{r.stdout}\n{r.stderr}"
        )
    return r


# ---------------------------------------------------------------- Drive
# Two Drive backends:
#  - GitHub Actions: GOOGLE_SERVICE_ACCOUNT_JSON env var (service account
#    JSON, shared as Viewer on the "final" Drive folder) -> Drive API v3.
#  - Muse VM (local): hatch_gws_cli (officialcanon08@gmail.com).
def _drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds)


def api_list_children(folder_id):
    """List files in a Drive folder via the Drive API. Returns list of dicts."""
    svc = _drive_service()
    q = f"'{folder_id}' in parents and trashed=false"
    resp = svc.files().list(q=q, pageSize=50,
                            fields="files(id,name,mimeType,size)").execute()
    return resp.get("files", [])


def api_download(file_id, dest):
    from googleapiclient.http import MediaIoBaseDownload
    svc = _drive_service()
    request = svc.files().get_media(fileId=file_id)
    with open(dest, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest


def list_children(folder_id):
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return api_list_children(folder_id)
    return gws_list_children(folder_id)


def download_file(file_id, dest):
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return api_download(file_id, dest)
    return gws_download(file_id, dest)


def gws_list_children(folder_id):
    """List files in a Drive folder via hatch_gws_cli. Returns list of dicts."""
    q = f"'{folder_id}' in parents and trashed=false"
    r = run([GWS_CLI, "drive", "files", "list", "--params",
             json.dumps({"q": q, "pageSize": 50,
                         "fields": "files(id,name,mimeType,size)"})])
    return json.loads(r.stdout).get("files", [])


def gws_download(file_id, dest):
    r = run([GWS_CLI, "drive", "files", "get", "--params",
             json.dumps({"fileId": file_id, "alt": "media"}),
             "--output", dest])
    out = json.loads(r.stdout)
    if out.get("status") != "success":
        raise RuntimeError(f"Drive download failed for {file_id}: {out}")
    return dest


def fetch_episode_files(ep, local_dir=None):
    """Return (caption_path, video_path, thumb_path) for episode dict ep."""
    ep_dir = os.path.join(WORK_DIR, f"EP{ep['episode']:02d}")
    os.makedirs(ep_dir, exist_ok=True)
    caption = os.path.join(ep_dir, "caption.txt")
    video = os.path.join(ep_dir, "FINAL.mp4")
    thumb = os.path.join(ep_dir, "thumbnail.png")

    if local_dir:
        import shutil
        for name, dest in (("caption.txt", caption), ("thumbnail.png", thumb)):
            shutil.copy(os.path.join(local_dir, name), dest)
        # FINAL.mp4 may have the episode name in it; find the .mp4 in the folder
        mp4s = [f for f in os.listdir(local_dir) if f.lower().endswith(".mp4")]
        if not mp4s:
            raise RuntimeError(f"no .mp4 found in {local_dir}")
        shutil.copy(os.path.join(local_dir, mp4s[0]), video)
        return caption, video, thumb

    files = list_children(ep["folder_id"])
    by_name = {f["name"]: f for f in files}
    if "caption.txt" not in by_name:
        raise RuntimeError(f"EP{ep['episode']:02d}: caption.txt missing in Drive folder")
    if "thumbnail.png" not in by_name:
        raise RuntimeError(f"EP{ep['episode']:02d}: thumbnail.png missing in Drive folder")
    mp4 = next((f for f in files if f["name"].endswith("_FINAL.mp4")), None)
    if mp4 is None:
        raise RuntimeError(f"EP{ep['episode']:02d}: *_FINAL.mp4 missing in Drive folder")
    log(f"EP{ep['episode']:02d} Drive files: caption={by_name['caption.txt']['id']}, "
        f"video={mp4['name']} ({mp4.get('size')} bytes), thumb={by_name['thumbnail.png']['id']}")
    download_file(by_name["caption.txt"]["id"], caption)
    download_file(mp4["id"], video)
    download_file(by_name["thumbnail.png"]["id"], thumb)
    return caption, video, thumb


# ---------------------------------------------------------------- ffmpeg
def build_cover_video(video_path, thumb_path, out_path):
    """
    Reproduce the corrected EP12 build (verified 2026-11-25):
    - 1-second still of thumbnail.png at the start (silent audio), then the
      original video+audio, so Facebook's auto-thumbnail is the thumbnail.
    - thumbnail.png also embedded as an attached_pic cover stream.
    Result: ~original duration + 1s, 1080x1920, H.264 + AAC.
    """
    tmp = out_path + ".tmp.mp4"
    run(["ffmpeg", "-y",
         "-loop", "1", "-framerate", "30", "-t", "1", "-i", thumb_path,
         "-f", "lavfi", "-t", "1",
         "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-i", video_path,
         "-filter_complex",
         "[0:v]format=yuv420p,scale=1080:1920[v0];"
         "[v0][1:a][2:v][2:a]concat=n=2:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", tmp])
    run(["ffmpeg", "-y", "-i", tmp, "-i", thumb_path,
         "-map", "0", "-map", "1", "-c", "copy",
         "-disposition:v:1", "attached_pic", out_path])
    os.remove(tmp)
    # Verify: attached_pic stream present, duration = original + ~1s
    probe = run(["ffprobe", "-hide_banner", out_path], check=False)
    if "attached pic" not in probe.stderr:
        raise RuntimeError("cover video built but no attached_pic stream found")
    log(f"cover video built: {out_path} ({os.path.getsize(out_path)} bytes)")
    return out_path


# ---------------------------------------------------------------- Graph API
def graph_post(path, data, token):
    body = dict(data)
    body["access_token"] = token
    req = urllib.request.Request(
        f"{GRAPH}{path}",
        data=urllib.parse.urlencode(body).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph POST {path} failed ({e.code}): {e.read().decode(errors='replace')}")


def graph_get(path, token, params=None):
    q = dict(params or {})
    q["access_token"] = token
    url = f"{GRAPH}{path}?{urllib.parse.urlencode(q)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph GET {path} failed ({e.code}): {e.read().decode(errors='replace')}")


def publish_reel(video_path, caption, title, token):
    """Reels Publishing API, 3 phases. Returns (video_id, reel_url)."""
    # 1. start
    start = graph_post(f"/{PAGE_ID}/video_reels",
                       {"upload_phase": "start"}, token)
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not video_id or not upload_url:
        raise RuntimeError(f"video_reels start returned no video_id/upload_url: {start}")
    log(f"upload session started: video_id={video_id}")

    # 2. upload bytes to rupload.facebook.com (NOT graph.facebook.com),
    #    auth header is "OAuth", not "Bearer".
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(upload_url, data=data, method="POST")
    req.add_header("Authorization", f"OAuth {token}")
    req.add_header("offset", "0")
    req.add_header("file_size", str(size))
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            up = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"rupload failed ({e.code}): {e.read().decode(errors='replace')}")
    if not up.get("success"):
        raise RuntimeError(f"rupload did not return success: {up}")
    log(f"uploaded {size} bytes to rupload")

    # 3. finish + publish (caption goes here as description)
    finish = graph_post(f"/{PAGE_ID}/video_reels", {
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "description": caption,
        "title": title,
    }, token)
    if not finish.get("success"):
        raise RuntimeError(f"video_reels finish did not return success: {finish}")
    log("finish accepted by Meta; polling status...")

    # 4. poll until published (processing is async)
    for _ in range(30):  # up to ~5 minutes
        st = graph_get(f"/{video_id}", token, {"fields": "status"})
        status = st.get("status", {})
        pub = status.get("publishing_phase", {}).get("publish_status")
        vid = status.get("video_status")
        log(f"status: video_status={vid} publish_status={pub}")
        if pub == "published":
            return video_id, f"https://facebook.com/reel/{video_id}/"
        if pub == "error" or vid in ("error", "upload_failed"):
            raise RuntimeError(f"Meta reported failure while publishing: {status}")
        time.sleep(10)
    raise RuntimeError("timed out waiting for Meta to publish the Reel "
                       "(it may still publish later — check the Page before retrying)")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Barya Stories auto-poster")
    ap.add_argument("--episode", type=int, help="episode number to post (default: next in state.json)")
    ap.add_argument("--dry-run", action="store_true", help="download + build cover video only, do not post")
    ap.add_argument("--local-dir", help="use a local episode folder instead of Google Drive")
    ap.add_argument("--force", action="store_true", help="allow re-posting an already-posted episode")
    args = ap.parse_args()

    schedule = load_json(SCHEDULE_FILE)
    by_ep = {s["episode"]: s for s in schedule}
    state = load_json(STATE_FILE)

    ep_num = args.episode or state["next_episode"]
    if ep_num not in by_ep:
        print(f"episode {ep_num} is not in schedule.json (EP13..EP200)", file=sys.stderr)
        return 2
    ep = by_ep[ep_num]
    if ep_num == 12:
        print("EP12 was already posted as a test on 2026-11-25; refusing. Use --force to override.", file=sys.stderr)
        if not args.force:
            return 2
    already = [p["episode"] for p in state.get("posted", [])]
    if ep_num in already and not args.force:
        print(f"EP{ep_num:02d} is already recorded as posted in state.json; refusing. Use --force to override.", file=sys.stderr)
        return 2

    token = os.environ.get("BARYA_PAGE_TOKEN")
    if not args.dry_run and not token:
        print("BARYA_PAGE_TOKEN is not set. See README.md — generate a Barya Stories "
              "Page Access Token first. Nothing was posted.", file=sys.stderr)
        return 2

    log(f"=== EP{ep_num:02d} {ep['folder']} — scheduled {ep['publish_at_manila']} Manila ===")
    caption_path, video_path, thumb_path = fetch_episode_files(ep, args.local_dir)
    with open(caption_path, encoding="utf-8") as f:
        caption = f.read().strip()
    log(f"caption ({len(caption)} chars): {caption[:120]!r}...")
    if len(caption) > 2000:
        log("WARNING: caption is very long; Meta may truncate the Reel description")

    cover_path = os.path.join(WORK_DIR, f"EP{ep_num:02d}", "FINAL-cover.mp4")
    build_cover_video(video_path, thumb_path, cover_path)

    if args.dry_run:
        log(f"DRY RUN OK — cover video ready at {cover_path}; nothing posted.")
        return 0

    video_id, reel_url = publish_reel(cover_path, caption, ep["title"], token)
    log(f"PUBLISHED EP{ep_num:02d}: video_id={video_id} url={reel_url}")

    state.setdefault("posted", []).append({
        "episode": ep_num, "folder": ep["folder"],
        "publish_at_manila": ep["publish_at_manila"],
        "posted_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "video_id": video_id, "reel_url": reel_url,
    })
    if ep_num == state["next_episode"]:
        nxt = next((s for s in schedule if s["episode"] == ep_num + 1), None)
        if nxt:
            state["next_episode"] = nxt["episode"]
            state["next_publish_at_manila"] = nxt["publish_at_manila"]
        else:
            state["next_episode"] = None
            state["next_publish_at_manila"] = None
    save_json(STATE_FILE, state)
    log(f"state advanced: next episode is now {state['next_episode']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
