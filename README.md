# Barya Stories — automatic poster (GitHub Actions)

Posts episodes EP13 to EP200 from Google Drive to the Facebook Page
**Barya Stories** (Page ID `1370430572829796`) five times a day at
**08:00, 11:00, 14:00, 18:00 and 21:00 Asia/Manila**, as Reels, with the
thumbnail as the video cover (never as a separate photo).

## Why GitHub Actions, not Muse crons

A Muse cron running the poster still triggers a Muse approval card on
every run, because Muse guards shell commands that send data to
Facebook. Proven Nov 26, 2026: EP13 at 1:44am only posted after Jonel
tapped Allow, and EP14 at 1:51am sat waiting on the same card.
GitHub Actions runs the same script on GitHub's servers, outside Muse,
so **no approval card appears at all**.

## How it works

`poster.py` (same script as the Muse version, plus a Drive API backend):
1. Downloads `caption.txt`, `*_FINAL.mp4` and `thumbnail.png` for the
   next episode from Google Drive, using a Google **service account**
   (read-only, shared as Viewer on the `3D Video Creations > outbox >
   final` folder).
2. Builds a cover video with ffmpeg: thumbnail as the first 1-second
   frame + attached_pic cover stream.
3. Publishes it as a Reel via the Facebook Graph API Reels Publishing
   API, using the Barya Stories Page Access Token.
4. Advances `state.json` and commits it back to this repo, so the next
   run posts the next episode.

## Secrets (Settings > Secrets and variables > Actions)

| Secret | What it is |
|---|---|
| `BARYA_PAGE_TOKEN` | Barya Stories Page Access Token (never expires). |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON key of the service account, read-only Drive access. |

## Schedule

`post.yml` runs at 00:00, 03:00, 06:00, 10:00, 13:00 UTC
= 08:00, 11:00, 14:00, 18:00, 21:00 Manila.
GitHub scheduled runs can start a few minutes late; that is normal.

Manual run: Actions tab > Barya Stories auto-post > Run workflow.
Optionally pass an episode number, or tick dry-run to test without posting.
