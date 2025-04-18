# ViX Season Ripper – v1

**Robust ViX season downloader** with automatic scrolling, MPD capture, resume support and VTT→SRT conversion.

---

## ⚙️ Features

- Auto‑scrolls & clicks through “Episodios …” ranges to harvest **all** episodes  
- Grabs the **first .mpd** URL from Chrome perf‑logs  
- Uses **N_m3u8DL‑RE** to download highest‑bit‑rate video & Spanish audio tracks  
- Converts WebVTT subtitles to SRT (`.es.srt`) and cleans up VTT  
- **Resume support** – skips already‑downloaded episodes on re‑run  
- Generates **`titles.csv`** and **`failures.log`**  

---

## 🚀 Requirements

- Python 3.8+  
- [Chrome ≥115](https://www.google.com/chrome/) + matching Chromedriver  
- `pip install selenium rich tqdm unidecode`  
- **N_m3u8DL‑RE** & **ffmpeg** in `$PATH`  

---

## 📥 Installation


git clone https://github.com/FlashZ/vix-season-ripper.git
cd vix-season-ripper
pip install -r requirements.txt
### ensure N_m3u8DL-RE & ffmpeg are installed and in PATH

---

## 🛠️ Usage

```bash
python vix_downloader.py \
  "https://vix.com/es-es/detail/series-XXXX" \
  --season 1 \
  --lang es \
  --out /path/to/downloads \
  [--headless] [--debug]
```

url – Base series URL

--season – Season number (default 1)

--lang – Subtitle & audio language code (default es)

--out – Output directory (default cwd)

--headless – Run Chrome headless

--debug – Enable DEBUG logging

---


## 📝 Configuration
You can tweak behavior by editing the top of vix_downloader.py:

```bash
# at top of script
MPD_TIMEOUT = 45        # time to wait for .mpd URL (seconds)
SAFE = "-_.() abc…0123456789"  # allowed filename chars
```
Or adjust:

Scroll delays: time.sleep(...) after selections

max_scrolls in scroll_and_extract_metadata()

N_m3u8DL-RE flags (bitrate selection, threads)

---

## 🔄 Resume & Logging
Already‑downloaded episodes (detected via titles.csv or existing .mp4) are skipped on re‑run.

titles.csv stores: EP_CODE, Episode Title, Filename

failures.log records episodes that failed to download or convert.

---

## 🙌 Support Me
If you find this tool useful, I’d appreciate a coffee:
<a href="https://buymeacoffee.com/nickkb" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

---

## 🤝 Contributing
Fork the repo

Create a feature branch

Submit a PR with tests/examples

Please follow code style and update this README where appropriate.

---

## 📜 License
This project is licensed under the AGPL‑3.0 license. See LICENSE for details.
