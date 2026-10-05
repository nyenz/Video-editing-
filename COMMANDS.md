# EditForge command reference

Generated from the instruction table in the code, so it always matches what the program accepts.

Times can be written as `12.5`, `12.5s`, `1:30`, `00:01:30`, `00:01:30.500`, `00:00:01:12` (HH:MM:SS:FF) or `f120` (frame 120).
Times in `keep`, `cut`, `speed`... are positions in the ORIGINAL video. Times in `text`, `image`, `music`, `fade` are positions in the FINISHED video (for `text` and `image` add `time=source` to use original-video times).

Any option can be written `name=value` or, in the line language, as `name value` (for example `from 10 to 20`). The same instructions work in the line, YAML and JSON languages.


## setup

### `input`
Choose the video or audio file to edit.

Also accepted: `source`, `open`, `load`

Example: `input "my video.mp4"`

| option | type | default | also called |
|---|---|---|---|
| `file` | file | required | path, video, source, src, media |

### `output`
Choose the name of the finished file.

Also accepted: `save`, `save_to`, `out_file`

Example: `output result.mp4`

| option | type | default | also called |
|---|---|---|---|
| `file` | file | required | path, save_as, name, to_file |

### `preset`
Pick a ready-made output style (see 'editforge presets').

Also accepted: `output_preset`, `profile`

Example: `preset shorts`

| option | type | default | also called |
|---|---|---|---|
| `name` | str | required | preset, profile, style |

### `size`
Set the picture size and how the video fits into it.

Also accepted: `resolution`, `dimensions`

Example: `size 1080x1920 fit=blur`

| option | type | default | also called |
|---|---|---|---|
| `size` | size | required | resolution, dimensions, wh |
| `fit` | enum (pad, crop, stretch, blur) | pad | mode, fill |

### `fps`
Set the frame rate of the finished video.

Also accepted: `frame_rate`, `framerate`

Example: `fps 30`

| option | type | default | also called |
|---|---|---|---|
| `fps` | num | required | rate, frame_rate, framerate |

### `quality`
Set the picture quality: low, medium, high, max or a CRF number.

Also accepted: `crf`

Example: `quality high`

| option | type | default | also called |
|---|---|---|---|
| `level` | quality | required | crf, value |

### `fast_cuts`
Cut on keyframes without re-encoding: very fast, but cuts are not frame-exact.

Also accepted: `fast_cut`, `keyframe_cuts`, `copy_cuts`

Example: `fast_cuts on`

| option | type | default | also called |
|---|---|---|---|
| `enabled` | bool | True | on, value |


## select

### `keep`
Keep only these parts of the video (everything else is dropped).

Also accepted: `include`, `only`, `keep_range`, `select`

Example: `keep 0:10-0:40, 1:00-1:30`

| option | type | default | also called |
|---|---|---|---|
| `ranges` | ranges |  | range, ranges, parts |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `cut`
Remove these parts of the video.

Also accepted: `remove`, `delete`, `drop`, `exclude`, `cut_out`, `trim_out`, `cut_range`

Example: `cut 0:20-0:25`

| option | type | default | also called |
|---|---|---|---|
| `ranges` | ranges |  | range, parts |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `pattern_keep`
Keep a short piece out of every cycle (for example 2 s out of every 10 s).

Also accepted: `keep_pattern`, `pattern_include`

Example: `pattern_keep 2 every 10`

| option | type | default | also called |
|---|---|---|---|
| `take` | dur | required | keep, length, duration, on, play |
| `every` | dur | required | cycle, period, interval |
| `offset` | dur | 0 | shift, delay, phase |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `pattern_remove`
Remove a short piece out of every cycle (for example 1 s out of every 5 s).

Also accepted: `pattern_cut`, `remove_pattern`, `cut_pattern`, `pattern_delete`, `pattern_drop`

Example: `pattern_remove 1 every 5`

| option | type | default | also called |
|---|---|---|---|
| `take` | dur | required | remove, cut, length, duration, off |
| `every` | dur | required | cycle, period, interval |
| `offset` | dur | 0 | shift, delay, phase |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `silence_remove`
Cut out silent parts (with a little padding so speech is not clipped).

Also accepted: `silence_removal`, `remove_silence`, `cut_silence`, `silence_cut`, `silence_remover`, `trim_silence`, `skip_silence`, `silenceremove`

Example: `silence_remove threshold=-35 min_silence=0.5 padding=0.1`

| option | type | default | also called |
|---|---|---|---|
| `threshold` | db | -35.0 | noise, level, db, thresh |
| `min_silence` | dur | 0.5 | min, min_duration, minimum, min_length, duration, longer_than |
| `padding` | dur | 0.1 | pad, keep_padding, margin |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `scene_cut`
Split the video at scene changes (and optionally keep or drop chosen scenes).

Also accepted: `scene_cuts`, `split_scenes`, `scene_split`, `scenes`

Example: `scene_cut threshold=0.3 keep=1,3-4`

| option | type | default | also called |
|---|---|---|---|
| `threshold` | num | 0.3 | score, sensitivity |
| `min_scene` | dur | 0.8 | min, min_length, min_duration |
| `keep` | intlist |  | keep_scenes, only |
| `remove` | intlist |  | cut, drop, skip, remove_scenes |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `scene_snap`
Move cut points onto the nearest scene change.

Also accepted: `snap_to_scenes`, `snap_scenes`, `snap_cuts_to_scenes`

Example: `scene_snap tolerance=0.5`

| option | type | default | also called |
|---|---|---|---|
| `tolerance` | dur | 0.5 | within, distance, max_shift |
| `threshold` | num | 0.3 | score, sensitivity |

### `beat_cut`
Cut the video on the beat of its own sound (or a fixed tempo).

Also accepted: `beat_sync`, `beat_cuts`, `cut_on_beats`, `cut_to_beat`, `beat_split`

Example: `beat_cut every=4 take=0.5`

| option | type | default | also called |
|---|---|---|---|
| `every` | int | 4 | beats, per, n |
| `take` | num | 1.0 | keep, fraction, portion |
| `source` | enum (audio, bpm) | audio | use |
| `bpm` | num |  | tempo |
| `offset` | dur | 0 | shift, delay |
| `downbeats` | bool | False | on_downbeats, bars |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `beat_snap`
Nudge cut points so they land on the beats of your background music.

Also accepted: `snap_to_beats`, `snap_beats`, `align_to_beats`, `beat_align`

Example: `beat_snap tolerance=0.25`

| option | type | default | also called |
|---|---|---|---|
| `tolerance` | dur | 0.25 | within, distance |
| `source` | enum (music, bpm) | music | use |
| `bpm` | num |  | tempo |
| `offset` | dur | 0 | shift, delay |
| `downbeats` | bool | False | on_downbeats, bars |

### `beat_effect`
Trigger a short effect on every beat (or every downbeat).

Also accepted: `beat_fx`, `on_beat`, `beats_effect`, `beat_flash`

Example: `beat_effect flash on=downbeat`

| option | type | default | also called |
|---|---|---|---|
| `effect` | enum (flash, zoom_pulse, invert, mirror, grayscale, blur, sharpen, shake, contrast) | flash | name, fx, type |
| `on` | enum (beat, downbeat) | beat | trigger, at |
| `every` | int | 1 | nth |
| `length` | dur | 0.12 | hold, duration, pulse_length |
| `intensity` | num | 0.5 | strength, amount |
| `source` | enum (audio, music, bpm) | audio | use |
| `bpm` | num |  | tempo |
| `offset` | dur | 0 | shift, delay |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |


## clip

### `speed`
Play faster (2) or slower (0.5).

Also accepted: `speedup`, `speed_up`, `slowmo`, `slow_motion`, `slow_down`, `slowdown`

Example: `speed 2 from 10 to 20`

| option | type | default | also called |
|---|---|---|---|
| `factor` | factor | required | rate, x, multiplier, speed, amount, times |
| `pitch` | bool | True | keep_pitch, preserve_pitch |
| `mute` | bool | False | silent, mute_audio |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `speed_ramp`
Gradually change speed from one value to another.

Also accepted: `ramp`, `speedramp`, `speed_curve`, `ramp_speed`

Example: `speed_ramp 1 to 3 from 5 to 8`

| option | type | default | also called |
|---|---|---|---|
| `from_speed` | factor | required | start_speed, speed_from, initial, s0 |
| `to_speed` | factor | required | end_speed, speed_to, final, s1 |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |
| `easing` | enum (linear, ease_in, ease_out, ease_in_out) | ease_in_out | ease, curve |
| `steps` | int | 0 | pieces |
| `mute` | bool | False | silent |

### `reverse`
Play a part backwards.

Also accepted: `rewind`, `backwards`, `backward`

Example: `reverse from 5 to 8`

| option | type | default | also called |
|---|---|---|---|
| `mute` | bool | False | silent |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `freeze`
Hold one frame still for a while.

Also accepted: `freeze_frame`, `hold`, `hold_frame`, `pause`

Example: `freeze at=4.5 duration=2`

| option | type | default | also called |
|---|---|---|---|
| `at` | time | required | time, frame_at, position, when |
| `duration` | dur | 2 | for, length, hold, dur |

### `zoom`
Slowly zoom in (or out) with smooth easing.

Also accepted: `zoom_in`, `punch_in`, `camera_zoom`

Example: `zoom 1.5 from 5 to 10`

| option | type | default | also called |
|---|---|---|---|
| `scale` | num | required | zoom, amount, level, factor, end_scale, to_scale |
| `from_scale` | num | 1.0 | start_scale, scale_from, initial |
| `x` | num | 0.5 | cx, center_x, focus_x |
| `y` | num | 0.5 | cy, center_y, focus_y |
| `easing` | enum (linear, ease_in, ease_out, ease_in_out) | ease_in_out | ease, curve |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `pan`
Slide the view across the picture (zoomed in a little).

Also accepted: `camera_pan`, `pan_across`

Example: `pan from_x=0.2 to_x=0.8 scale=1.4`

| option | type | default | also called |
|---|---|---|---|
| `from_x` | num | 0.2 | x_from, x0 |
| `to_x` | num | 0.8 | x_to, x1 |
| `from_y` | num | 0.5 | y_from, y0 |
| `to_y` | num | 0.5 | y_to, y1 |
| `scale` | num | 1.3 | zoom, level |
| `easing` | enum (linear, ease_in, ease_out, ease_in_out) | ease_in_out | ease, curve |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `crop`
Cut the picture down to an aspect ratio (9:16, 1:1 ...) or a box.

Also accepted: `crop_to`, `trim_frame`

Example: `crop 9:16`

| option | type | default | also called |
|---|---|---|---|
| `aspect` | aspect |  | ratio, shape |
| `box` | box |  | rect, area |
| `position` | enum (center, left, right, top, bottom) | center | anchor, align |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `reframe`
Re-frame 16:9 video to 9:16 or 1:1, following faces when available.

Also accepted: `auto_reframe`, `autoreframe`, `smart_crop`, `reframe_face`

Example: `reframe 9:16 mode=face`

| option | type | default | also called |
|---|---|---|---|
| `aspect` | aspect | 9:16 | ratio, shape |
| `mode` | enum (auto, center, face, speaker) | auto | follow |
| `smooth` | num | 0.7 | smoothing |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `color`
Adjust brightness, contrast, saturation, gamma and hue.

Also accepted: `colour`, `color_adjust`, `colour_adjust`, `grade`, `adjust`, `color_grade`

Example: `color contrast=1.2 saturation=1.3`

| option | type | default | also called |
|---|---|---|---|
| `brightness` | num | 0.0 | light |
| `contrast` | num | 1.0 |  |
| `saturation` | num | 1.0 | sat |
| `gamma` | num | 1.0 |  |
| `hue` | num | 0.0 | tint |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `effect`
Apply a look: mirror, hflip, flip, invert, grayscale, blur, sharpen, sepia, vignette.

Also accepted: `fx`, `filter`, `video_effect`, `look`, `mirror`, `hflip`, `flip`, `vflip`, `invert`, `negate`, `grayscale`, `greyscale`, `blur`, `sharpen`, `sepia`, `vignette`

Example: `effect mirror from 5 to 10`

| option | type | default | also called |
|---|---|---|---|
| `name` | enum (mirror, hflip, flip, vflip, invert, grayscale, blur, sharpen, sepia, vignette) | required | effect, type, fx |
| `strength` | num | 0.5 | amount, level |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `every_nth`
Apply an effect to every 2nd, 3rd ... segment.

Also accepted: `every_other`, `nth`, `every_n`, `alternate`

Example: `every_nth 2 effect=mirror`

| option | type | default | also called |
|---|---|---|---|
| `n` | int | required | every, step, nth |
| `effect` | enum (mirror, hflip, flip, vflip, invert, grayscale, blur, sharpen, sepia, vignette) | required | name, fx, type |
| `strength` | num | 0.5 | amount, level |
| `offset` | int | 0 | first, skip, start_at |


## transition

### `transition`
Blend between clips (crossfade, wipe, slide ...).

Also accepted: `crossfade`, `xfade`, `join`, `transitions`, `dissolve`, `wipe`

Example: `transition fade 0.5`

| option | type | default | also called |
|---|---|---|---|
| `type` | enum | fade | style, kind, effect |
| `duration` | dur | 0.5 | dur, length, for, seconds |
| `at` | time |  | when, near, around |
| `every` | int | 1 | nth |
| `contiguous` | bool | False | include_contiguous, force |


## audio

### `loudnorm`
Make loudness match a standard (two-pass EBU R128) with a clipping guard.

Also accepted: `normalize`, `normalise`, `loudness`, `normalize_audio`, `normalise_audio`, `loudness_normalize`, `ebu_r128`, `r128`

Example: `loudnorm -16`

| option | type | default | also called |
|---|---|---|---|
| `target` | db | -16.0 | i, lufs, level, loudness |
| `true_peak` | db | -1.5 | tp, peak, ceiling |
| `lra` | num | 11.0 | range |
| `enabled` | bool | True | on |

### `music`
Add background music (it gets quieter when someone talks).

Also accepted: `background_music`, `bgm`, `add_music`, `soundtrack`

Example: `music "song.mp3" volume=0.2`

| option | type | default | also called |
|---|---|---|---|
| `file` | file | required | path, track, song |
| `volume` | gain | 0.25 | level, gain, vol |
| `duck` | bool | True | ducking, auto_duck |
| `start` | time | 0 | at, from, begin |
| `end` | time |  | to, until, stop |
| `loop` | bool | True | repeat |
| `fade_in` | dur | 1 | fadein, in_fade |
| `fade_out` | dur | 2 | fadeout, out_fade |

### `fade`
Fade the picture and sound in at the start and out at the end.

Also accepted: `fades`, `fade_in_out`, `fade_in`, `fade_out`

Example: `fade in=1 out=2`

| option | type | default | also called |
|---|---|---|---|
| `fade_in` | dur | 0 | in, fadein, start_fade |
| `fade_out` | dur | 0 | out, fadeout, end_fade |
| `audio` | bool | True | sound |
| `video` | bool | True | picture |
| `color` | enum (black, white) | black | colour |

### `denoise`
Reduce background noise (free FFmpeg filters).

Also accepted: `noise_reduction`, `noise_reduce`, `reduce_noise`, `clean_audio`, `remove_noise`, `noise_removal`

Example: `denoise 12`

| option | type | default | also called |
|---|---|---|---|
| `amount` | num | 12.0 | strength, level, nr, db |
| `highpass` | bool | True | rumble, hp |
| `enabled` | bool | True | on |

### `volume`
Make the sound louder or quieter (2, 0.5, -6dB, 150%).

Also accepted: `gain`, `audio_volume`

Example: `volume -6dB`

| option | type | default | also called |
|---|---|---|---|
| `gain` | gain | required | level, amount |
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |

### `mute`
Silence the sound (whole video, or a range).

Also accepted: `silence_audio`, `mute_audio`

Example: `mute from 3 to 5`

| option | type | default | also called |
|---|---|---|---|
| `start` | time |  | from, begin, since, in |
| `end` | time |  | to, until, stop, out |


## overlay

### `text`
Put text on the picture.

Also accepted: `overlay_text`, `caption_text`, `title`, `add_text`, `label`, `drawtext`

Example: `text "Hello" start=00:00:01 end=00:00:04 position=bottom`

| option | type | default | also called |
|---|---|---|---|
| `text` | str | required | message, words, label |
| `start` | time | 0 | from, begin, at, in |
| `end` | time |  | to, until, stop, out |
| `duration` | dur |  | for, length, dur |
| `position` | pos (top, bottom, center, top_left, top_right, bottom_left, bottom_right, left, right) | bottom | pos, align, placement |
| `x` | str |  | left_px |
| `y` | str |  | top_px |
| `size` | num | 48.0 | font_size, fontsize |
| `color` | color | white | colour, font_color, fill |
| `font` | str |  | font_file, typeface, fontfile |
| `box` | bool | False | background, bg |
| `box_color` | color | black@0.5 | box_colour, bg_color |
| `box_padding` | num | 12.0 | padding, pad |
| `border` | num | 0.0 | outline, stroke |
| `border_color` | color | black | outline_color, stroke_color |
| `shadow` | bool | False |  |
| `fade` | dur | 0 | fade_in_out |
| `time` | enum (output, source) | output | timeline, clock |

### `image`
Put a picture or logo on the video.

Also accepted: `logo`, `overlay_image`, `watermark`, `add_image`, `add_logo`, `picture`

Example: `image "logo.png" position=top_right scale=0.15`

| option | type | default | also called |
|---|---|---|---|
| `file` | file | required | path, logo, picture |
| `position` | pos (top, bottom, center, top_left, top_right, bottom_left, bottom_right, left, right) | top_right |  |
| `x` | str |  |  |
| `y` | str |  |  |
| `scale` | num | 0.15 | size, width_fraction |
| `width` | num |  | px |
| `opacity` | num | 1.0 | alpha |
| `margin` | num | 24.0 | gap |
| `start` | time | 0 | from, begin, at, in |
| `end` | time |  | to, until, stop, out |
| `duration` | dur |  | for, length, dur |
| `time` | enum (output, source) | output | timeline, clock |

### `captions`
Make captions with local Whisper (or use your own transcript) and add them.

Also accepted: `caption`, `subtitles`, `subtitle`, `auto_captions`, `autocaption`, `auto_caption`, `transcribe`, `whisper`, `add_captions`

Example: `captions style=karaoke mode=burn`

| option | type | default | also called |
|---|---|---|---|
| `mode` | enum (burn, soft, both, files) | burn | how, where, embed |
| `style` | enum (karaoke, plain, boxed) | karaoke | look |
| `language` | str | auto | lang |
| `model` | str | base | whisper_model |
| `transcript` | file |  | from_file, srt, subtitles_file, import, words |
| `words_per_line` | int | 6 | max_words |
| `max_chars` | int | 32 | chars |
| `font_size` | num | 56.0 | size |
| `color` | color | white | colour |
| `highlight` | color | yellow | highlight_color, active_color |
| `outline` | color | black | outline_color |
| `position` | pos (top, bottom, center, top_left, top_right, bottom_left, bottom_right, left, right) | bottom | pos, align |
| `margin` | num | 60.0 |  |
| `font` | str |  | font_file, typeface |
| `files` | bool | True | write_files, sidecar |
| `device` | enum (auto, cpu, cuda) | auto |  |


## Command line

```
editforge web [--host H] [--port N] [--no-open] [--media-dir FOLDER]
editforge edit SCRIPT -i VIDEO [-o OUT] [-e TEXT] [--preset P] [--size WxH] [--fps N] [--quality Q] [--preview] [--fast-cuts]
              [--transcript FILE] [--dry-run] [--overwrite] [--encoder E] [--workers N] [--max-memory MB] [--no-resume] [--keep-temp]
editforge plan SCRIPT -i VIDEO [--json]           (same options as edit; shows the plan and writes nothing)
editforge validate SCRIPT [-i VIDEO] [-e TEXT]
editforge probe FILE [--json]
editforge presets
editforge encoders [--retest]
editforge proxy FILE [-o OUT] [--height N] [--overwrite]
editforge beats FILE [--json]
editforge jobs [--limit N]
editforge commands | features | version
editforge clean [--all]
```
Add `--verbose` (or `-v`) to any command to see technical details. Exit codes: 0 fine, 1 a problem that was explained, 2 the script has mistakes, 130 cancelled.

`--fast-cuts` copies the picture without re-encoding. It is very fast, but every cut moves to the nearest keyframe (the result can start earlier and end later than asked)
and it cannot be combined with speed, effects, text, transitions, resizing or other picture changes. The default cuts are frame-accurate.
