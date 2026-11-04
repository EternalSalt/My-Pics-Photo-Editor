My Pics v1 for Windows
======================

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

What you get
 My-Pics-Setup-1.0.0.exe      installs My Pics like a normal program
 My-Pics-Portable-1.0.0.exe   runs without installing

Notes
 - The app is not code-signed, so Windows may show "Windows protected your PC".
   Click More info, then Run anyway.
 - Save all (clean + watermarked) and Save all as preview ask you for a folder,
   then write numbered files into it (1.jpg, 2.jpg, ...).
 - Opens JPEG, PNG and WebP photos.
