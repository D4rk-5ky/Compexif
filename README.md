# Image EXIF Compare / Compexif

A PySide6 desktop application for loading, comparing, grouping, marking, and safely trashing image files based on embedded metadata dates.

The main goal of this project is to help find possible duplicate photos by comparing images that share the same embedded metadata date, then make it easier to decide which file should be kept, which file should become the default/reference image, and which files should be moved to the operating-system Trash.

> [!IMPORTANT]
> **Disclaimer:** This software is provided as-is. You, the user, are fully responsible for any damage, data loss, accidental deletion, corruption, overwrite, wrong duplicate decision, or other problems caused by using this software. Always test with copied files first and keep backups before using it on important photos.

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
- Move all images marked for deletion to the system Trash/Recycle Bin.
- Rebuild duplicate groups after trashing files, so groups with only one remaining image become unique images.
- Fullscreen-style comparison window with normal window borders and resize controls.
- Open the fullscreen viewer from a selected image even when no default image has been chosen.
- Set the default/locked image from inside the fullscreen viewer.
- Cycle through pictures in the selected list group from inside the fullscreen viewer.
- Keep/delete/clear mark actions inside the fullscreen viewer apply to the image currently shown there.
- Show warnings/errors in a copyable window that updates while scans/appends continue.
- Persistent bottom-left status statistics after scanning, appending, loading lists, or searching duplicates.
- Multi-threaded metadata-date scanning, image-detail prefetching, saved-list validation, and duplicate search.
- Thread-count drop-down beside **Search duplicates**, filled from the detected CPU logical thread count.
- App/window icon included.
- PyInstaller one-file build support with icon.

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

They are still shown as useful comparison data, but they do not decide whether an image has a real embedded metadata date.

## Typical workflow

1. Click **Load pictures**.
2. Choose a folder containing images.
3. Wait for the scan to finish.
4. Click **Search duplicates**.
5. Review each duplicate group.
6. Open a selected image in the fullscreen viewer, or lock one image as the default/reference image.
7. Compare the default image with the selected/checked image.
8. Use **Keep picture**, **Clear keep**, or **Mark for deletion**.
9. Save the list as JSON if you want to continue later.
10. Review every image marked for deletion.
11. Use **🗑 Move to trash** to move marked images to the system Trash/Recycle Bin.

## Main buttons

```text
Load pictures
Append images to list
Append list
Save image list
Load image list
Search duplicates
Threads drop-down
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

### Threads drop-down

The drop-down beside **Search duplicates** controls how many worker threads are used for file-heavy work.

It is filled from the logical CPU thread count detected at startup, for example:

```text
1 thread
2 threads
3 threads
...
```

The chosen value is used for:

- metadata-date scanning
- image-detail prefetching before sorting/list building
- checking saved JSON image lists
- duplicate metadata-date search

Use fewer threads for slow USB drives, spinning disks, or network folders. Use more threads for SSD/NVMe storage if the computer stays responsive. Duplicate grouping is still done after the threaded results are merged, so matching metadata dates are found even when files were processed by different worker threads.

## Bottom action buttons

```text
Keep picture
Clear keep
Mark for deletion
🗑 Move to trash
```

### Keep picture

Marks the selected image as kept.

A kept image is greyed out in the list, gets the keep mark, and cannot be marked for deletion until the keep mark is cleared.

### Clear keep

Removes the keep mark from the selected image.

This allows the image to be marked differently again.

### Mark for deletion

Marks the selected image for later deletion.

This does **not** delete the file immediately.

### 🗑 Move to trash

Moves all images currently marked for deletion to the operating-system Trash/Recycle Bin.

This does **not** permanently delete the files.

After files are successfully moved to Trash:

- they are removed from the active app list
- their marks are removed from the active list state
- duplicate groups are rebuilt from the remaining images
- if only one image remains from a duplicate group, it moves into **Unique images**

If a file fails to move to Trash, it stays in the list and the error is added to **Files -> Show warnings/errors...**.

## Files menu

The **Files** menu mirrors the main list actions and includes the warning/error window:

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

## Keep/delete rules

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

Move to trash
  Moves all images marked for deletion to the system Trash/Recycle Bin.
  Removes successfully trashed images from the active app list.
```

The app protects duplicate groups by preventing every image in the same duplicate group from being marked for deletion.

At least one image in a duplicate group must remain unmarked or kept.

## Fullscreen-style image viewer

Each picture preview frame has a semi-transparent fullscreen icon in the bottom-right corner.

Clicking it opens a large resizable window with normal window borders.

The large viewer:

- scales large images down to fit the window
- keeps smaller images at original size instead of enlarging them
- can be opened from a selected image even when no default image has been chosen
- can switch between **Default image** and **Checked image**
- can set the currently shown image as the default image
- can cycle through pictures in the selected list group
- has **Keep picture**, **Clear mark**, and **Mark for deletion** controls

The top line in the large viewer has:

```text
◀
▶
Set as default
Default image
Checked image
Close
```

### Fullscreen group navigation

The arrow buttons cycle through the currently selected list group.

In duplicate view:

```text
◀ / ▶ cycles through the images under the same duplicate group header.
```

In flat list view:

```text
◀ / ▶ cycles through the visible image list.
```

This is useful when a duplicate group contains more than two images.

### Fullscreen action targeting

The keep/delete actions inside the fullscreen window always apply to the image currently visible in that fullscreen window.

```text
Open from default image    -> actions affect default image
Switch to checked image    -> actions affect checked image
Switch back to default     -> actions affect default image
Cycle to another group row -> actions affect the currently shown group image
```

The fullscreen status line shows which file the buttons apply to.

Example:

```text
Current fullscreen image: Checked image — IMG_0001.JPG · Status: unmarked.
```

Keyboard shortcuts inside the large image window:

```text
Left / Right   Previous/next image in the selected list group
Space          Switch between default and checked image
Esc            Close the large image window
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
Trash move failed: /path/to/image.jpg: Could not find a working trash handler.
```

If the warnings/errors window is open while appending images, loading/appending a list, or moving files to Trash, new warnings/errors continue to appear in the same window.

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

## Multi-threading

Metadata reading is the slowest part of large scans. Compexif now uses background worker threads for several file-heavy phases:

- scanning supported images for embedded metadata dates
- pre-reading image details used by sorting and list columns
- checking saved JSON lists
- searching for duplicate metadata-date groups

The GUI still builds Qt widgets on the main thread, because Qt widgets must not be created from worker threads. The worker threads only read file/metadata information and return results. Duplicate grouping is merged after all worker results are collected, so images can still be matched as duplicates even when they were processed by different threads.

The default worker count is automatic and capped at 8. You can change it directly in the **Threads** drop-down beside **Search duplicates**. The drop-down is filled from the detected logical CPU thread count.

You can also set the startup value with an environment variable:

```bash
COMPEXIF_METADATA_WORKERS=4 ./main.py
```

Or from the command line:

```bash
./main.py --metadata-workers 4
```

Use fewer workers for slow USB drives, spinning disks, or network folders. Use more workers only if your storage can handle it.

## Installation

### Requirements

- Python 3
- PySide6
- Pillow
- iptcinfo3
- Send2Trash

Install requirements:

```bash
python3 -m pip install -r requirements.txt
```

`Send2Trash` is used so files are moved to the system Trash/Recycle Bin instead of being permanently deleted.

## Running from source

From the project folder:

```bash
python3 main.py
```

Or, if executable:

```bash
./main.py
```

## App icon and PyInstaller one-file build

The project includes the app icon here:

```text
assets/Compexif_Exif_multi_size.ico
assets/Compexif_Exif_multi_size.png
```

The app uses the icon as the Qt application/window icon.

The PyInstaller build is configured as a **one-file** build.

### Build one executable on Linux

```bash
./build_pyinstaller.sh
```

Output:

```text
dist/Compexif
```

### Build one executable on Windows

```bat
build_pyinstaller_windows.bat
```

Output:

```text
dist\Compexif.exe
```

The Windows `.exe` should use the included Compexif icon as the actual executable file icon.

### Manual PyInstaller build

```bash
python3 -m pip install -r requirements-build.txt
python3 -m PyInstaller --noconfirm --clean Compexif.spec
```

The spec bundles:

```text
image_compare_layout.ui
assets/Compexif_Exif_multi_size.ico
```

The spec uses:

```text
icon='assets/Compexif_Exif_multi_size.ico'
```

On Linux, normal executable files do not reliably have embedded file icons in file managers. The app still uses the icon as its window/app icon. For a Linux file-manager/menu icon, build first and then run:

```bash
./install_linux_desktop_launcher.sh
```

That creates a local desktop launcher using the included PNG icon.

## Project structure

```text
main.py                           App entry point
image_compare_layout.ui           Qt Designer UI layout
gui_app.py                        Main GUI logic
image_metadata.py                 Metadata and EXIF reading
picture_loader.py                 Image loading/scanning
picture_sorting.py                List sorting rules
metadata_threading.py              Threaded image-summary and duplicate-search helpers
threaded_work.py                   Bounded thread-pool helper used by scan/sort/search
picture_list_columns.py           List column formatting
picture_list_io.py                Save/load JSON image lists
duplicate_grouping.py             Duplicate grouping by metadata date
image_display.py                  Preview image scaling/display
image_fullscreen.py               Large image viewer, group navigation, and overlay buttons
warnings_errors_dialog.py         Copyable warning/error window
trash_delete.py                   Move marked images to system Trash/Recycle Bin
about_dialog.py                   About dialog
assets/Compexif_Exif_multi_size.ico  App/window/PyInstaller icon
assets/Compexif_Exif_multi_size.png  Linux launcher icon
Compexif.spec                     PyInstaller one-file build spec
build_pyinstaller.sh              Linux PyInstaller build helper
build_pyinstaller_windows.bat     Windows PyInstaller build helper
install_linux_desktop_launcher.sh Optional Linux desktop launcher helper
requirements.txt                  Runtime Python dependencies
requirements-build.txt            Build dependencies
```

## Safety notes

This app is meant to help review images before deletion. Treat all delete marks as suggestions until you have manually verified them.

Recommended safe workflow:

1. Work on a copy of your photo folders first.
2. Save the JSON list before making major changes.
3. Review every image marked for deletion.
4. Use **🗑 Move to trash** instead of permanent deletion.
5. Check your Trash before emptying it.
6. Keep backups of important photos.
7. Do not rely only on metadata dates to decide which file is original.

## Limitations

Duplicate detection is based on embedded metadata date, not image pixels.

Two different photos can technically share the same metadata date, especially if a camera burst mode, export tool, or copied metadata caused matching timestamps.

File size and width/height are shown to help you decide, but the app cannot guarantee which image is the original.

Moving files to Trash depends on the operating system and desktop environment. The app uses Send2Trash first and may fall back to common Linux desktop trash commands when available.

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
