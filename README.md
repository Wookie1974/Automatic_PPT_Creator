# Automatic PPT Creator

## Overview

This repository contains a tool for automatically generating PowerPoint presentations from Chrome bookmarks.

## Active Script

**`extract_bookmarks.py`** is the primary and only active script in this repository. It:

- Reads Chrome bookmarks from a specified folder
- Extracts content and images from the bookmarked web pages using Playwright
- Uses OpenAI's GPT-4 to clean and process the extracted text content
- Generates a PowerPoint presentation with the processed content

## Usage

1. Ensure you have the required dependencies installed:
   ```bash
   pip install python-pptx playwright beautifulsoup4 openai
   playwright install chromium
   ```

2. Set your OpenAI API key as an environment variable:
   ```bash
   export OPENAI_API_KEY="your-api-key-here"
   ```

3. Run the script:
   ```bash
   python extract_bookmarks.py
   ```

4. When prompted, enter the name of the Chrome bookmarks folder you want to process.

## Output

The script generates several files:
- `page_data_list.json` - Raw extracted data from web pages
- `page_data_list_cleaned.json` - AI-cleaned version of the extracted data
- `bookmarks_output.pptx` - The final PowerPoint presentation

## Repository Cleanup

**Note**: This repository previously contained a multi-stage pipeline (files numbered 1-5) for processing Autodesk Help documentation. These files have been removed as they are no longer part of the active codebase:

- ~~`1_probe_children_shadow.py`~~ (removed)
- ~~`2_extract_pages_to_disk.py`~~ (removed) 
- ~~`3_sectionize_to_slidespec.py`~~ (removed)
- ~~`4_clean_slidespecs_overview_and_dupes.py`~~ (removed)
- ~~`5_slidespec_to_ppt.py`~~ (removed)

The cleanup was performed to streamline the repository and focus on the active bookmark-to-PowerPoint functionality provided by `extract_bookmarks.py`.