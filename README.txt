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
 to 1.10.1) and commit. GitHub builds it and posts it automatically.

OPTION B: Build it on your own PC
 1. Install Node.js (LTS) from https://nodejs.org
 2. Double-click build-windows.bat. The files appear in the "dist" folder.
 To try it without building: run  npm install  then  npm start

What you get (the file names never include a version number)
 My-Pics-Setup.exe      installs My Pics like a normal program
 My-Pics-Portable.exe   runs without installing
 My-Pics-Mac-arm64.dmg  Macs with an Apple chip (M1 or newer)
 My-Pics-Mac-x64.dmg    Intel Macs

Automatic updates
 - My Pics checks for a new version when it starts (and every 4 hours).
 - If there is one, it downloads in the background. A card then offers
   "Restart and update". If you ignore the card, the update installs by itself
   the next time you close My Pics.
 - Windows installed copy: the update installs quietly, My Pics reopens, and the
   downloaded installer is deleted. Nothing piles up.
 - Windows portable copy: the .exe you run is replaced in place by the new
   version (same file, same place), then My Pics reopens.
 - Mac: the new disk image downloads and opens. Drag My Pics into Applications.
   (Mac cannot update silently without a paid Apple developer certificate.)
 - "Check for updates" at the bottom of the left panel checks right away.
 - To send an update: raise "version" in package.json and commit. If the number
   is not raised, installed copies will not see it as new.
 - If a check fails, the message says why. Common reasons: the repository is not
   public, or there is no internet connection. The card then offers a button that
   opens your Releases page instead.
 - Copies installed before version 1.10.0 update to it the old way, and from
   1.10.0 on everything above applies.

Saving
 - Save all asks where to save, then creates two folders in that place:
      Edits       your edited photos, numbered 1, 2, 3...
      Watermark   the same photos with your watermark
   "Save all as preview" creates a Preview folder the same way.
 - If those folders already contain photos, My Pics asks before replacing files.

Mac
 - Open the .dmg and drag My Pics into Applications.
 - The first time, macOS may block the app because it is not notarized. Open System
   Settings > Privacy & Security, scroll down and click Open Anyway. If the Mac says
   the app is "damaged", open Terminal and run:
      xattr -cr "/Applications/My Pics.app"
 - Mac files can only be built on GitHub (or on a Mac with  npm run dist:mac ).

Watermark
 - Click Export, then Watermark settings to use your own logo (PNG, JPEG or WebP)
   or your own text. Choose opacity, size and position and see it on your photo.
   Your choices are remembered on this computer.

Notes
 - The app is not code-signed, so Windows may show "Windows protected your PC".
   Click More info, then Run anyway.
 - Opens JPEG, PNG and WebP photos.
