My Pics for Windows and Mac
===========================

OPTION A: Build it on GitHub (nothing to install on your computer)
 1. Make a free account at github.com, click New repository, name it my-pics
    and choose Public (Public is what lets other people download it).
 2. On the new repository page click "uploading an existing file", drag in
    everything from inside this folder (include the .github folder), then click
    Commit changes.
    If GitHub will not take the .github folder: Add file > Create new file, type
    .github/workflows/build.yml as the name, and paste in the contents of that file.
 3. Click the Actions tab. The build starts by itself and takes about 5 minutes.
    (If it asks, click "I understand my workflows, go ahead and enable them".)
 4. When it turns green, go back to the repository's main page and click
    Releases on the right. The installer and the portable app are there.
 5. Share this link (swap in your own username):
      https://github.com/YOUR-USERNAME/my-pics/releases/latest

 Publishing a new version later: change "version" in package.json (for example
 to 1.0.1) and commit. GitHub builds it and posts it automatically. You can also
 start a build by hand from the Actions tab with Run workflow.

OPTION B: Build it on your own PC
 1. Install Node.js (LTS) from https://nodejs.org
 2. Double-click build-windows.bat. The files appear in the "dist" folder.
 To try it without building: run  npm install  then  npm start

What you get (Windows)
 My-Pics-Setup-X.Y.Z.exe      installs My Pics like a normal program
 My-Pics-Portable-X.Y.Z.exe   runs without installing

Mac
 - The same GitHub build also makes Mac files. Pick the one for your Mac:
      My-Pics-Mac-X.Y.Z-arm64.dmg   Macs with an Apple chip (M1 or newer)
      My-Pics-Mac-X.Y.Z-x64.dmg     Intel Macs
   (Apple menu > About This Mac shows which chip you have.)
 - Open the .dmg and drag My Pics into Applications.
 - The first time, macOS may block the app because it is not notarized. Open System
   Settings > Privacy & Security, scroll down and click Open Anyway. If the Mac says
   the app is "damaged", open Terminal and run:
      xattr -cr "/Applications/My Pics.app"
 - Mac files can only be built on GitHub (or on a Mac with  npm run dist:mac ).

Automatic updates
 - Installed copies check for a new version every time My Pics starts (and every 4 hours).
 - Windows installer: the update downloads in the background. A card appears with
   "Restart and update". It also installs by itself the next time the app is closed.
 - Windows portable app and Mac: a card says a new version is available and the
   Download button opens your Releases page. (Mac cannot update silently without a
   paid Apple developer certificate.)
 - "Check for updates" at the bottom of the left panel checks right away.
 - To send an update: raise "version" in package.json (for example 1.6.0 to 1.6.1),
   then commit. GitHub builds it and posts it to Releases. If the number is not
   raised, installed copies will not see it as new.
 - Copies installed from an older version (before 1.6.0) do not have the updater yet.
   They need to install 1.6.0 manually one time. After that, updates are automatic.

Watermark
 - Click Watermark settings to use your own logo (PNG, JPEG or WebP) or your own text.
   Choose opacity, size and position and see it on your photo as you go.
   Your choices are remembered on this computer.

Notes
 - The app is not code-signed, so Windows may show "Windows protected your PC".
   Click More info, then Run anyway.
 - Save all (clean + watermarked) and Save all as preview ask you for a folder,
   then write numbered files into it (1.jpg, 2.jpg, ...).
 - Opens JPEG, PNG and WebP photos.
