# Voice packs

Each folder is one voice for spoken directions (Settings › Driving › Voice). Built-in packs live here;
your own go on the head unit in `/var/lib/headunit/voices/packs/<name>/`, or on a USB stick in a
`HEADUNIT_VOICES/<name>/` folder (copied in automatically when you plug it in).

`voice.json`:

| key | meaning |
|---|---|
| `name`, `description` | shown in Settings |
| `language` | `en` (the pack's lines are English); other head-unit languages use the Standard voice |
| `engine` | `piper` (natural offline voice, model downloaded on Wi-Fi) or `espeak` (built-in robot voice) |
| `model` | Piper voice name, e.g. `en_US-joe-medium` (any voice from the Piper voices list) |
| `length_scale` | speaking pace for Piper: below 1 faster, above 1 slower |
| `noise_scale`, `noise_w` | how lively the voice sounds and how much its rhythm varies (Piper; about 0.6–0.9) |
| `pause` | seconds of breath between sentences (e.g. 0.25) |
| `phrases` | wording variety for directions, e.g. `{"Turn left": ["Turn left", "Take a left"]}` (one is picked each time) |
| `espeak` | `{voice, speed, pitch}`: used with `engine: espeak`, and while a Piper model is still downloading |
| `lines` | what it says: `greet` (map opens), `route_started`, `rerouting`, `arrived` (a line or a list to pick from), `in_dist` (`{dist}` and `{text}` = the turn), `sample` |
| `clips` | your own recordings instead of a spoken line: `{"arrived": ["arrived1.wav", "arrived2.wav"], ...}` (WAV files in the pack folder). Turn directions are always spoken by the voice. |

Every voice reads directions the way a person says them (half a mile, North Main Street, Interstate 45,
two close turns in one breath: bin/hu_speech.py), and lowers the music while it talks (DUCK_AUDIO=1 in
headunit.conf, the default).

Only use recordings you made yourself or have the rights to.

Built-in Piper voices and their data: Kristin, John (LibriVox recordings, public domain), Joe (CC0),
Northern English Male (OpenSLR 83, CC BY-SA 4.0). Piper: https://github.com/OHF-Voice/piper1-gpl (GPL-3.0).
