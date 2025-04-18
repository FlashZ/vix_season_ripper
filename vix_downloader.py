#!/usr/bin/env python3
"""
Robust **ViX season ripper** – **v1**
=======================================
"""
from __future__ import annotations
import argparse, csv, json, logging, re, subprocess, time
from pathlib import Path
from typing import List, Set, Tuple

from unidecode import unidecode
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, StaleElementReferenceException,
)

###############################################################################
# --------------------------- helpers --------------------------------------- #
###############################################################################
SAFE = "-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MPD_TIMEOUT = 45  # seconds

def slug(txt: str) -> str:
    txt = unidecode(txt)
    txt = "".join(c if c in SAFE else "_" for c in txt)
    return re.sub(r"_+", "_", txt).strip("_ ")

def run(cmd: list[str]) -> int:
    logging.debug("EXEC: %s", " ".join(cmd))
    return subprocess.call(cmd, shell=False)

###############################################################################
# --------------------------- selenium utils -------------------------------- #
###############################################################################
def make_driver(headless: bool = False) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--lang=es-ES,es")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    opts.set_capability("goog:perfLoggingPrefs", {"enableNetwork": True})
    d = webdriver.Chrome(options=opts)
    d.set_page_load_timeout(60)
    return d

def wait_css(drv: webdriver.Chrome, selector: str, sec: int = 20):
    return WebDriverWait(drv, sec).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
    )

def js_click(drv: webdriver.Chrome, el):
    drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        drv.execute_script("arguments[0].click();", el)
    except Exception:
        el.send_keys(Keys.ENTER)

def safe_get_attr(drv: webdriver.Chrome, el, attr: str):
    try:
        return drv.execute_script(
            "return arguments[0]?.getAttribute(arguments[1]);", el, attr
        )
    except StaleElementReferenceException:
        return None

###############################################################################
# --------------------------- workflow -------------------------------------- #
###############################################################################
def prepare_season(drv: webdriver.Chrome, season: int):
    try:
        btn = wait_css(drv, 'button[aria-haspopup="listbox"]', 7)
        js_click(drv, btn)
        for opt in drv.find_elements(By.CSS_SELECTOR, '[role="option"], li'):
            if re.search(fr"\b{season}\b", opt.text):
                js_click(drv, opt)
                time.sleep(1)
                return
    except TimeoutException:
        logging.info("No season selector – assuming single season.")

def click_range(drv: webdriver.Chrome, label: str):
    btn = wait_css(drv, 'button[aria-label="Selected Item"]', 5)
    for _ in range(3):
        js_click(drv, btn)
        try:
            xpath = f'//div[@role="button" and normalize-space(text())="{label}"]'
            opt = drv.find_element(By.XPATH, xpath)
            js_click(drv, opt)
            time.sleep(2)
            return
        except (WebDriverException, StaleElementReferenceException):
            time.sleep(0.6)
    raise RuntimeError(f"could not click range '{label}'")

def extract_card_meta(card) -> Tuple[int, str]:
    # (Keep this function as is)
    raw = (card.text or "").replace("\n", " ")
    m = re.search(r"EP\.?\s*(\d+)", raw, re.I)
    ep_num = int(m.group(1)) if m else -1
    title = re.sub(r"EP\.?\s*\d+", "", raw, flags=re.I).strip() or "Episode"
    # Add a little robustness for missing titles
    if not title and ep_num != -1:
        title = f"Episode {ep_num}"
    elif not title and ep_num == -1:
        title = "Unknown Episode"
    return ep_num, title

def scroll_and_extract_metadata(
    drv: webdriver.Chrome,
    cont_sel: str,
    card_sel: str,
    all_cards_data: dict[str, Tuple[int, str]], # Pass the main dict here
    max_scrolls: int = 100 # Increased max scrolls just in case
) -> int:
    """
    Scrolls the WINDOW, finds new cards within the specific container,
    extracts metadata, and adds them to the all_cards_data dictionary.
    Returns the number of new items added in this pass.
    """
    newly_added_count = 0
    stagnant_scroll_attempts = 0 # Counter for attempts where scroll position didn't change
    processed_in_this_scroll: Set[str] = set() # Track hrefs processed in the *entire* scroll operation for this range

    logging.info("Starting scroll/extract loop for current range...")

    for scroll_attempt in range(max_scrolls):
        # Get current scroll position *before* doing anything else
        try:
            current_scroll_y = drv.execute_script("return window.pageYOffset;")
        except WebDriverException as e:
             logging.warning("Could not get current scroll position: %s. Stopping scroll.", e)
             break

        cont = None
        try:
            # Still need the container to find elements within it accurately
            cont = drv.find_element(By.CSS_SELECTOR, cont_sel)
        except Exception:
            try:
                cont = wait_css(drv, cont_sel, 3) # Shorter wait, might not be needed if window scroll works
            except TimeoutException:
                # If container disappears maybe content loaded differently? Less critical now.
                logging.warning("Could not find scroll container '%s' (might be okay if window scrolled)", cont_sel)
                # Don't break immediately, let window scroll try
                pass # Continue to scroll attempt


        # Find card elements *within the container* in the current view
        current_cards = []
        if cont: # Only search if container was found
            try:
                current_cards = cont.find_elements(By.CSS_SELECTOR, card_sel)
                logging.debug(f"Scroll attempt {scroll_attempt + 1}: Found {len(current_cards)} card elements in container.")
            except StaleElementReferenceException:
                logging.debug("Stale container reference during card search, attempting next scroll.")
                time.sleep(0.5)
                # Don't process, just try scrolling again
            except WebDriverException as e:
                 logging.warning("Error finding card elements: %s", e)
                 # Don't process, try scrolling
        else:
             logging.debug("Container not found in this iteration, relying on window scroll.")


        # Process elements found in the current view
        found_new_this_iteration = False
        for card_link_el in current_cards: # If current_cards is empty, this loop is skipped
            href = None
            try:
                href = safe_get_attr(drv, card_link_el, "href")
                if not href: continue

                # Ensure URL is absolute for consistent tracking
                full_href = href if href.startswith("http") else drv.current_url.split("/detail")[0] + href

                # Check if we already processed this specific href in this range's scroll operation
                if full_href not in processed_in_this_scroll:
                     # Also double-check against the global dict, though processed_in_this_scroll should cover it for the current range
                     if full_href not in all_cards_data:
                        try:
                            # Locate parent element containing metadata relative to the link
                            # Using XPath ancestor is generally robust here
                            parent_button = card_link_el.find_element(By.XPATH, "./ancestor::div[@role='button'][1]")
                            num, title = extract_card_meta(parent_button)

                            # Basic validation of extracted data
                            if num != -1 or "Unknown" not in title:
                                logging.debug("-> Extracted: Href=%s, Num=%d, Title=%s", full_href, num, title)
                                all_cards_data[full_href] = (num, title)
                                processed_in_this_scroll.add(full_href) # Mark as processed for this range
                                newly_added_count += 1
                                found_new_this_iteration = True # Mark that we found something new overall
                            else:
                                logging.warning("Metadata extraction failed for card with href %s (Num=%d, Title='%s')", full_href, num, title)
                                processed_in_this_scroll.add(full_href) # Mark as processed even if extraction failed to avoid retrying

                        except NoSuchElementException:
                             logging.warning("Could not find ancestor button element for href %s. Structure might differ.", href)
                             processed_in_this_scroll.add(full_href) # Mark as processed to avoid retrying
                        except Exception as e:
                            logging.warning("Error processing card details for href %s: %s", href, e)
                            processed_in_this_scroll.add(full_href) # Mark as processed to avoid retrying
                     else:
                         # Already exists in global dict (e.g. from a previous range), mark as processed for this scroll too
                         processed_in_this_scroll.add(full_href)

            except StaleElementReferenceException:
                logging.debug("Stale card link element encountered for href %s, skipping.", href)
                continue # Skip this card, try others


        # --- Attempt to scroll the WINDOW ---
        try:
            scroll_height_before = drv.execute_script("return document.body.scrollHeight;")
            logging.debug(f"Scroll attempt {scroll_attempt + 1}: Scrolling window down from Y={current_scroll_y}...")
            # Scroll down by 80% of the viewport height
            drv.execute_script("window.scrollBy(0, window.innerHeight * 0.8);")
            # Wait longer after scroll to allow content loading triggered by window scroll
            time.sleep(2.0) # Increased wait time
        except WebDriverException as e:
             logging.warning("Error during window scrolling: %s", e)
             break # Stop scrolling this range if it fails

        # --- Check for Stagnancy based on scroll position ---
        try:
            new_scroll_y = drv.execute_script("return window.pageYOffset;")
            scroll_height_after = drv.execute_script("return document.body.scrollHeight;")
        except WebDriverException as e:
             logging.warning("Could not get scroll position/height after scroll: %s. Stopping scroll.", e)
             break

        # Check if the total scrollable height increased (content loaded)
        height_increased = scroll_height_after > scroll_height_before
        # Check if the scroll position actually changed
        scrolled_down = new_scroll_y > current_scroll_y + 5 # Use a small threshold

        if scrolled_down or height_increased:
            # If we scrolled down OR if new content loaded (increasing height), things are likely progressing.
            stagnant_scroll_attempts = 0 # Reset stagnant counter
            logging.debug(f"Scroll attempt {scroll_attempt+1}: Window scroll position changed to {new_scroll_y} or height increased. Progress likely.")
        else:
            # If we didn't scroll down AND the page height didn't increase...
            # Double-check if we are already at the bottom
            window_height = drv.execute_script("return window.innerHeight;")
            if (new_scroll_y + window_height) >= scroll_height_after - 10: # Check if bottom is reached
                logging.info(f"Scroll attempt {scroll_attempt+1}: Bottom of page likely reached (Y={new_scroll_y}, H={scroll_height_after}).")
                break # Exit scroll loop

            # Otherwise, increment stagnant counter
            stagnant_scroll_attempts += 1
            logging.debug(f"Scroll attempt {scroll_attempt+1}: Window scroll position ({new_scroll_y}) AND page height ({scroll_height_after}) did not change significantly. Stagnant count: {stagnant_scroll_attempts}")


        if stagnant_scroll_attempts >= 5: # Stop after 5 consecutive attempts with no scroll change / height increase
            logging.info("Scrolling stopped after %d stagnant scroll attempts.", stagnant_scroll_attempts)
            break


    logging.info("Finished scrolling/extraction for this range. Total new items processed in this pass: %d", newly_added_count)
    return newly_added_count


def collect_episode_links(drv: webdriver.Chrome) -> List[Tuple[int, str, str]]:
    episode_range_button_sel = 'button[aria-label="Selected Item"]'
    scroll_container_sel = 'div.ContentList_container__cV53J'
    # Make card selector slightly more specific if possible, but keep it simple for now
    card_sel = 'a.Card_link__M4ZXt[href]' # Ensure it has an href attribute

    labels = []
    dropdown = None

    # --- Get Episode Range Labels ---
    try:
        dropdown = wait_css(drv, episode_range_button_sel, 10)
        time.sleep(0.5)
        js_click(drv, dropdown)
        time.sleep(1.0) # Wait for dropdown items

        # Wait for options to be present
        opts_container = WebDriverWait(drv, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'ul[role="listbox"]'))
        )
        opts = opts_container.find_elements(By.CSS_SELECTOR, 'div[role="button"]')
        labels = [o.text.strip() for o in opts if o.text.strip()]
        labels = [lbl for lbl in labels if lbl.startswith("Episodios")] # Keep the filter
        logging.info("Found %d range options: %s", len(labels), labels)

        # Try clicking the dropdown button again to close it
        try:
            js_click(drv, dropdown)
            time.sleep(0.5)
        except Exception:
            logging.debug("Could not click dropdown to close, maybe already closed.")
            pass # Continue anyway

    except TimeoutException:
        logging.info("No episode range dropdown found, treating as single block.")
        labels = ["current"] # Use a placeholder to trigger one loop iteration
        dropdown = None # Ensure dropdown is None
    except Exception as e:
        logging.error("Error finding/processing episode range dropdown: %s", e)
        return [] # Return empty list if dropdown fails critically

    all_cards_data: dict[str, Tuple[int, str]] = {}

    # --- Iterate Through Each Episode Range ---
    for label in labels:
        if dropdown and label != "current":
            logging.info("→ Selecting episode range: %s", label)
            try:
                # Click dropdown open
                js_click(drv, dropdown)
                time.sleep(0.8) # Wait a bit

                # Find and click the specific option
                xpath = f'//ul[@role="listbox"]//div[normalize-space(text())="{label}"]'
                opt = WebDriverWait(drv, 7).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                js_click(drv, opt)
                logging.info("Clicked range option '%s'. Waiting for content load...", label)
                time.sleep(3.0) # Increase wait time after selection significantly

            except Exception as e:
                logging.warning("Could not click range '%s': %s. Trying to close dropdown.", label, e)
                # Attempt to close dropdown if clicking option failed
                try: js_click(drv, dropdown)
                except Exception: pass
                time.sleep(0.5)
                continue # Skip this range if selection failed

        # --- Scroll and Extract Data for the Current Range ---
        logging.info("Processing container for range: '%s'", label)
        scroll_and_extract_metadata(drv, scroll_container_sel, card_sel, all_cards_data, max_scrolls=60) # Increase max_scrolls too?


    # --- Final Sorting and Formatting ---
    if not all_cards_data:
        logging.warning("No episode data could be extracted from any range.")
        return []

    # Convert dict to list of tuples and sort
    sorted_cards = sorted(
        [(data[0], data[1], href) for href, data in all_cards_data.items()], # (num, title, href)
        key=lambda x: x[0] if x[0] != -1 else float('inf') # Sort unknowns last
    )

    # Assign sequential numbers if EP number extraction failed but order is known
    unknown_counter = 1
    final_list = []
    max_known_ep = max((c[0] for c in sorted_cards if c[0] != -1), default=0)

    for i, (num, title, href) in enumerate(sorted_cards):
         if num == -1:
             # Try to infer based on position relative to known numbers
             # This is complex; a simpler fallback is sequential numbering for unknowns
             logging.warning(f"Episode number missing for '{title}' ({href}). Assigning placeholder.")
             # Simple approach: assign based on overall order if needed, or just keep -1
             # If sorting seems reliable, you could try: inferred_num = max_known_ep + unknown_counter
             # unknown_counter += 1
             # For now, just pass it through as -1 or assign a large number to keep it last
             final_list.append((-1, title, href)) # Keep -1 to indicate unknown original number
         else:
             final_list.append((num, title, href))


    logging.info("Total unique episodes extracted: %d", len(final_list))
    # Log first few found entries for verification
    if final_list:
        logging.info("First few episodes found: %s", final_list[:5])

    return final_list
def clear_perf_log(drv: webdriver.Chrome):
    try:
        drv.get_log("performance")
    except Exception:
        pass

def capture_mpd(drv: webdriver.Chrome, timeout_sec: int = MPD_TIMEOUT) -> str | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for entry in drv.get_log("performance"):
            try:
                msg = json.loads(entry["message"])["message"]
                if msg.get("method") == "Network.requestWillBeSent":
                    url = msg["params"]["request"]["url"]
                    if ".mpd" in url.lower():
                        return url
            except Exception:
                continue
        time.sleep(0.3)
    return None

def n_m3u8dl_re(mpd: str, out_stub: Path, lang: str, headers: dict[str, str]):
    hdr = sum([["--header", f"{k}: {v}"] for k, v in headers.items()], [])
    cmd = [
        "N_m3u8DL-RE", mpd,
        "--save-dir", str(out_stub.parent),
        "--save-name", out_stub.name,
        "--thread-count", "8",
        "-sv", "best",
        "-sa", f"best:lang={lang}",
        "-ss", lang,
        "--del-after-done"
    ] + hdr
    return run(cmd)

def convert_vtt(vtt: Path, srt: Path):
    if vtt.exists():
        run(["ffmpeg", "-y", "-i", str(vtt), str(srt)])
        vtt.unlink(missing_ok=True)

###############################################################################
# --------------------------- resume helpers ------------------------------- #
###############################################################################
def previously_done(out_dir: Path, titles_csv: Path) -> Set[str]:
    done: Set[str] = set()
    if titles_csv.exists():
        with titles_csv.open(newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if row:
                    done.add(row[0].upper())
    for f in out_dir.glob("*.mp4"):
        m = re.search(r"(S\d{2}E\d{3})", f.stem, re.I)
        if m:
            done.add(m.group(1).upper())
    return done

###############################################################################
# --------------------------- main ----------------------------------------- #
###############################################################################
def main():
    ap = argparse.ArgumentParser(description="Download an entire ViX season")
    ap.add_argument("url")
    ap.add_argument("--season", type=int, default=1)
    ap.add_argument("--lang", default="es")
    ap.add_argument("--out", type=Path, default=Path.cwd())
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--debug", action="store_true", help="Enable debug logging") # Add debug flag
    args = ap.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO # Set level based on flag
    logging.basicConfig(level=log_level,
                        format="%(asctime)s %(levelname)5s : %(message)s")

    args.out.mkdir(parents=True, exist_ok=True)
    # Use output dir for resume files for better organization
    titles_csv = args.out / "titles.csv"
    fails_path = args.out / "failures.log"

    done_eps = previously_done(args.out, titles_csv)
    if done_eps:
        logging.info("Resuming – %d episodes already tracked or downloaded", len(done_eps))

    drv = None # Initialize drv to None
    fails = fails_path.open("a", encoding="utf-8", newline="")
    titles = titles_csv.open("a", newline="", encoding="utf-8")
    writer = csv.writer(titles)

    try:
        drv = make_driver(args.headless)
        drv.get(args.url)
        # Wait for page title or a known element before proceeding
        try:
             WebDriverWait(drv, 20).until(EC.title_contains("ViX")) # Basic wait
             logging.info("Page loaded: %s", drv.title)
        except TimeoutException:
             logging.error("Page load timed out or title didn't contain 'ViX'.")
             return # Exit if initial page load fails

        prepare_season(drv, args.season)

        title_raw = re.sub(r"\bpor\s+ViX.*", "", drv.title, flags=re.I)
        title_raw = re.sub(r"^\s*Ver\s+", "", title_raw, flags=re.I).strip()
        series_title = slug(title_raw) if title_raw else "Unknown_Series"
        logging.info("Series detected: %s", series_title)

        eps = collect_episode_links(drv) # Use the modified function
        logging.info("%d unique episodes collected for download.", len(eps))

        if not eps:
             logging.warning("No episodes collected. Exiting.")
             return

        for ep_num, ep_title, link in eps:
            # Generate ep_code, handle -1 case carefully
            if ep_num != -1:
                ep_code = f"S{args.season:02d}E{ep_num:03d}"
                base_filename = slug(f"{series_title}.{ep_code}")
            else:
                # Fallback for missing episode numbers: use slugified title
                ep_code = f"S{args.season:02d}EUNK_{slug(ep_title[:30])}" # Use part of title slug
                base_filename = slug(f"{series_title}.{ep_code}")
                ep_code = f"UNK_{slug(ep_title[:30])}" # Use simpler code for tracking/logging if num is unknown

            if ep_code.upper() in done_eps:
                logging.info("-- skipping %s ('%s') (already tracked/downloaded)", ep_code, ep_title)
                continue

            # Ensure link is absolute before navigating
            if not link.startswith("http"):
                 logging.warning("Skipping potentially malformed relative link: %s", link)
                 continue

            clear_perf_log(drv)
            logging.info("=== Processing Ep %s ('%s') ===", ep_code, ep_title)
            logging.info("Navigating to: %s", link)
            try:
                drv.get(link)
                # Add a wait after navigation for the player/page elements to load
                WebDriverWait(drv, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "h1, h2")) # Wait for title element again
                )
                time.sleep(2) # Extra buffer
            except TimeoutException:
                logging.error("Timeout loading episode page: %s", link)
                fails.write(f"{ep_code},{link},PAGE_LOAD_TIMEOUT\n"); fails.flush()
                continue
            except WebDriverException as e:
                 logging.error("WebDriverException loading episode page %s: %s", link, e)
                 fails.write(f"{ep_code},{link},PAGE_LOAD_FAIL\n"); fails.flush()
                 continue


            # --- Optional: Sanity check page title again ---
            try:
                watch_title_el = wait_css(drv, "h1,h2", 5) # Reduced wait
                watch_title = watch_title_el.text.strip()
                if watch_title and unidecode(watch_title.lower()) not in unidecode(ep_title.lower()):
                    logging.warning("⚠️ Page title '%s' differs from collected title '%s'. Using page title.", watch_title, ep_title)
                    ep_title = watch_title # Update title if different
                    # Re-generate filename if title changed significantly? Optional.
                    # if ep_num == -1: base_filename = slug(f"{series_title}.UNK_{slug(ep_title[:30])}")
            except Exception as e:
                 logging.debug("Could not verify page title: %s", e)
                 pass
            # -----------------------------------------------

            mpd = capture_mpd(drv, timeout_sec=MPD_TIMEOUT) # Use existing function
            if not mpd:
                logging.error("NO_MPD found for %s ('%s') at %s", ep_code, ep_title, link)
                fails.write(f"{ep_code},{link},NO_MPD\n"); fails.flush()
                continue
            logging.info("MPD found: %s", mpd)

            base_stub = args.out / base_filename # Use the generated filename
            headers = {
                "User-Agent": drv.execute_script("return navigator.userAgent;"),
                "Referer": drv.current_url # Use the current episode page as referer
            }

            logging.info("Starting download for %s to %s", ep_code, base_stub.with_suffix(".mp4"))
            # Ensure N_m3u8DL-RE path is correct or in system PATH
            try:
                dl_ret_code = n_m3u8dl_re(mpd, base_stub, args.lang, headers)
                if dl_ret_code != 0:
                    logging.error("N_m3u8DL-RE failed (code %d) for %s", dl_ret_code, link)
                    fails.write(f"{ep_code},{link},DL_FAIL_CODE_{dl_ret_code}\n"); fails.flush()
            # Clean up potentially partial files, BUT KEEP the final MP4 if it exists
                    logging.warning("Download command failed (code %d). Attempting cleanup, but preserving MP4 if found.", dl_ret_code)
                    final_mp4_path = base_stub.with_suffix(".mp4") # Define the target file path
                    final_m4a = base_stub.with_suffix(".m4a").resolve()
                    #try:
                    #    for f in args.out.glob(f"{base_stub.name}*"):
                    #        # Resolve paths to compare apples-to-apples (absolute paths)
                    #        if f.resolve() != final_mp4_path.resolve():
                    #            logging.debug("Cleaning up non-final/temp file: %s", f.name)
                    #            f.unlink(missing_ok=True)
                    #        else:
                    #            logging.info("Keeping potentially completed file despite error: %s", f.name)
                    #except Exception as cleanup_e:
                    #    logging.error("Error during cleanup attempt: %s", cleanup_e)
                    continue # Skip to next episode on download failure
                else:
                    # --- DOWNLOAD SUCCEEDED ---
                    logging.info("Download command completed successfully for %s", ep_code)

                    # --- RECORD SUCCESS IMMEDIATELY ---
                    # Write success record now that download is confirmed complete.
                    # This happens regardless of subtitle outcome.
                    writer.writerow([ep_code, ep_title, base_stub.name + ".mp4"]) # Record MP4 filename
                    titles.flush()
                    done_eps.add(ep_code.upper()) # Add to runtime set to prevent re-download if script restarts quickly
                    logging.info("✔ Successfully recorded download for %s ('%s')", ep_code, ep_title)
                    # --- END RECORD SUCCESS ---

            except FileNotFoundError:
                logging.error("FATAL: 'N_m3u8DL-RE' command not found. Make sure it's installed and in your PATH.")
                # No point continuing if downloader is missing for all episodes
                return # Exit script
            except Exception as e:
                logging.error("Exception during download process for %s: %s", ep_code, e)
                fails.write(f"{ep_code},{link},DL_EXCEPTION\n"); fails.flush()
                # Decide if you want to clean up potential partial files here too
                # for f in args.out.glob(f"{base_stub.name}*"): f.unlink(missing_ok=True)
                continue # Skip to next episode on other download exceptions


            # --- ATTEMPT SUBTITLE CONVERSION (AFTER SUCCESSFUL DOWNLOAD IS RECORDED) ---
            # This section now runs independently of the success logging for the download itself.
            vtt_file = base_stub.with_suffix(f".{args.lang}.vtt")
            srt_file = base_stub.with_suffix(f".{args.lang}.srt")
            if vtt_file.exists():
                logging.info("Attempting VTT->SRT conversion for %s...", ep_code)
                try:
                    # Optional: You could check the return code from convert_vtt if run() was modified to return it
                    convert_vtt(vtt_file, srt_file)
                    if srt_file.exists():
                         logging.info("Subtitle conversion successful for %s.", ep_code)
                    else:
                         # This might happen if ffmpeg failed internally but didn't raise an exception via run()
                         logging.warning("Subtitle conversion attempted for %s, but SRT file not found afterwards. Check ffmpeg output/logs if needed.", ep_code)
                         # Keep the VTT if conversion fails? Or log error to fails.log?
                         # fails.write(f"{ep_code},{link},SUB_CONVERT_FAIL\n"); fails.flush()
                except Exception as e:
                     logging.error("Error during subtitle conversion for %s: %s", ep_code, e)
                     # Log this specific failure too?
                     # fails.write(f"{ep_code},{link},SUB_CONVERT_EXCEPTION\n"); fails.flush()
            else:
                logging.info("No VTT subtitle found for %s (file %s missing). Skipping conversion.", ep_code, vtt_file.name)
            # --- END SUBTITLE CONVERSION ---


            time.sleep(2) # Small delay before next episode

    except KeyboardInterrupt:
        logging.warning("Keyboard interrupt detected. Exiting.")
    except Exception as e:
        logging.error("An unexpected error occurred in main loop: %s", e, exc_info=True) # Log traceback
    finally:
        if drv:
            logging.info("Closing WebDriver.")
            drv.quit()
        if fails:
            fails.close()
        if titles:
            titles.close()
        logging.info("Script finished.")


if __name__ == "__main__":
    main()