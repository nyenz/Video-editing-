# Nyenz Edit (EditForge)

A free video editor that runs on your own computer. You write a few simple lines ("keep 0:10-0:40", "speed 2 from 15 to 20", "loudnorm") and
EditForge makes the video. No accounts, no cloud, no tracking, nothing is sent anywhere.

**Version 1.5.0.** Read SELF_REVIEW.md for what was and was not tested.

## Install on Windows (one file, like any program)
1. Open https://github.com/nyenz/Video-editing-/releases/latest and download **EditForge-Setup.exe**.
2. Double-click it. If Windows says "Windows protected your PC", click **More info**, then **Run anyway** (the app is free and not signed).
3. Click **Next** until it is done. EditForge is now on your desktop and in the Start menu, with its own icon.

Everything is inside: Python, FFmpeg, face tracking and automatic captions. Nothing else to install.
EditForge opens in its own window. Close the window to stop it (a video still being made is finished first).

**Updates install themselves.** About once a day the app looks for a new version. When there is one, a small **Update available**
button shows at the top. Click it, then **Update now**: it downloads, checks the file, waits until no video is being made, then the app
closes, updates and opens again. Your projects and videos stay. (No internet: nothing happens.)

**Mac and Linux:** install Python 3.10+ and FFmpeg (`brew install ffmpeg` or `sudo apt install ffmpeg`), then run `start.command` or `bash start.sh`.

## Home
The first screen. One big **Create project** button: choose a video and it opens in the Editor, already cut into pieces.
Below it: **Clips**, **Editor** and **Combine**, then your **projects** (picture, name, `size | length`, newest opened first).
Search, grid or list, and a **Trash** you can undo (**Empty trash** deletes for good). On a project's **...** menu: Open, Rename,
Duplicate, Move to trash. A project whose video was moved or deleted shows **Video not found**: click **Find it again**.

**Big videos are not copied.** "Choose file" opens the normal Windows file window and uses the video where it is on your disk.
(Dropping a file on the page still sends a copy; that copy is deleted when its project is deleted.) **Storage** (bottom left) shows
the space the app uses and has **Free up** buttons; light copies and pictures not used for a month are removed by themselves.

## The pages
The bar at the top has **Clips**, **Editor**, **Combine** and **Advanced**. **Exports** (top right) lists the videos you made:
watch them, **Save** them, or open them in the Editor. The length of what you are making is always shown.

### Clips: cut parts out of a long video
1. Press **Choose file** on the left (or pick one you added before).
2. Play it. Press **Start here** and **End here** (or the keys **I** and **O**), or simply drag across the strip in the timeline.
   The length shows while you mark. Drag the yellow edges to change it.
3. Press **Add clip** (or **Enter**). Repeat. The list on the right shows each clip's length and the total.
4. Tick clips to use only those. **Join** makes one video of them (the length shows on the button); **Separate** makes one video each.

Films with Dolby or DTS sound (often .mkv) play silent or black in a browser. EditForge sees this and first makes a quick copy
(picture as it is, normal sound; seconds, not minutes), then a light copy for smooth jumping. Your clips are always cut from the original, so quality is not lost.

### Editor: pieces on a timeline, like CapCut
1. Choose a video and a piece length (for example 2 seconds). It opens already cut into pieces.
2. **Pattern** (left): "in every group of 3, number 1". **Remove these** or **Keep only these**. The timeline and the viewer change at
   once, no waiting. The new length shows at the top.
3. Work in the timeline: **play** (Space), **click** a piece to select it, **Ctrl+click** or drag across empty space to select several,
   **drag** pieces to move them, **drag a piece's edge** to make it longer or shorter (the tooltip shows the piece and video length),
   **S** splits at the red playhead, **Delete** removes, **Ctrl+Z** undoes. Removed pieces can be put back (right side).
4. The right side shows the selected pieces: speed, loudness, mute, start and end. **Effects, Colour, Zoom and Follow** are folded away
   until you open them.
5. **Steps** (top of Cut and Pattern): press **Pin** to lock the result (with your hand edits) and start step 2 from it: cut it again
   (for example 3 s pieces) and use another pattern. Nothing is rendered between steps, so every piece is still a part of the
   ORIGINAL video: dragging an edge always reaches the real footage, also footage an earlier step removed (it stops at the next piece).
   **All steps** shows each step's pattern and length; click one to look at it (older steps are locked), **Undo step**, and
   **Save steps** to repeat them on another video (from Saved patterns).
6. **Finish** (left, below the line): **Audio** (music, beat cuts), **Sound** (remove noise, remove voices or keep only voices,
   clearer voices, balance quiet and loud parts, even loudness, bass and treble), **Format** (tall 9:16, square, transitions, fades),
   **Text** (words, captions).
7. **Export** (top right): see the length and size, then **Quick preview** or **Export**.

The viewer plays your edit live from a light copy; the export is made from the original, exact to the frame.

### Combine two videos
Choose two videos and a layout: one after the other, side by side, top and bottom, small one in a corner, or one over the other.
The finished length and picture size show before you press **Combine**.

### Advanced: type your edit
Pick a video, pick an example (or type), press **Show the plan**, then **Make the video**.

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
`pip install faster-whisper` (automatic captions; downloads a model file once, then works offline) - `pip install "opencv-python-headless<5"` (following faces and things, face-following re-frame). Run `editforge features` to see what is on.

## Real limitations
* Memory stays flat with the number of cuts and was measured under 300 MB up to 1080p; 4K needs more.
* Scene detection follows brightness only. Downbeat and active-speaker detection are heuristics and can be wrong.
* Automatic Whisper captions, hardware encoders, the Windows/Mac launchers and 4-hour sources were not tested by the author (see SELF_REVIEW.md).
* "Follow a thing" matches the picture inside your box from frame to frame. It loses things that change shape a lot, leave the picture or get covered; the view then waits where it last saw them.
* Slow motion repeats frames (no smoothing). Zoom can shimmer slightly at slow speeds.

Safety of the web page: it only listens on this computer (127.0.0.1), needs a random per-run key, checks the Host/Origin headers, never inserts your text as HTML, and keeps uploads in their own folder.

License: MIT.
