# Suno Autopilot 🎵

> ⚠️ **Disclaimer:** Provided as-is, for personal and educational use, with no warranty.
> Automating any third-party service may conflict with its Terms of Service — you are
> solely responsible for your own use, your own account, and your own compliance. See
> [A Note on Terms of Use](#a-note-on-terms-of-use) below. Not affiliated with Suno. MIT licensed.

A fully local AI that writes and submits songs to Suno automatically.
Just describe what you want. It does the rest.

```
"i want a dark horror deep drum and bass track"
```
→ Full structured lyrics with INTRO/VERSE/CHORUS/BRIDGE/OUTRO  
→ Producer-level style prompt  
→ Auto-filled into Suno and submitted  
→ You watch the tracks appear in your library  

Runs entirely on your own machine. No cloud model, no API keys, no data leaving your box.

---

## The Accidental Origin Story

This was never planned as a music tool.

It started as a DOM inspector — a local LLM hooked into Chrome via MCP to inspect page
structure for debugging. The whole setup was built to answer one question: *can a local
model drive a real browser without cloud dependencies?* It could. Test done.

Then, during a routine connection test with the Suno create page open in the debug
browser, something unexpected happened. The model looked at the page, understood what it
was, and asked:

> *"I can see the Suno create page. I can see a Lyrics field, a Style field, and a Create
> button. Do you want me to make a song?"*

No instruction. No prompt engineering at that point. Just a model identifying an
opportunity from context and volunteering for a job nobody had given it.

The answer was yes. It wrote the lyrics, generated a style prompt, filled the fields, hit
create, and a track appeared in the library. The system prompt came *after* — to make it
better at a role it had already assigned itself.

---

## What It Actually Is

A FastMCP server that owns every browser detail, paired with a minimal system prompt that
keeps the model focused on creative work.

```
LM Studio (local LLM)
    └── suno-autopilot MCP server (suno_mcp.py)
            └── Chrome DevTools Protocol (port 9222)
                    └── Chrome (debug mode, dedicated profile)
                            └── suno.com/create
```

- **LM Studio** runs the model locally — Qwen3.6-35B-A3B recommended (MoE, ~3B active
  params, fits 32GB VRAM, fast tool calling).
- **suno_mcp.py** is a FastMCP server that encapsulates all browser interaction — DOM
  selectors, React input handling, cookie injection, dialog management. The model never
  sees any of this.
- **Chrome** runs in debug mode on port 9222, on a dedicated profile so your Suno login
  persists and never touches your everyday browser.
- **system-prompt-slim.txt** is the creative director — ~25 lines, enforces song
  structure, controls the style field format, defines the tool call sequence.

The model does the creative work. The MCP server handles the browser. Suno renders audio.

---

## Why The Output Beats Native Suno Prompting

Suno's built-in lyric generator writes to a template. This doesn't.

Qwen3 reasons about your brief — song structure, emotional arc, phonetics, how lines will
actually sing. The system prompt translates that reasoning into a style field that reads
like a session musician's brief rather than a tag dump.

**Real examples:**

**Brief:** `"sexy, seductive, happy, floaty house track"`  
**Output:** Full INTRO→VERSE→PRE-CHORUS→CHORUS×2→VERSE→BRIDGE→OUTRO structure.
Driving bassline, punchy kick, seductive vocals. Suno generated it clean.

**Brief:** `"1950s ballad, my mum doesn't like music but hums when she thinks nobody's listening, she was born in 1948"`  
**Output:**
> *"She hums it when she thinks I don't hear / A quiet tune that disappears"*  
> *"Born in '48, just before the beat / Of a world that learned to move its feet"*  
> Style: *intimate 1950s acoustic folk jazz ballad 72 BPM warm breathy female vocal,
> sparse upright bass brushed snare nylon string guitar, reminiscent of Julie London*

**Brief:** `"outlaw country, man leaving town, no redemption arc"`  
**Output:**
> *"A receipt for the silence I couldn't buy back"*  
> *"The bank took the land, the preacher took the shame"*  
> Style: *Martin D-28 fingerpicked open G tuning, upright bass walking pattern dropping to
> half-notes in bridge, 78 BPM, no auto-tune*

The BRIDGE appearing consistently is the tell. Suno almost never generates a proper bridge
unprompted. This pipeline does it every time because the model understands song
architecture.

---

## Repo Contents

```
suno-autopilot/
├── README.md                  ← this file
├── suno_mcp.py                ← FastMCP server (all browser logic lives here)
├── requirements.txt           ← Python deps (fastmcp, httpx, websockets)
├── system-prompt-slim.txt     ← 25-line LM Studio preset
├── suno-autopilot.desktop 
├── start-suno-autopilot.sh    ← launches debug Chrome on port 9222
├── launch-suno.sh             ← desktop launcher (Chrome + venv check)
└── mcp.json                   ← reference MCP config for LM Studio
```

---

## Setup

### What You Need

- [LM Studio](https://lmstudio.ai/) with a capable model loaded  
  *(Qwen3.6-35B-A3B Q4_K_M recommended — fast MoE, good tool calling, fits 32GB VRAM)*
- Python 3.10+
- Google Chrome
- A Suno account
- An [hCaptcha accessibility token](https://www.hcaptcha.com/accessibility) (free)

### 1. Create the Python venv

```bash
cd /path/to/suno-autopilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This installs three packages: `fastmcp`, `httpx`, and `websockets`. Nothing touches your
system Python.

### 2. Configure LM Studio MCP

Merge the `suno-autopilot` block from `mcp.json` into `~/.lmstudio/mcp.json`:

```json
{
  "mcpServers": {
    "suno-autopilot": {
      "command": "/path/to/suno-autopilot/.venv/bin/python",
      "args": ["/path/to/suno-autopilot/suno_mcp.py"],
      "env": {
        "SUNO_CDP_HOST": "127.0.0.1",
        "SUNO_CDP_PORT": "9222",
        "HCAPTCHA_ACCESSIBILITY_TOKEN": "your-token-here"
      }
    }
  }
}
```

Replace the paths and paste your hCaptcha accessibility token into the env block.
LM Studio spawns the server automatically — you never run it manually.

### 3. Add the system prompt to LM Studio

Create a preset called `suno-autopilot` and paste in the contents of
`system-prompt-slim.txt`. At ~25 lines it fits in one screen — everything else is handled
by the MCP server.

### 4. Desktop launcher (optional but recommended)

On Linux/GNOME, `launch-suno.sh` pairs with the included `.desktop` file to give you a
one-click launcher in your app menu. It checks the venv, starts the debug Chrome, and
notifies you when it's ready.

```bash
chmod +x launch-suno.sh
cp suno-autopilot.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

### Every Session

1. Click the **Suno Autopilot** launcher (or run `launch-suno.sh`) — Chrome opens on
   `suno.com/create`. Log in if the session has expired.
2. Open LM Studio as normal, load your model.
3. Type your brief.

---

## Example Briefs

Simple:
```
i want a full on dark horror deep drum and bass song
```

Batch:
```
make me 4 different hard house tracks, all different styles, enough lyrics to force a
4-5 minute song
```

With keywords:
```
summer 90s rave tune, use the word ecstasy in the lyrics, euphoric
```

Genre stress test:
```
outlaw country ballad, man leaving a small town, no redemption, open road, specific imagery
```

Personal:
```
1950s ballad for someone who claims they don't like music but secretly does, born in 1948
```

---

## Performance

The architecture is deliberately lean. All browser logic lives in the MCP server — the
model only sees clean tool inputs and outputs.

| Metric | Value |
|---|---|
| Tokens per track | ~860 |
| Context used per 4-track batch | ~4% of 200k window |
| KV cache similarity per turn | 0.99+ |
| Tracks per context window | ~80 before degradation |
| Prompt eval speed (35B MoE) | ~1,200 tok/s |
| Generation speed (35B MoE) | ~115 tok/s |

Tested on AMD Radeon AI PRO R9700 (32GB VRAM), Fedora, ROCm native drivers, LM Studio.

The ~4% context figure means you can load a 20-30 track batch brief and the model will
plan and execute the whole thing unattended in a single conversation.

---

## GPU Requirements

| VRAM | Model | Tokens per track |
|---|---|---|
| 8GB | Qwen3.5-9B | ~900 |
| 16GB+ | Qwen3.6-35B-A3B | ~860 |
| 32GB | Qwen3.6-35B-A3B | ~860 |

All produce proper full-structure tracks with a BRIDGE. Bigger model = better lyrics,
not the difference between working and not working.

---

## LM Studio Settings

- **Context length:** 8k minimum, 32k+ recommended for large batches
- **Temperature:** 0.3–0.35 for reliable tool calling
- **Thinking/reasoning mode:** OFF — inflates token count with no creative benefit
- **One conversation per session** — multiple cached sessions thrash the KV cache

---

## How It Actually Works (Technical)

```
You: "make me 4 hard house tracks"
  ↓
Model plans all 4 tracks upfront (lyrics + styles) before touching any tools
  ↓
For each track:
  validate_cookie → navigate_suno → clear_form → type_lyrics → type_style
  → click_create → wait_next (45–90s randomised delay)
  ↓
suno_mcp.py handles everything below this line:
  - Cookie injection via CDP Network.setCookie
  - Field targeting: lyrics by data-testid, style by DOM position after lyrics
  - React input: native HTMLTextAreaElement prototype setter + input/change events
    (direct value injection and keystroke simulation both fail to update React state —
    the prototype setter bypasses React's own override so onChange fires correctly)
  - Create button: text-match with .includes() to handle emoji prefixes
  - Clear: clicks button, fires confirm if dialog appears, succeeds either way
  - wait_next: randomised 45–90s sleep between submissions
```

The React input handling is the key technical detail. Suno uses controlled React
components — the Create button stays disabled until React's internal state reflects the
field content. Standard paste, direct DOM value assignment, and CDP keystroke events all
write to the DOM but leave React's state empty, so the button never activates. The native
prototype setter approach sets the value at the browser engine level before React can
intercept it, then fires a bubbling input event that React's event delegation picks up and
processes as a genuine user interaction.

---

## A Note on Terms of Use

Read this before you run it. The following is a plain-language summary of how this project
relates to Suno's Terms of Service — **not legal advice.** Read the current terms yourself
and decide what you're comfortable with.

**What this tool does and doesn't do.** It generates **original** lyrics and style prompts
and types them into *your own* logged-in Suno session. It does **not** scrape, download,
copy, extract, or clone Suno's content. Suno's anti-scraping language covers pulling data
*out* of the Service. This tool only puts original input *in*.

**Where the gray area actually is.** A separate clause prohibits automated access that
resembles a "robot." Whether driving your own session this way is permitted is genuinely
unsettled. The realistic consequence is **not** a lawsuit — it's that Suno may suspend an
account at its sole discretion. Use an account you can afford to lose.

**Ownership depends on your tier.** Free/Basic tier output is licensed for personal
non-commercial use only. Pro/Premier tiers assign ownership to you. If you intend to
release or monetize tracks, your subscription tier matters.

**On distributing this setup.** This is a general-purpose local-AI browser-automation
project provided as-is for personal and educational use. Each person who runs it is solely
responsible for their own use, account, and compliance. The author provides no warranty
and accepts no liability.

---

## Credits

- **FastMCP** — MCP server framework
- **LM Studio** — local model serving
- **Qwen3** — Alibaba's MoE model that saw a Suno page and asked if it could make a song

The model chose this job. We just gave it better instructions.

---

*Built accidentally on a Saturday. Debugged on a Sunday.*
