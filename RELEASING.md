# Releasing LectureWeave+

GitHub Actions builds the Windows installer and portable ZIP whenever a version
tag is pushed. No GitHub CLI installation is required on the development PC.

## Publish an update

1. Update `APP_VERSION` in `lecture_transcript_gui_qt.py`.
2. Commit and push the completed changes to `main`.
3. Create and push a matching tag:

   ```powershell
   git tag v0.3.0
   git push origin v0.3.0
   ```

4. Open the repository's **Actions** page and wait for **Build Windows
   release** to finish.

The workflow creates or updates the corresponding GitHub release with:

- `LectureWeavePlus-Setup.exe`
- `LectureWeavePlus-Portable.zip`
- `SHA256SUMS.txt`

The README download button always points to the installer attached to the
latest release. Installed copies use the same GitHub release to check whether
a newer version exists, verify the installer against GitHub's published SHA-256
asset digest, and run the in-place upgrade.

## Manual release build

The workflow can also be started from GitHub's Actions page using **Run
workflow**. Enter the version without the `v` prefix. A normal tagged release is
preferred because the tag permanently identifies the source used to build the
installer.

## Version rule

Use increasing semantic versions such as `0.2.0`, `0.2.1` and `0.3.0`. The app
compares the installed `APP_VERSION` with the release tag, so they must match.

