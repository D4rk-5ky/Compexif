# Image EXIF Compare

A PySide6 desktop application for loading, comparing, grouping, and marking image files based on embedded metadata dates.

The main goal of this project is to help find possible duplicate photos by comparing images that share the same metadata date, then make it easier to decide which file should be kept and which files should be marked for later deletion.

> [!IMPORTANT]
> **Disclaimer:** This software is provided as-is. You, the user, are fully responsible for any damage, data loss, accidental deletion, corruption, overwrite, or other problems caused by using this software. Always test with copied files first and keep backups before using it on important photos.

## Features

- Load images from folders.
- Append more images to the current list.
- Save and load image lists as JSON.
- Append a saved JSON image list to the current list.
- Only load images that contain a real embedded metadata date.
- Compare a locked/default image against the currently selected/checked image.
- Sort the image list by columns:
  - Name
  - Width x Height
  - Size
  - Metadata date
  - File date
  - Folder
- Search for possible duplicate images based on identical embedded metadata dates.
- Group duplicate matches together in the list.
- Mark images for deletion without deleting them immediately.
- Mark images as kept.
- Greys out kept images in the list.
- Prevents kept images from being marked for deletion until the keep mark is cleared.
- Save and restore keep/delete marks in the JSON image list.
- Fullscreen-style comparison window with normal window borders and resize controls.
- Switch between default image and checked image inside the large image window.
- Keep/delete/clear mark actions inside the large image window apply to the image currently shown there.
- Show warnings/errors in a copyable window.
- Persistent bottom status statistics after scanning or loading lists.

## What counts as a duplicate?

This app treats files as **possible duplicate images** when they share the same embedded metadata date.

Example:

```text
IMG_0001.JPG       Metadata date: 2020-05-01 12:33:10
IMG_0001-copy.JPG  Metadata date: 2020-05-01 12:33:10
```

Those files will be grouped together as possible duplicates.

A duplicate **path** is different. If the exact same file path is already in the list, append operations skip it to avoid adding the same file twice.

```text
Duplicate image match = same embedded metadata date
Duplicate path        = exact same file location already loaded
```

## What does not count as metadata date?

Normal file information does not qualify a picture for metadata-date loading.

These do **not** count as metadata dates:

- file modified date
- file created date
- file name
- folder name
- file size
- image width/height

They are still shown as useful comparison data, but they do not decide whether an image has a real metadata date.

## Typical workflow

1. Click **Load pictures**.
2. Choose a folder containing images.
3. Wait for the scan to finish.
4. Click **Search duplicates**.
5. Review each duplicate group.
6. Lock one image as the default/reference image.
7. Compare it with the selected/checked image.
8. Use **Keep picture** or **Mark for deletion**.
9. Save the list as JSON.
10. Review the marked files later before doing any real deletion.

## Main buttons

```text
Load pictures
Append images to list
Append list
Save image list
Load image list
Search duplicates
```

### Load pictures

Replaces the current list with images from the selected folder or input.

### Append images to list

Adds qualifying images to the existing list without clearing it.

Exact same file paths already in the list are skipped.

### Append list

Loads a saved JSON image list and appends any new qualifying paths to the current list.

Existing keep/delete marks are preserved, and marks from the appended list are applied to newly added images.

### Save image list

Saves the current list to JSON, including:

- image paths
- visible order
- locked/default image
- keep/delete marks
- metadata-date filtering state

### Load image list

Loads a saved JSON image list and replaces the current list.

### Search duplicates

Searches the current list for images that share the same embedded metadata date, then groups those images together.

## Files menu

The **Files** menu mirrors the main button actions:

```text
Files
  Load pictures...
  Append images to list...
  Append image list...
  Save image list...
  Load image list...
  Search duplicates
  Show warnings/errors...
  Quit
```

## Keep/delete marking

Marking a file does not delete it from disk.

```text
Keep picture
  Marks the image as kept.
  Greys it out in the list.
  Prevents it from being marked for deletion.

Clear keep
  Removes the keep mark from the selected image.
  Allows the image to be marked differently again.

Mark for deletion
  Marks the image for later deletion.
  Does not delete the file immediately.
```

The app protects duplicate groups by preventing every image in the same duplicate group from being marked for deletion.

At least one image in a duplicate group must remain unmarked or kept.

## Fullscreen-style image viewer

Each picture preview frame has a semi-transparent fullscreen icon in the bottom-right corner.

Clicking it opens a large resizable window with normal window borders.

The large viewer:

- scales large images down to fit the window
- keeps smaller images at original size instead of enlarging them
- can switch between **Default image** and **Checked image**
- has **Keep picture**, **Clear mark**, and **Mark for deletion** controls

The keep/delete actions inside the fullscreen window always apply to the image currently visible in that fullscreen window.

```text
Open from default image  -> actions affect default image
Switch to checked image  -> actions affect checked image
Switch back to default   -> actions affect default image
```

Keyboard shortcuts inside the large image window:

```text
Space / Left / Right   Switch between default and checked image
Esc                    Close the large image window
```

## Persistent statistics

The bottom-left status line shows live information while scanning/loading and becomes a permanent statistics line after the operation is done.

Example:

```text
Walked over 1,917 file(s) · 666 had metadata date · 220 duplicate picture(s) in 95 group(s) · 446 unique picture(s) · 1 read warning/error(s)
```

Statistics can include:

- walked over file count
- number of files with metadata date
- duplicate picture count
- duplicate group count
- unique picture count
- files skipped because they had no metadata date
- exact same paths skipped during append
- read warnings/errors

## Warnings/errors window

Use:

```text
Files -> Show warnings/errors...
```

This opens a copyable warning/error window.

It can show things such as:

```text
/path/to/image.jpg: Pillow metadata read: UserWarning: Truncated File Read
/path/to/missing.jpg: Saved list entry is missing or no longer a file.
/path/to/file.txt: Saved list entry is not a supported image file.
```

If the warnings/errors window is open while appending images or loading/appending a list, new warnings/errors continue to appear in the same window.

## Saved JSON format

The saved image list remembers marks.

Example entry:

```json
{
  "path": "/path/to/image.jpg",
  "status": "delete",
  "marked_for_deletion": true,
  "marked_to_keep": false
}
```

Possible mark states:

```text
unmarked
marked for deletion
marked to keep
```

Older saved lists that only use the `status` field should still load.

## Installation

### Requirements

- Python 3
- PySide6
- Pillow
- optional IPTC support, depending on the project requirements file

Install requirements:

```bash
python3 -m pip install -r requirements.txt
```

If there is no requirements file yet, install the main dependencies manually:

```bash
python3 -m pip install PySide6 Pillow iptcinfo3
```

## Running

From the project folder:

```bash
python3 main.py
```

Or, if executable:

```bash
./main.py
```

## Project structure

```text
main.py                       App entry point
image_compare_layout.ui        Qt Designer UI layout
gui_app.py                     Main GUI logic
image_metadata.py              Metadata and EXIF reading
picture_loader.py              Image loading/scanning
picture_sorting.py             List sorting rules
picture_list_columns.py        List column formatting
picture_list_io.py             Save/load JSON image lists
duplicate_grouping.py          Duplicate grouping by metadata date
image_display.py               Preview image scaling/display
image_fullscreen.py            Large image viewer and overlay buttons
warnings_errors_dialog.py      Copyable warning/error window
about_dialog.py                About dialog
```

## Safety notes

This app is meant to help review images before deletion. Treat all delete marks as suggestions until you have manually verified them.

Recommended safe workflow:

1. Work on a copy of your photo folders first.
2. Save the JSON list before making major changes.
3. Review every image marked for deletion.
4. Keep backups of important photos.
5. Do not rely only on metadata dates to decide which file is original.

## Limitations

Duplicate detection is based on embedded metadata date, not image pixels.

Two different photos can technically share the same metadata date, especially if a camera burst mode, export tool, or copied metadata caused matching timestamps.

File size and width/height are shown to help you decide, but the app cannot guarantee which image is the original.

## Disclaimer

This software is provided without warranty. The user is solely responsible for using it safely.

By using this software, you accept full responsibility for any damage, data loss, accidental deletion, corruption, overwrite, wrong duplicate decision, or other loss caused directly or indirectly by this software.

Always keep backups and test with copied files before using it on important data.

## License

Add your chosen license here.

Example options:

- MIT License
- GPL-3.0
- Apache-2.0
- Private / All rights reserved

## Author

Created by **D4rk-5ky**.
