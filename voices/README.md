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
| `phrases` | wording variety for directions, e.g. `{"Turn left": ["Turn left", "Take a left"]}` (one is picked per instruction, the same one each time it comes up, so it can be spoken ahead) |
| `espeak` | `{voice, speed, pitch}`: used with `engine: espeak`, and while a Piper model is still downloading |
| `lines` | what it says: `greet` (map opens), `route_started`, `rerouting`, `arrived` (a line or a list to pick from), `in_dist` (`{dist}` and `{text}` = the turn), `sample` |
| `clips` | your own recordings instead of a spoken line: `{"arrived": ["arrived1.wav", "arrived2.wav"], ...}` (WAV files in the pack folder). Turn directions are always spoken by the voice. |
| `car_eq` | `true` (default): the line is tuned for a car (rumble below ~110 Hz cut, consonants lifted around 3 kHz, gentle compression, every line at the same loudness); `false` leaves Piper's sound as it is |
| `presence_db` | how much the consonant range is lifted (default 3) |

How a voice stays fluid: the next few turns are spoken ahead of time in the background (the map sends
them as soon as a turn is passed), so a prompt starts the moment it's due instead of after a pause while
it's worked out; heads-ups use a set distance per road ("In half a mile" on highways, "In 1000 feet" in
town) so they're the same words every time; Piper's own silences between sentences are trimmed and
replaced by the pack's `pause`; a sign's long list of places is cut to the first two.

Every voice reads directions the way a person says them (half a mile, North Main Street, Interstate 45,
two close turns in one breath: bin/hu_speech.py), and lowers the music while it talks (DUCK_AUDIO=1 in
headunit.conf, the default).

Only use recordings you made yourself or have the rights to.

**Hearing a voice the way it sounds in the car:** Settings › Driving › Voice › *Test drive* plays a short
drive's worth of directions back to back (route set, a heads-up and its turn, two quick turns in one
breath, a highway exit, rerouting, arrival).

**Tuning:** `noise_scale` / `noise_w` close to the values the Piper voices were trained with (0.667 / 0.8)
keep the rhythm steady; much higher makes it wobble. Lines read best short, with commas for breaths and
no colons (Piper makes an odd stop at a colon).

**Common lines made once.** For the chosen Piper voice, the head unit makes its greetings / rerouting /
arrived lines and the usual directions without a street name (turn left, keep right at the fork, take
the exit, roundabouts, your destination is on the right...), each at the turn and as a heads-up ("In
half a mile, ..."), once in the background after the model arrives (about 85 lines; Settings shows
"getting its common lines ready") and keeps them, so they play instantly every time. The prompts with
street names are made a few turns ahead during a route.

Built-in Piper voices and their data: Kristin, John (LibriVox recordings, public domain), Joe (CC0),
Northern English Male (OpenSLR 83, CC BY-SA 4.0). Piper: https://github.com/OHF-Voice/piper1-gpl (GPL-3.0).
