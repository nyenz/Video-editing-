# EditForge

A free video editor that runs on your own computer. You write a few simple lines ("keep 0:10-0:40", "speed 2 from 15 to 20", "loudnorm") and
EditForge makes the video. No accounts, no cloud, no tracking, nothing is sent anywhere.

**Status: version 1.0.0, released early. Read SELF_REVIEW.md for what was and was not tested.**

## Install (about 5 minutes)
1. Install **Python 3.10 or newer** (https://www.python.org/downloads/ - tick "Add python.exe to PATH") and **FFmpeg** (Windows: `winget install Gyan.FFmpeg`; Mac: `brew install ffmpeg`; Linux: `sudo apt install ffmpeg`).
2. Unpack the folder. If you got the single text file, run: `python tools/extract_repo.py EditForge_v1_0_COMPLETE_REPO_SOURCE.txt` and open the `EditForge` folder.
3. **Windows:** double-click `start.bat`. **Mac:** double-click `start.command`. **Linux:** `bash start.sh`.
   The first start makes a private Python environment and installs two small packages (needs internet once). Your browser opens the EditForge page.

The launchers check for FFmpeg and tell you how to install it if it is missing.

## Your first clips (no typing)
The page that opens is the **Clip workshop**:
1. **Choose a video.** Drop it on the page, or click "choose a file".
2. **Find the part you want.** Play the video. Press **Set START here** where the part begins and **Set END here** where it ends, then **+ Add clip**. Repeat for every part you want.
3. **Make the clips.** Choose "One video per clip" or "Join all clips into one video", then press **Make the clips**.
4. **Finished videos** appear at the bottom. Press **Watch** to check one, **Save to my computer** to keep it, or **Edit this video** to cut it again.

Use Google Chrome or Microsoft Edge. If a video will not play, EditForge makes a light copy for watching; your clips are still cut from the original, so quality is not lost.
For a very big video, copy it into the EditForge media folder (the page shows where) instead of dropping it on the page.

## The Editor: pieces, patterns and edits
Click **Editor** at the top (or press **Slice and edit this** on any video).
1. **Cut the video into pieces.** Type the seconds for each piece (for example 2) and press **Cut into pieces**. If you chose music in step 4, **Cut on the beat** cuts on every 4th beat instead.
2. **Pick pieces.** Click a piece to pick it. Shift-click picks everything in between. For a pattern, set "In every group of 3", tick the numbers you want (1, 2, 3) and press **Pick by pattern**.
3. **Change the picked pieces.** Remove, flip, mirror, speed, backwards, zoom, colour, sound, make longer or shorter, move, copy. You can mix patterns and hand edits in any order. **Undo** takes back the last change.
4. **The whole video.** Shape (for example tall 9:16 for TikTok), what happens between pieces, music, fades and quality.
5. **Make the video.** **Quick preview** makes a small fast version to check. **Make the video** makes the full-quality one.

**Save this pattern** keeps the pattern together with the edits of the first picked piece. **Use** applies it to any project.

## Combine two videos
Click **Combine** at the top. Choose two videos and how to put them together: one after the other, side by side, top and bottom, a small one in a corner, or one over the other.

## Your first scripted edit
Click **Advanced (type a script)** at the top. Drop a video in step 1, pick an example in step 2 (or type), press **Show the plan**, then **Make the video**. Download it when the bar reaches 100%.

Or in a terminal:
```
editforge edit -e "keep 0:10-0:40\nloudnorm" -i my_video.mp4 -o result.mp4
```
(`python -m editforge ...` works too.) Use `--dry-run` or `editforge plan` first to see the length and the cuts without making anything.

## What the instructions do (copy and paste)
Times may be written `12.5`, `12.5s`, `1:30`, `00:01:30`, `00:01:30.500`, `00:00:01:12` or `f120` (frame 120). Cuts are frame-accurate.

| you write | what happens |
|---|---|
| `keep 0:10-0:40, 1:00-1:30` | keep only these parts |
| `cut 0:20-0:25` | remove this part |
| `pattern_keep 2 every 10` / `pattern_remove 1 every 5` | keep/remove a piece of every cycle |
| `silence_remove threshold=-35 min_silence=0.5 padding=0.1` | cut silent parts |
| `scene_cut` / `scene_snap tolerance=0.5` | split at scene changes / move cuts onto them |
| `beat_cut every=4 take=0.5` / `beat_snap` / `beat_effect flash on=downbeat` | cut on the beat / align cuts to your music / flash on beats |
| `speed 2 from 10 to 20`, `speed_ramp 1 to 3 from 5 to 8`, `reverse from 5 to 8`, `freeze at=4.5 duration=2` | speed, ramps, backwards, hold a frame |
| `zoom 1.5 from 5 to 10`, `pan from_x=0.2 to_x=0.8`, `crop 9:16`, `reframe 9:16 mode=face` | camera moves, cropping, re-framing |
| `color contrast=1.2`, `mirror`, `hflip`, `flip`, `invert`, `grayscale`, `blur`, `sharpen`, `every_nth 2 effect=mirror` | looks (also on every Nth segment) |
| `transition crossfade 0.5` (or `wipeleft`, `slideright`, `fadeblack` ...) | blend between clips |
| `loudnorm -16`, `denoise 12`, `music "song.mp3" volume=0.25 duck=true`, `fade in=1 out=2`, `volume -6dB`, `mute` | sound |
| `text "Hello" start=1 end=4 position=bottom box=true`, `image "logo.png" position=top_right` | text and logo |
| `captions transcript="talk.srt" style=karaoke` (or automatic with Whisper) | captions, burned in or as a track, plus .srt and .ass files |
| `preset shorts`, `size 1080x1920 fit=blur`, `fps 30`, `quality high` | output style |

Put words in quotes: `text "Hello world"`. The same instructions can be written in YAML or JSON (see `examples/`). Full list: COMMANDS.md, or run `editforge commands`.
If you mistype something EditForge says so, shows the line number and suggests the right word ("did you mean ...?").

Presets: `original`, `youtube_1080p`, `youtube_4k`, `shorts` (also reels, tiktok), `instagram_square`, `preview_480p` (also `--preview`), `podcast_audio`, `podcast_mp3`, `gif`. Run `editforge presets`.

## Fast cuts or exact cuts?
Exact (default): frame-accurate, the picture is re-encoded. `--fast-cuts`: no re-encoding, very fast, but each cut moves to the nearest keyframe, so the result can start earlier and end later than you asked, and it cannot be combined with picture changes.

## Optional add-ons (EditForge works without them and says what is switched off)
`pip install faster-whisper` (automatic captions; downloads a model file once, then works offline) - `pip install "opencv-python-headless<5"` (face-following re-frame). Run `editforge features` to see what is on.

## Real limitations
* Memory stays flat with the number of cuts and was measured under 300 MB up to 1080p; 4K needs more.
* Scene detection follows brightness only. Downbeat and active-speaker detection are heuristics and can be wrong.
* Automatic Whisper captions, hardware encoders, the Windows/Mac launchers and 4-hour sources were not tested by the author (see SELF_REVIEW.md).
* Slow motion repeats frames (no smoothing). Zoom can shimmer slightly at slow speeds.

Safety of the web page: it only listens on this computer (127.0.0.1), needs a random per-run key, checks the Host/Origin headers, never inserts your text as HTML, and keeps uploads in their own folder.

License: MIT.
